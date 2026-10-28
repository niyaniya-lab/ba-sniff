// list.cpp -- runtime IL2CPP browser. Inject it and it walks the live type
// system, writing matching classes and their method signatures to list.log.
// This is the "discover what to call" tool from DISCOVERY.md, using only the
// il2cpp_* API GameAssembly.dll exports -- no external dumper.
//
// Query: put a substring in native/list_query.txt (matched case-insensitively
// against "Namespace.ClassName"). Empty/missing = dump EVERY class (large).
// Examples:  NetworkTask   |   JsonConvert   |   NetworkTaskManager   |   GameObject
//
// Output line format:
//   Namespace.ClassName
//     [rva=0x00XXXXXX] ReturnType MethodName(ParamType, ...)
//   (rva=0x0 means a generic/uncompiled method -- not directly callable/hookable.)

#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <ctype.h>
#include <share.h>
#include "il2cpp_min.h"

static Il2CppApi g_api;
static HMODULE   g_self, g_game;
static FILE*     g_out;

// A couple of extras not in il2cpp_min.h's core/ext sets, resolved locally.
typedef const char*       (*fn_ns)(Il2CppClass*);
typedef const Il2CppType* (*fn_rettype)(const MethodInfo*);
static fn_ns      get_ns;
static fn_rettype get_ret;

static void outln(const char* s) {
    if (g_out) { fputs(s, g_out); fputc('\n', g_out); fflush(g_out); }
    OutputDebugStringA(s); OutputDebugStringA("\n");
}

static int ci_contains(const char* hay, const char* needle) {
    if (!needle || !*needle) return 1;
    size_t nl = strlen(needle);
    for (const char* p = hay; *p; ++p) {
        size_t i = 0;
        while (i < nl && p[i] && tolower((unsigned char)p[i]) == tolower((unsigned char)needle[i])) i++;
        if (i == nl) return 1;
    }
    return 0;
}

static void near_self(const wchar_t* leaf, wchar_t* out) {
    GetModuleFileNameW(g_self, out, MAX_PATH);
    wchar_t* s = wcsrchr(out, L'\\'); if (s) s[1] = 0;
    wcscat_s(out, MAX_PATH, leaf);
}

static void read_query(char* q, int n) {
    q[0] = 0;
    wchar_t path[MAX_PATH]; near_self(L"list_query.txt", path);
    FILE* f = 0; _wfopen_s(&f, path, L"r");
    if (!f) return;
    if (fgets(q, n, f)) { q[strcspn(q, "\r\n")] = 0; }
    fclose(f);
}

static DWORD WINAPI list_thread(LPVOID) {
    wchar_t logp[MAX_PATH]; near_self(L"list.log", logp);
    g_out = _wfsopen(logp, L"w", _SH_DENYWR);

    g_game = il2cpp_wait(120000);
    if (!g_game) { outln("[list] FATAL: runtime not ready"); if (g_out) fclose(g_out); return 1; }
    const char* miss = 0;
    if (!il2cpp_init(&g_api, g_game, &miss) || !il2cpp_init_ext(&g_api)) {
        char b[128]; _snprintf_s(b, sizeof b, _TRUNCATE, "[list] FATAL: missing export %s", miss ? miss : "(ext)");
        outln(b); if (g_out) fclose(g_out); return 2;
    }
    g_api.thread_attach(g_api.domain_get());
    get_ns  = (fn_ns)GetProcAddress(g_game, "il2cpp_class_get_namespace");
    get_ret = (fn_rettype)GetProcAddress(g_game, "il2cpp_method_get_return_type");

    char query[256]; read_query(query, sizeof query);
    char hdr[320]; _snprintf_s(hdr, sizeof hdr, _TRUNCATE,
        "[list] query=%s  (empty = dump all). GameAssembly=%p", query[0] ? query : "(all)", (void*)g_game);
    outln(hdr);

    size_t na = 0;
    const Il2CppAssembly** as = g_api.domain_get_assemblies(g_api.domain_get(), &na);
    int total = 0, matched = 0;

    for (size_t ai = 0; ai < na; ++ai) {
        const Il2CppImage* img = g_api.assembly_get_image(as[ai]);
        if (!img) continue;
        size_t cn = g_api.image_get_class_count(img);
        for (size_t ci = 0; ci < cn; ++ci) {
            Il2CppClass* k = g_api.image_get_class(img, ci);
            if (!k) continue;
            const char* name = g_api.class_get_name(k);
            const char* ns   = get_ns ? get_ns(k) : "";
            char full[512];
            _snprintf_s(full, sizeof full, _TRUNCATE, "%s%s%s", (ns && *ns) ? ns : "", (ns && *ns) ? "." : "", name ? name : "?");
            total++;
            if (!ci_contains(full, query)) continue;
            matched++;
            outln(full);

            void* it = 0;
            const MethodInfo* m;
            while ((m = g_api.class_get_methods(k, &it)) != 0) {
                const char* mn = g_api.method_get_name(m);
                uint32_t pc = g_api.method_get_param_count(m);
                void* mp = il2cpp_method_ptr(m);
                uint64_t rva = mp ? (uint64_t)((uint8_t*)mp - (uint8_t*)g_game) : 0;

                char rt[128] = "?";
                if (get_ret) { char* r = g_api.type_get_name((const Il2CppType*)get_ret(m)); if (r) { strncpy_s(rt, sizeof rt, r, _TRUNCATE); if (g_api.free_) g_api.free_(r); } }

                char params[640]; params[0] = 0; int off = 0;
                for (uint32_t j = 0; j < pc; ++j) {
                    char* pn = g_api.type_get_name(g_api.method_get_param(m, j));
                    off += _snprintf_s(params + off, sizeof params - off, _TRUNCATE, "%s%s", j ? ", " : "", pn ? pn : "?");
                    if (pn && g_api.free_) g_api.free_(pn);
                    if (off >= (int)sizeof params - 8) break;
                }
                char line[1024];
                _snprintf_s(line, sizeof line, _TRUNCATE, "    [rva=0x%llX] %s %s(%s)", (unsigned long long)rva, rt, mn ? mn : "?", params);
                outln(line);
            }
        }
    }

    char done[160];
    _snprintf_s(done, sizeof done, _TRUNCATE, "[list] done. %d classes scanned, %d matched.", total, matched);
    outln(done);
    if (g_out) fclose(g_out);
    return 0;
}

BOOL APIENTRY DllMain(HMODULE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = h;
        DisableThreadLibraryCalls(h);
        HANDLE t = CreateThread(0, 0, list_thread, 0, 0, 0);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
