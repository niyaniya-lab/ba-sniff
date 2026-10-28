// recon.cpp -- read-only IL2CPP recon probe for Blue Archive (Global).
//
// PURPOSE
//   Answer one question before we commit to writing a trampoline hooker:
//   "Can a native DLL, injected into BlueArchive.exe, reach Unity's IL2CPP
//    domain, locate the Newtonsoft.Json Deserialize(string) methods, read each
//    method's native address, and convert a managed System.String to text?"
//   If yes -> Option A (observe the managed deserializer from native) is
//   reachable and we know the real symbol names + addresses to hook.
//   If no  -> we fall back / rethink, having written zero hooking code.
//
//   This probe is READ-ONLY. It never patches a byte, never installs a hook,
//   never calls a game function. It only reads IL2CPP metadata and round-trips
//   one string we create ourselves. It cannot corrupt the process.
//
// WHY GameAssembly.dll
//   Blue Archive is a Unity IL2CPP game. Unity compiles the C# (managed) code
//   to C++ and ships it as GameAssembly.dll, which EXPORTS the "il2cpp_*" C API
//   we use here. So we don't need Frida's metadata engine -- we just
//   GetProcAddress the functions straight out of that DLL.
//
// OUTPUT
//   Everything is written to recon.log, placed NEXT TO this DLL (a DLL has no
//   console). The Python injector prints that path so you can read it.

#include <windows.h>
#include <tlhelp32.h>
#include <cstdint>
#include <cstdio>
#include <cstring>

// ---------------------------------------------------------------------------
// IL2CPP opaque types.
//   We never dereference these ourselves (except MethodInfo, carefully, below),
//   so treating them as opaque void-ish pointers is enough and keeps us
//   independent of the exact IL2CPP version's struct layouts.
// ---------------------------------------------------------------------------
typedef void  Il2CppDomain;
typedef void  Il2CppThread;
typedef void  Il2CppAssembly;
typedef void  Il2CppImage;
typedef void  Il2CppClass;
typedef void  Il2CppType;
typedef void  MethodInfo;      // NOTE: we read ONLY its first field (see below)
typedef void  Il2CppString;
typedef uint16_t Il2CppChar;   // IL2CPP strings are UTF-16

// ---------------------------------------------------------------------------
// Function-pointer typedefs for the il2cpp_* exports we need.
//   Signatures match Unity's il2cpp public C API. We resolve each by name from
//   GameAssembly.dll at runtime; any that fail to resolve are reported.
// ---------------------------------------------------------------------------
typedef Il2CppDomain*   (*fn_domain_get)(void);
typedef Il2CppThread*   (*fn_thread_attach)(Il2CppDomain*);
typedef const Il2CppAssembly** (*fn_domain_get_assemblies)(const Il2CppDomain*, size_t*);
typedef const Il2CppImage* (*fn_assembly_get_image)(const Il2CppAssembly*);
typedef const char*     (*fn_image_get_name)(const Il2CppImage*);
typedef Il2CppClass*    (*fn_class_from_name)(const Il2CppImage*, const char*, const char*);
typedef const MethodInfo* (*fn_class_get_methods)(Il2CppClass*, void**);
typedef const char*     (*fn_method_get_name)(const MethodInfo*);
typedef uint32_t        (*fn_method_get_param_count)(const MethodInfo*);
typedef const Il2CppType* (*fn_method_get_param)(const MethodInfo*, uint32_t);
typedef char*           (*fn_type_get_name)(const Il2CppType*);
typedef Il2CppString*   (*fn_string_new)(const char*);
typedef Il2CppChar*     (*fn_string_chars)(Il2CppString*);
typedef int32_t         (*fn_string_length)(Il2CppString*);
typedef void            (*fn_free)(void*);

// Resolved once we find GameAssembly.dll.
static fn_domain_get           il2cpp_domain_get           = nullptr;
static fn_thread_attach        il2cpp_thread_attach        = nullptr;
static fn_domain_get_assemblies il2cpp_domain_get_assemblies = nullptr;
static fn_assembly_get_image   il2cpp_assembly_get_image   = nullptr;
static fn_image_get_name       il2cpp_image_get_name       = nullptr;
static fn_class_from_name      il2cpp_class_from_name      = nullptr;
static fn_class_get_methods    il2cpp_class_get_methods    = nullptr;
static fn_method_get_name      il2cpp_method_get_name      = nullptr;
static fn_method_get_param_count il2cpp_method_get_param_count = nullptr;
static fn_method_get_param     il2cpp_method_get_param     = nullptr;
static fn_type_get_name        il2cpp_type_get_name        = nullptr;
static fn_string_new           il2cpp_string_new           = nullptr;
static fn_string_chars         il2cpp_string_chars         = nullptr;
static fn_string_length        il2cpp_string_length        = nullptr;
static fn_free                 il2cpp_free                 = nullptr;

static HMODULE   g_self       = nullptr;   // this DLL, for locating recon.log
static HMODULE   g_gameasm    = nullptr;   // GameAssembly.dll
static FILE*     g_log        = nullptr;

// ---------------------------------------------------------------------------
// Logging: write to recon.log next to this DLL, and also mirror to the
// debugger (DebugView) so you can watch live if you want.
// ---------------------------------------------------------------------------
static void logf(const char* fmt, ...) {
    char line[2048];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(line, sizeof(line), _TRUNCATE, fmt, ap);
    va_end(ap);
    if (g_log) { fputs(line, g_log); fputc('\n', g_log); fflush(g_log); }
    OutputDebugStringA(line);
    OutputDebugStringA("\n");
}

static void open_log() {
    // Place recon.log next to recon.dll (GetModuleFileName gives the DLL path).
    wchar_t path[MAX_PATH];
    DWORD n = GetModuleFileNameW(g_self, path, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { return; }
    // Strip "recon.dll", append "recon.log".
    wchar_t* slash = wcsrchr(path, L'\\');
    if (slash) { slash[1] = 0; }
    wcscat_s(path, MAX_PATH, L"recon.log");
    _wfopen_s(&g_log, path, L"w");
}

// Resolve one export; log and remember if it is missing.
template <typename T>
static bool resolve(T& out, const char* name) {
    out = reinterpret_cast<T>(GetProcAddress(g_gameasm, name));
    if (!out) { logf("  [MISSING] %s", name); return false; }
    return true;
}

// Wait (with a timeout) until GameAssembly.dll is loaded AND the il2cpp domain
// is initialized. The game's managed runtime comes up a moment after the
// process starts, so an injected DLL must poll rather than assume it's ready.
static bool wait_for_runtime(int timeout_ms) {
    const int step = 250;
    for (int waited = 0; waited < timeout_ms; waited += step) {
        g_gameasm = GetModuleHandleA("GameAssembly.dll");
        if (g_gameasm) {
            fn_domain_get dg = reinterpret_cast<fn_domain_get>(
                GetProcAddress(g_gameasm, "il2cpp_domain_get"));
            if (dg && dg() != nullptr) { return true; }
        }
        Sleep(step);
    }
    return false;
}

// ---------------------------------------------------------------------------
// The probe itself. Runs on its own thread (never in DllMain -- doing real
// work under the loader lock can deadlock).
// ---------------------------------------------------------------------------
static DWORD WINAPI recon_thread(LPVOID) {
    open_log();
    logf("[recon] probe started.");

    if (!wait_for_runtime(120000)) {
        logf("[recon] FATAL: GameAssembly.dll / il2cpp domain not ready within 120s.");
        if (g_log) fclose(g_log);
        return 1;
    }
    logf("[recon] GameAssembly.dll base = %p", (void*)g_gameasm);

    // Resolve the whole il2cpp API surface we need. Report the string exports
    // specifically, since the handoff doc guessed il2cpp_string_toC (which
    // likely does not exist) -- recon settles what the real name is.
    bool ok = true;
    ok &= resolve(il2cpp_domain_get,            "il2cpp_domain_get");
    ok &= resolve(il2cpp_thread_attach,         "il2cpp_thread_attach");
    ok &= resolve(il2cpp_domain_get_assemblies, "il2cpp_domain_get_assemblies");
    ok &= resolve(il2cpp_assembly_get_image,    "il2cpp_assembly_get_image");
    ok &= resolve(il2cpp_image_get_name,        "il2cpp_image_get_name");
    ok &= resolve(il2cpp_class_from_name,       "il2cpp_class_from_name");
    ok &= resolve(il2cpp_class_get_methods,     "il2cpp_class_get_methods");
    ok &= resolve(il2cpp_method_get_name,       "il2cpp_method_get_name");
    ok &= resolve(il2cpp_method_get_param_count,"il2cpp_method_get_param_count");
    ok &= resolve(il2cpp_method_get_param,      "il2cpp_method_get_param");
    ok &= resolve(il2cpp_type_get_name,         "il2cpp_type_get_name");
    ok &= resolve(il2cpp_string_new,            "il2cpp_string_new");
    ok &= resolve(il2cpp_string_length,         "il2cpp_string_length");
    // String-to-native: try both candidates, report which resolves.
    bool has_chars = resolve(il2cpp_string_chars, "il2cpp_string_chars");
    void* toC = GetProcAddress(g_gameasm, "il2cpp_string_toC");
    logf("[recon] string export: il2cpp_string_chars=%s  il2cpp_string_toC=%s",
         has_chars ? "PRESENT" : "missing", toC ? "PRESENT" : "missing");
    resolve(il2cpp_free, "il2cpp_free"); // optional; only needed to free type names

    if (!ok) {
        logf("[recon] FATAL: core il2cpp exports missing -> Option A unreachable.");
        if (g_log) fclose(g_log);
        return 2;
    }

    // Our thread must be attached to the managed runtime before we touch
    // managed objects (string allocation especially).
    Il2CppDomain* domain = il2cpp_domain_get();
    il2cpp_thread_attach(domain);
    logf("[recon] domain=%p, thread attached.", domain);

    // Find the Newtonsoft.Json assembly. It loads with the network layer (around
    // login), so we POLL for it rather than assume it's present -- this makes
    // injection timing forgiving: inject any time and the probe fires when the
    // assembly appears. (Re-injecting would NOT help: DllMain runs once per load.)
    // On the final attempt we also log every assembly name, so a failure still
    // shows the exact spellings that ARE loaded.
    const Il2CppImage* newtonsoft = nullptr;
    const int nt_timeout_ms = 120000, nt_step = 500;
    for (int waited = 0; waited <= nt_timeout_ms && !newtonsoft; waited += nt_step) {
        size_t count = 0;
        const Il2CppAssembly** asms = il2cpp_domain_get_assemblies(domain, &count);
        bool last = (waited + nt_step > nt_timeout_ms);
        for (size_t i = 0; i < count; ++i) {
            const Il2CppImage* img = il2cpp_assembly_get_image(asms[i]);
            const char* name = img ? il2cpp_image_get_name(img) : "(null)";
            if (name && strstr(name, "Newtonsoft.Json")) {
                logf("[recon] found Newtonsoft after ~%dms: assembly[%zu] = %s", waited, i, name);
                newtonsoft = img;
                break;
            }
            if (last) logf("[recon]   assembly[%zu] = %s", i, name);
        }
        if (!newtonsoft && !last) Sleep(nt_step);
    }
    if (!newtonsoft) {
        logf("[recon] FATAL: Newtonsoft.Json not loaded within 120s. Try logging in,");
        logf("        then RESTART the game before re-injecting (DllMain fires once).");
        if (g_log) fclose(g_log);
        return 3;
    }

    // For JsonConvert and JsonSerializer, list every Deserialize-named method
    // whose first parameter is System.String, printing its native address.
    // That address (MethodInfo's first field, methodPointer) is exactly what
    // Frida reports as method.virtualAddress -- the byte we would hook.
    const char* classes[] = { "JsonConvert", "JsonSerializer" };
    int armable = 0;
    for (const char* cls : classes) {
        Il2CppClass* klass = il2cpp_class_from_name(newtonsoft, "Newtonsoft.Json", cls);
        if (!klass) { logf("[recon] class Newtonsoft.Json.%s NOT FOUND", cls); continue; }
        logf("[recon] --- Newtonsoft.Json.%s ---", cls);

        void* iter = nullptr;
        const MethodInfo* m;
        while ((m = il2cpp_class_get_methods(klass, &iter)) != nullptr) {
            const char* mname = il2cpp_method_get_name(m);
            if (!mname || !strstr(mname, "Deserialize")) continue;

            uint32_t pc = il2cpp_method_get_param_count(m);
            const char* p0 = "(none)";
            char* p0owned = nullptr;
            if (pc >= 1) {
                const Il2CppType* t = il2cpp_method_get_param(m, 0);
                p0owned = il2cpp_type_get_name(t); // heap string, free below
                p0 = p0owned ? p0owned : "(null)";
            }

            // MethodInfo's FIRST field is `Il2CppMethodPointer methodPointer`.
            // This layout is stable across IL2CPP versions, so reading the
            // first pointer-sized field gives the compiled native address.
            // It is null for generic/uncompiled methods -- exactly the ones
            // the capture.ts filter skips.
            void* method_ptr = *reinterpret_cast<void* const*>(m);
            void* rva = method_ptr
                ? (void*)((uint8_t*)method_ptr - (uint8_t*)g_gameasm)
                : nullptr;

            bool string_first = (pc >= 1 && p0owned && strcmp(p0owned, "System.String") == 0);
            logf("[recon]   %-28s params=%u  p0=%-24s  ptr=%p  rva=%p  %s",
                 mname, pc, p0, method_ptr, rva,
                 (string_first && method_ptr) ? "<== ARMABLE" : "");
            if (string_first && method_ptr) armable++;

            if (p0owned && il2cpp_free) il2cpp_free(p0owned);
        }
    }
    logf("[recon] armable Deserialize(string) methods (non-null ptr): %d", armable);

    // Prove the managed-string -> native-text path works, using a string we
    // create ourselves (so no game data is touched). This is the exact
    // conversion the real observer will do on the deserializer's argument.
    if (il2cpp_string_new && il2cpp_string_chars && il2cpp_string_length) {
        const char* sample = "recon-string-roundtrip-\xE3\x83\x86\xE3\x82\xB9\xE3\x83\x88"; // ascii + UTF-8 "テスト"
        Il2CppString* s = il2cpp_string_new(sample);
        int32_t len = il2cpp_string_length(s);
        Il2CppChar* wc = il2cpp_string_chars(s); // UTF-16 buffer
        char utf8[512];
        int got = WideCharToMultiByte(CP_UTF8, 0, (wchar_t*)wc, len, utf8, sizeof(utf8) - 1, nullptr, nullptr);
        utf8[got > 0 ? got : 0] = 0;
        logf("[recon] string round-trip: len=%d, utf8='%s'", len, utf8);
        logf("[recon] string path %s", strcmp(utf8, sample) == 0 ? "OK (exact match)" : "MISMATCH (inspect)");
    } else {
        logf("[recon] string round-trip SKIPPED (a string export was missing).");
    }

    logf("[recon] done. Verdict: %s",
         armable > 0 ? "Option A REACHABLE -- greenlight the trampoline."
                     : "no armable methods -- inspect the method list above.");
    if (g_log) fclose(g_log);
    return 0;
}

// ---------------------------------------------------------------------------
// DllMain: do the absolute minimum here (loader lock). Spawn the worker thread
// and return immediately.
// ---------------------------------------------------------------------------
BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = hModule;
        DisableThreadLibraryCalls(hModule);
        HANDLE t = CreateThread(nullptr, 0, recon_thread, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
