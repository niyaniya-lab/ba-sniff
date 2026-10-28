// observe.cpp -- Stage 3 native observer for Blue Archive (Global), read-only.
//
// Injected into BlueArchive.exe, it hooks Newtonsoft.Json.JsonConvert
// .DeserializeObject(string, ...) and reads each already-decrypted JSON string
// as the client's own code deserializes it, then lets the original run normally.
//
// It reproduces the exact selection + filter of frida/agent/capture.ts:
//   - arm only string-first, compiled DeserializeObject overloads;
//   - keep strings >= 20 chars that contain "Protocol"/"protocol";
//   - dedup by (length + first 48 chars).
// Matches are written to observe.log (readable) and observe_raw.jsonl (full JSON,
// one per line) next to this DLL -- the raw file is what Stage 4 will feed to the
// existing capture.py pipeline.

#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <share.h>          // _wfsopen / _SH_DENYWR
#include "il2cpp_min.h"
#include "hook.h"

#define MAX_HOOKS 16

typedef void* (*fn4)(void*, void*, void*, void*);  // armed overloads: <=4 register args

static Il2CppApi       g_api;
static HMODULE         g_self;
static FILE*           g_log;
static FILE*           g_raw;
static CRITICAL_SECTION g_cs;
static fn4             g_tramp[MAX_HOOKS];
static int             g_armed;
static int             g_matches;

// Stage B: auto-fire the friend-list fetch so the id card arrives without
// manual navigation. g_ext_ok = the invoke/heap-walk API resolved.
static int             g_ext_ok;
static int             g_want_fire;   // latched once the login bundle is seen
static int             g_fire_done;
static CRITICAL_SECTION g_fire_cs;

// --- logging -------------------------------------------------------------
static void logf(const char* fmt, ...) {
    char line[1024];
    va_list ap; va_start(ap, fmt);
    _vsnprintf_s(line, sizeof line, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (g_log) { fputs(line, g_log); fputc('\n', g_log); fflush(g_log); }
    OutputDebugStringA(line); OutputDebugStringA("\n");
}

static void open_logs(void) {
    wchar_t path[MAX_PATH];
    if (!GetModuleFileNameW(g_self, path, MAX_PATH)) return;
    wchar_t* slash = wcsrchr(path, L'\\');
    if (slash) slash[1] = 0;
    wchar_t logp[MAX_PATH], rawp[MAX_PATH];
    wcscpy_s(logp, MAX_PATH, path); wcscat_s(logp, MAX_PATH, L"observe.log");
    wcscpy_s(rawp, MAX_PATH, path); wcscat_s(rawp, MAX_PATH, L"observe_raw.jsonl");
    // Open shared-read (deny only other writers) so the log can be tailed and
    // read while the observer is still running and holding the handle open.
    g_log = _wfsopen(logp, L"w", _SH_DENYWR);
    g_raw = _wfsopen(rawp, L"w", _SH_DENYWR);
}

// --- dedup (recent keys) -------------------------------------------------
#define DEDUP_N 1024
static char g_keys[DEDUP_N][64];
static int  g_keycount;
static int seen_or_add(const char* key) {   // returns 1 if already seen
    for (int i = 0; i < g_keycount; ++i)
        if (strcmp(g_keys[i], key) == 0) return 1;
    if (g_keycount < DEDUP_N) { strncpy_s(g_keys[g_keycount], 64, key, _TRUNCATE); g_keycount++; }
    return 0;
}

// --- Stage B: ask the game for the friend list (so the id card arrives) ---
// Mirrors capture_refresh.ts fireNow(): heap-scan for a live NetworkTaskManager,
// spin up a GameObject, AddComponent the FriendListNetworkTask, and Request it.
// Called from a detour, i.e. on the game's network thread -- the valid context
// for these Unity calls. Fires exactly once, retrying until the manager exists.
static int g_fire_attempts;
static void try_fire_friendlist(void) {
    EnterCriticalSection(&g_fire_cs);
    if (g_fire_done) { LeaveCriticalSection(&g_fire_cs); return; }
    // Log the first few attempts and then every 60th, so we can see where it
    // stops without flooding the log on the (silent) retry path.
    int diag = (++g_fire_attempts <= 3) || (g_fire_attempts % 60 == 0);

    const Il2CppImage* ba   = il2cpp_find_image(&g_api, "BlueArchive");
    const Il2CppImage* core = il2cpp_find_image(&g_api, "UnityEngine.CoreModule");
    if (diag) logf("[fire] attempt %d: ba=%p core=%p", g_fire_attempts, ba, core);
    if (!ba || !core) { LeaveCriticalSection(&g_fire_cs); return; }

    Il2CppClass* mgrClass = g_api.class_from_name(ba, "Assets._MX.Program.Scripts.Network", "NetworkTaskManager");
    if (diag) logf("[fire]   mgrClass=%p", mgrClass);
    if (!mgrClass) { logf("[fire] NetworkTaskManager class not found -- giving up."); g_fire_done = 1; LeaveCriticalSection(&g_fire_cs); return; }

    void* mgr = il2cpp_gc_choose_one(&g_api, mgrClass);
    if (diag) logf("[fire]   live mgr=%p (heap objects walked=%ld)", mgr, g_choose_seen);
    if (!mgr) { LeaveCriticalSection(&g_fire_cs); return; }   // not alive yet; retry on a later packet

    Il2CppClass* taskClass = il2cpp_find_class(&g_api, ba, "FriendListNetworkTask");
    if (!taskClass) taskClass = il2cpp_find_class(&g_api, ba, "FriendGetListNetworkTask");
    Il2CppClass* goClass = g_api.class_from_name(core, "UnityEngine", "GameObject");
    if (diag) logf("[fire]   taskClass=%p goClass=%p", taskClass, goClass);
    if (!taskClass || !goClass) { logf("[fire] task/GameObject class missing -- giving up."); g_fire_done = 1; LeaveCriticalSection(&g_fire_cs); return; }

    const MethodInfo* ctor    = g_api.class_get_method_from_name(goClass, ".ctor", 0);
    const MethodInfo* addComp = g_api.class_get_method_from_name(goClass, "AddComponent", 1);
    const MethodInfo* request = g_api.class_get_method_from_name(mgrClass, "Request", 2);
    if (!request) request = g_api.class_get_method_from_name(mgrClass, "Request", 1);
    if (diag) logf("[fire]   ctor=%p addComp=%p request=%p", ctor, addComp, request);
    if (!ctor || !addComp || !request) { logf("[fire] method missing (ctor=%p add=%p req=%p).", ctor, addComp, request); g_fire_done = 1; LeaveCriticalSection(&g_fire_cs); return; }

    void* exc = 0;
    void* go = g_api.object_new(goClass);
    g_api.runtime_invoke(ctor, go, 0, &exc);
    void* typeObj = g_api.type_get_object(g_api.class_get_type(taskClass));
    void* addArgs[1] = { typeObj };
    void* task = g_api.runtime_invoke(addComp, go, addArgs, &exc);   // GameObject.AddComponent(Type)
    void* reqArgs[2] = { task, 0 };
    g_api.runtime_invoke(request, mgr, reqArgs, &exc);               // NetworkTaskManager.Request(task, null)

    g_fire_done = 1;
    if (exc) logf("[fire] managed exception during fire (id card may not arrive).");
    else     logf("[fire] FriendListNetworkTask requested -- id card should arrive shortly.");
    LeaveCriticalSection(&g_fire_cs);
}

// --- the observation (shared by every detour) ----------------------------
static void observe(void* arg0) {
    char buf[1 << 16];   // 64 KB, per-call (thread-safe); JSON packets fit comfortably
    int len = il2cpp_str_utf8(&g_api, (Il2CppString*)arg0, buf, sizeof buf);
    if (len < 20) return;
    if (!strstr(buf, "Protocol") && !strstr(buf, "protocol")) return;

    char key[64];
    _snprintf_s(key, sizeof key, _TRUNCATE, "%d:%.48s", len, buf);

    EnterCriticalSection(&g_cs);
    if (!seen_or_add(key)) {
        g_matches++;
        if (g_log) { fprintf(g_log, "[observe] MATCH #%d len=%d first160=%.160s\n", g_matches, len, buf); fflush(g_log); }
        if (g_raw) { fputs(buf, g_raw); fputc('\n', g_raw); fflush(g_raw); }
    }
    LeaveCriticalSection(&g_cs);

    // Latch on the login bundle, then attempt the fire on this and any later
    // packet until it succeeds (heartbeats keep arriving), so a not-yet-live
    // NetworkTaskManager on the first try doesn't make us miss.
    if (g_ext_ok && strstr(buf, "CharacterListResponse")) g_want_fire = 1;
    if (g_ext_ok && g_want_fire && !g_fire_done) try_fire_friendlist();
}

// --- N distinct detours, each bound to its own trampoline slot ------------
// A shared detour couldn't tell which original to continue to; one per slot can.
// Each: observe arg0 (the System.String), then continue via its trampoline,
// forwarding all four register args and the return value untouched.
#define DETOUR(k) static void* detour##k(void* a, void* b, void* c, void* d) { \
    observe(a); return g_tramp[k] ? g_tramp[k](a, b, c, d) : 0; }
DETOUR(0)  DETOUR(1)  DETOUR(2)  DETOUR(3)  DETOUR(4)  DETOUR(5)  DETOUR(6)  DETOUR(7)
DETOUR(8)  DETOUR(9)  DETOUR(10) DETOUR(11) DETOUR(12) DETOUR(13) DETOUR(14) DETOUR(15)
static const void* g_detours[MAX_HOOKS] = {
    detour0, detour1, detour2, detour3, detour4, detour5, detour6, detour7,
    detour8, detour9, detour10, detour11, detour12, detour13, detour14, detour15,
};

// --- arm the hooks -------------------------------------------------------
static int arm(void) {
    const Il2CppImage* nj = il2cpp_find_image(&g_api, "Newtonsoft.Json");
    if (!nj) { logf("[observe] FATAL: Newtonsoft.Json not found."); return 0; }
    Il2CppClass* k = g_api.class_from_name(nj, "Newtonsoft.Json", "JsonConvert");
    if (!k) { logf("[observe] FATAL: JsonConvert not found."); return 0; }

    int n = 0;
    void* it = 0;
    const MethodInfo* m;
    while ((m = g_api.class_get_methods(k, &it)) != 0) {
        const char* mn = g_api.method_get_name(m);
        if (!mn || strcmp(mn, "DeserializeObject") != 0) continue;   // only the JSON entry
        if (g_api.method_get_param_count(m) < 1) continue;

        char* p0 = g_api.type_get_name(g_api.method_get_param(m, 0));
        int is_string = (p0 && strcmp(p0, "System.String") == 0);
        if (p0 && g_api.free_) g_api.free_(p0);
        if (!is_string) continue;

        void* ptr = il2cpp_method_ptr(m);   // null for generic/uncompiled -> skip
        if (!ptr || n >= MAX_HOOKS) continue;

        void* tr = install_hook(ptr, g_detours[n]);
        if (tr) { g_tramp[n] = (fn4)tr; logf("[observe] armed DeserializeObject #%d ptr=%p", n, ptr); n++; }
        else    { logf("[observe] install_hook FAILED for ptr=%p", ptr); }
    }
    logf("[observe] armed on %d method(s).", n);
    return n;
}

static DWORD WINAPI observer_thread(LPVOID) {
    open_logs();
    logf("[observe] starting.");

    HMODULE game = il2cpp_wait(120000);
    if (!game) { logf("[observe] FATAL: runtime not ready within 120s."); return 1; }

    const char* missing = 0;
    if (!il2cpp_init(&g_api, game, &missing)) {
        logf("[observe] FATAL: missing export %s", missing ? missing : "(?)");
        return 2;
    }

    // Attach OUR thread so metadata reads during arming are safe. The detours
    // themselves run on the game's own (already-attached) threads.
    g_api.thread_attach(g_api.domain_get());

    // Resolve the extended API for auto-firing the friend-list fetch (Stage B).
    g_ext_ok = il2cpp_init_ext(&g_api);
    InitializeCriticalSection(&g_fire_cs);
    logf("[observe] id-card auto-fetch: %s",
         g_ext_ok ? "on (fires FriendListNetworkTask after login)"
                  : "off (invoke API missing; open Friends manually)");

    // Newtonsoft loads around login; poll so injection timing is forgiving.
    for (int w = 0; w < 120000 && !il2cpp_find_image(&g_api, "Newtonsoft.Json"); w += 500)
        Sleep(500);

    InitializeCriticalSection(&g_cs);
    g_armed = arm();
    if (g_armed == 0) logf("[observe] nothing armed -- see messages above.");
    else logf("[observe] live. Watching for JSON packets...");
    return 0;
}

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = hModule;
        DisableThreadLibraryCalls(hModule);
        HANDLE t = CreateThread(0, 0, observer_thread, 0, 0, 0);
        if (t) CloseHandle(t);
    }
    return TRUE;
}
