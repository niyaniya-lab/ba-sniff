// il2cpp_min.h -- minimal IL2CPP interop.
//
// Blue Archive is a Unity IL2CPP game: its C# is compiled to C++ and shipped as
// GameAssembly.dll, which exports a small "il2cpp_*" C API. We resolve just the
// handful of functions the observer needs (no Frida, no metadata engine) and
// wrap the couple of operations we perform (find an assembly, read a managed
// string). Everything here is read-only reflection over the running runtime.

#pragma once
#include <windows.h>
#include <stdint.h>
#include <string.h>

typedef void Il2CppDomain;   typedef void Il2CppThread;   typedef void Il2CppAssembly;
typedef void Il2CppImage;    typedef void Il2CppClass;    typedef void Il2CppType;
typedef void MethodInfo;     typedef void Il2CppString;   typedef uint16_t Il2CppChar;

typedef Il2CppDomain*          (*fn_domain_get)(void);
typedef Il2CppThread*          (*fn_thread_attach)(Il2CppDomain*);
typedef const Il2CppAssembly** (*fn_domain_get_assemblies)(const Il2CppDomain*, size_t*);
typedef const Il2CppImage*     (*fn_assembly_get_image)(const Il2CppAssembly*);
typedef const char*            (*fn_image_get_name)(const Il2CppImage*);
typedef Il2CppClass*           (*fn_class_from_name)(const Il2CppImage*, const char*, const char*);
typedef const MethodInfo*      (*fn_class_get_methods)(Il2CppClass*, void**);
typedef const char*            (*fn_method_get_name)(const MethodInfo*);
typedef uint32_t               (*fn_method_get_param_count)(const MethodInfo*);
typedef const Il2CppType*      (*fn_method_get_param)(const MethodInfo*, uint32_t);
typedef char*                  (*fn_type_get_name)(const Il2CppType*);
typedef Il2CppChar*            (*fn_string_chars)(Il2CppString*);
typedef int32_t                (*fn_string_length)(Il2CppString*);
typedef void                   (*fn_free)(void*);

// Extended API used only for actively firing a NetworkTask (Stage B). These let
// us invoke managed methods and heap-scan for a live object -- the same moves
// frida-il2cpp-bridge makes for gc.choose + Method.invoke.
typedef void*                  (*fn_object_new)(Il2CppClass*);
typedef void*                  (*fn_runtime_invoke)(const MethodInfo*, void* obj, void** params, void** exc);
typedef const Il2CppType*      (*fn_class_get_type)(Il2CppClass*);
typedef void*                  (*fn_type_get_object)(const Il2CppType*);
typedef const MethodInfo*      (*fn_class_get_method_from_name)(Il2CppClass*, const char*, int);
typedef Il2CppClass*           (*fn_image_get_class)(const Il2CppImage*, size_t);
typedef size_t                 (*fn_image_get_class_count)(const Il2CppImage*);
typedef const char*            (*fn_class_get_name)(Il2CppClass*);
typedef Il2CppClass*           (*fn_object_get_class)(void*);
typedef void                   (*fn_gc_void)(void);
typedef void                   (*fn_gc_foreach_heap)(void*, void*);
typedef int                    (*fn_class_is_subclass_of)(Il2CppClass*, Il2CppClass*, int);

typedef struct {
    HMODULE game;
    fn_domain_get            domain_get;
    fn_thread_attach         thread_attach;
    fn_domain_get_assemblies domain_get_assemblies;
    fn_assembly_get_image    assembly_get_image;
    fn_image_get_name        image_get_name;
    fn_class_from_name       class_from_name;
    fn_class_get_methods     class_get_methods;
    fn_method_get_name       method_get_name;
    fn_method_get_param_count method_get_param_count;
    fn_method_get_param      method_get_param;
    fn_type_get_name         type_get_name;
    fn_string_chars          string_chars;
    fn_string_length         string_length;
    fn_free                  free_;
    // extended (Stage B: fire a NetworkTask). Null unless il2cpp_init_ext ran.
    fn_object_new                object_new;
    fn_runtime_invoke            runtime_invoke;
    fn_class_get_type            class_get_type;
    fn_type_get_object           type_get_object;
    fn_class_get_method_from_name class_get_method_from_name;
    fn_image_get_class           image_get_class;
    fn_image_get_class_count     image_get_class_count;
    fn_class_get_name            class_get_name;
    fn_object_get_class          object_get_class;
    fn_gc_void                   gc_disable;
    fn_gc_void                   gc_enable;
    fn_gc_foreach_heap           gc_foreach_heap;
    fn_class_is_subclass_of      class_is_subclass_of;
} Il2CppApi;

// Wait until GameAssembly.dll is loaded and the managed domain is initialized.
// Returns the module handle, or NULL on timeout.
static HMODULE il2cpp_wait(int timeout_ms) {
    for (int w = 0; w < timeout_ms; w += 250) {
        HMODULE g = GetModuleHandleA("GameAssembly.dll");
        if (g) {
            fn_domain_get dg = (fn_domain_get)GetProcAddress(g, "il2cpp_domain_get");
            if (dg && dg()) return g;
        }
        Sleep(250);
    }
    return NULL;
}

// Resolve the API from the module. Returns true only if every required export
// was found; `missing` (optional) receives the name of the first that wasn't.
static int il2cpp_init(Il2CppApi* a, HMODULE game, const char** missing) {
    memset(a, 0, sizeof *a);
    a->game = game;
    struct { void** slot; const char* name; } tbl[] = {
        { (void**)&a->domain_get,             "il2cpp_domain_get" },
        { (void**)&a->thread_attach,          "il2cpp_thread_attach" },
        { (void**)&a->domain_get_assemblies,  "il2cpp_domain_get_assemblies" },
        { (void**)&a->assembly_get_image,     "il2cpp_assembly_get_image" },
        { (void**)&a->image_get_name,         "il2cpp_image_get_name" },
        { (void**)&a->class_from_name,        "il2cpp_class_from_name" },
        { (void**)&a->class_get_methods,      "il2cpp_class_get_methods" },
        { (void**)&a->method_get_name,        "il2cpp_method_get_name" },
        { (void**)&a->method_get_param_count, "il2cpp_method_get_param_count" },
        { (void**)&a->method_get_param,       "il2cpp_method_get_param" },
        { (void**)&a->type_get_name,          "il2cpp_type_get_name" },
        { (void**)&a->string_chars,           "il2cpp_string_chars" },
        { (void**)&a->string_length,          "il2cpp_string_length" },
    };
    for (size_t i = 0; i < sizeof(tbl) / sizeof(tbl[0]); ++i) {
        *tbl[i].slot = (void*)GetProcAddress(game, tbl[i].name);
        if (!*tbl[i].slot) { if (missing) *missing = tbl[i].name; return 0; }
    }
    a->free_ = (fn_free)GetProcAddress(game, "il2cpp_free"); // optional
    return 1;
}

// Find a loaded assembly image whose name contains `substr` (e.g. "Newtonsoft.Json").
static const Il2CppImage* il2cpp_find_image(Il2CppApi* a, const char* substr) {
    Il2CppDomain* d = a->domain_get();
    size_t n = 0;
    const Il2CppAssembly** asms = a->domain_get_assemblies(d, &n);
    for (size_t i = 0; i < n; ++i) {
        const Il2CppImage* img = a->assembly_get_image(asms[i]);
        const char* name = img ? a->image_get_name(img) : 0;
        if (name && strstr(name, substr)) return img;
    }
    return 0;
}

// Convert a managed System.String to UTF-8. Returns the string's char length.
static int il2cpp_str_utf8(Il2CppApi* a, Il2CppString* s, char* out, int outsz) {
    if (!s) { if (outsz) out[0] = 0; return 0; }
    int len = a->string_length(s);
    Il2CppChar* wc = a->string_chars(s);
    int n = WideCharToMultiByte(CP_UTF8, 0, (wchar_t*)wc, len, out, outsz - 1, 0, 0);
    out[n > 0 ? n : 0] = 0;
    return len;
}

// The native address of a compiled method = the first pointer-sized field of
// MethodInfo (methodPointer). Null for generic/uncompiled methods.
static void* il2cpp_method_ptr(const MethodInfo* m) {
    return *(void* const*)m;
}

// ---- Stage B: resolve the invoke/heap-walk API (optional) -----------------
// Returns 1 only if every extended export was found. Firing is skipped cleanly
// if any is missing, so the read-only observer is unaffected.
static int il2cpp_init_ext(Il2CppApi* a) {
    struct { void** slot; const char* name; } tbl[] = {
        { (void**)&a->object_new,                "il2cpp_object_new" },
        { (void**)&a->runtime_invoke,            "il2cpp_runtime_invoke" },
        { (void**)&a->class_get_type,            "il2cpp_class_get_type" },
        { (void**)&a->type_get_object,           "il2cpp_type_get_object" },
        { (void**)&a->class_get_method_from_name,"il2cpp_class_get_method_from_name" },
        { (void**)&a->image_get_class,           "il2cpp_image_get_class" },
        { (void**)&a->image_get_class_count,     "il2cpp_image_get_class_count" },
        { (void**)&a->class_get_name,            "il2cpp_class_get_name" },
        { (void**)&a->object_get_class,          "il2cpp_object_get_class" },
        { (void**)&a->gc_disable,                "il2cpp_gc_disable" },
        { (void**)&a->gc_enable,                 "il2cpp_gc_enable" },
        { (void**)&a->gc_foreach_heap,           "il2cpp_gc_foreach_heap" },
        { (void**)&a->class_is_subclass_of,      "il2cpp_class_is_subclass_of" },
    };
    for (size_t i = 0; i < sizeof(tbl)/sizeof(tbl[0]); ++i) {
        *tbl[i].slot = (void*)GetProcAddress(a->game, tbl[i].name);
        if (!*tbl[i].slot) return 0;
    }
    return 1;
}

// Find a class by simple name within an image (namespace unknown). Linear scan.
static Il2CppClass* il2cpp_find_class(Il2CppApi* a, const Il2CppImage* img, const char* name) {
    size_t n = a->image_get_class_count(img);
    for (size_t i = 0; i < n; ++i) {
        Il2CppClass* k = a->image_get_class(img, i);
        const char* cn = k ? a->class_get_name(k) : 0;
        if (cn && strcmp(cn, name) == 0) return k;
    }
    return 0;
}

// gc.choose(klass): first LIVE instance of klass on the managed heap, or NULL.
// il2cpp_gc_foreach_heap takes a plain C callback; we stash the query/result in
// file-scope statics so the callback signature (obj, userdata) needn't be exact.
static Il2CppApi*   g_choose_api;
static Il2CppClass* g_choose_klass;
static void*        g_choose_result;
static long         g_choose_seen;   // total objects the walk visited (diagnostic)
static void il2cpp__choose_cb(void* obj, void* userdata) {
    (void)userdata;
    g_choose_seen++;
    if (g_choose_result || !obj) return;
    Il2CppClass* oc = g_choose_api->object_get_class(obj);
    if (!oc) return;
    // Exact class, or any subclass of it (how Frida's gc.choose matches). Both
    // are cheap pointer ops -- no string handling in the hot walk callback.
    if (oc == g_choose_klass || g_choose_api->class_is_subclass_of(oc, g_choose_klass, 0))
        g_choose_result = obj;
}
static void* il2cpp_gc_choose_one(Il2CppApi* a, Il2CppClass* klass) {
    g_choose_api = a; g_choose_klass = klass; g_choose_result = 0; g_choose_seen = 0;
    a->gc_disable();
    a->gc_foreach_heap((void*)il2cpp__choose_cb, 0);
    a->gc_enable();
    return g_choose_result;
}
