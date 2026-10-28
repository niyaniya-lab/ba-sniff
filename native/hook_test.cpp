// hook_test.cpp -- Stage 2 self-test for the trampoline engine. Offline: it
// hooks functions we define here, never the game.
//
// It proves three things without touching Blue Archive:
//   1. Hooking a normal C function: the detour observes the arguments AND the
//      original still returns the correct value (via the trampoline).
//   2. rel32 relocation: a hand-built function whose stolen prologue contains a
//      `call rel32` still calls the right helper after being moved.
//   3. RIP-relative relocation: a hand-built function whose stolen prologue
//      loads a global via `[rip+disp32]` still reads the right global.
//
// If relocation math is wrong, the hooked function returns the wrong value or
// crashes -- so a correct return IS the proof.

#include <windows.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include "hook.h"

static int g_pass = 0, g_fail = 0;
static void check(const char* what, long long got, long long want) {
    bool ok = (got == want);
    printf("  [%s] %-40s got=%lld want=%lld\n", ok ? "OK " : "FAIL", what, got, want);
    if (ok) g_pass++; else g_fail++;
}

// ---- Test 1: hook a normal function ---------------------------------------
#pragma optimize("", off)   // keep a real stack frame so the prologue is >=14 stealable bytes
static int victim(int a, int b) {
    volatile int acc = 0;
    for (int i = 0; i < a; ++i) acc += b;
    return acc + 1;
}
#pragma optimize("", on)

typedef int (*victim_t)(int, int);
static victim_t g_victim_tramp;
static int g_vhits, g_seen_a, g_seen_b;
static int victim_detour(int a, int b) {
    g_vhits++; g_seen_a = a; g_seen_b = b;
    return g_victim_tramp(a, b);   // run the original through the trampoline
}

// ---- Test 2 & 3: hand-built functions that force relocation ----------------
static int reloc_helper(void) { return 10; }   // called via rel32 from the blob
static int64_t g_reloc_global = 100;            // read via [rip+disp32] from the blob

typedef int (*fn0)(void);
static fn0 g_rel_tramp, g_rip_tramp;
static int g_rel_hits, g_rip_hits;
static int rel_detour(void) { g_rel_hits++; return g_rel_tramp(); }
static int rip_detour(void) { g_rip_hits++; return g_rip_tramp(); }

// Build a small executable function near the module, patch its rel32/disp32.
static uint8_t* make_blob(const uint8_t* code, size_t n) {
    uint8_t* p = (uint8_t*)hook__alloc_near((void*)&reloc_helper, 64);
    memcpy(p, code, n);
    FlushInstructionCache(GetCurrentProcess(), p, n);
    return p;
}

int main(void) {
    printf("== Stage 2: trampoline engine self-test ==\n");

    // ---- Test 1 ----
    int before = victim(3, 4);                 // 3*4 + 1 = 13
    g_victim_tramp = (victim_t)install_hook((void*)victim, (void*)victim_detour);
    check("install_hook(victim) != NULL", g_victim_tramp != NULL, 1);
    if (g_victim_tramp) {
        int after = victim(3, 4);
        check("victim returns same after hook", after, before);
        check("detour fired once", g_vhits, 1);
        check("detour saw arg a", g_seen_a, 3);
        check("detour saw arg b", g_seen_b, 4);
    }

    // ---- Test 2: rel32 (call in the stolen range) ----
    // call reloc_helper; add eax,1 x4; ret   -> 10 + 4 = 14
    uint8_t rel_code[] = {
        0xE8, 0,0,0,0,         // call rel32 -> reloc_helper
        0x83,0xC0,0x01,        // add eax, 1
        0x83,0xC0,0x01,
        0x83,0xC0,0x01,
        0x83,0xC0,0x01,
        0xC3                   // ret
    };
    uint8_t* rel_blob = make_blob(rel_code, sizeof rel_code);
    *(int32_t*)(rel_blob + 1) = (int32_t)((intptr_t)&reloc_helper - ((intptr_t)rel_blob + 5));
    FlushInstructionCache(GetCurrentProcess(), rel_blob, sizeof rel_code);
    int rel_before = ((fn0)rel_blob)();
    check("rel blob baseline", rel_before, 14);
    g_rel_tramp = (fn0)install_hook(rel_blob, (void*)rel_detour);
    check("install_hook(rel blob) != NULL", g_rel_tramp != NULL, 1);
    if (g_rel_tramp) {
        int rel_after = ((fn0)rel_blob)();
        check("rel blob correct after hook (rel32 relocated)", rel_after, 14);
        check("rel detour fired", g_rel_hits, 1);
    }

    // ---- Test 3: RIP-relative (global load in the stolen range) ----
    // mov rax,[rip+g]; add rax,1 x2; ret   -> 100 + 2 = 102
    uint8_t rip_code[] = {
        0x48,0x8B,0x05, 0,0,0,0,   // mov rax, [rip+disp32] -> g_reloc_global
        0x48,0x83,0xC0,0x01,       // add rax, 1
        0x48,0x83,0xC0,0x01,       // add rax, 1
        0xC3                       // ret
    };
    uint8_t* rip_blob = make_blob(rip_code, sizeof rip_code);
    *(int32_t*)(rip_blob + 3) = (int32_t)((intptr_t)&g_reloc_global - ((intptr_t)rip_blob + 7));
    FlushInstructionCache(GetCurrentProcess(), rip_blob, sizeof rip_code);
    int rip_before = ((fn0)rip_blob)();
    check("rip blob baseline", rip_before, 102);
    g_rip_tramp = (fn0)install_hook(rip_blob, (void*)rip_detour);
    check("install_hook(rip blob) != NULL", g_rip_tramp != NULL, 1);
    if (g_rip_tramp) {
        int rip_after = ((fn0)rip_blob)();
        check("rip blob correct after hook (RIP-rel relocated)", rip_after, 102);
        check("rip detour fired", g_rip_hits, 1);
    }

    printf("\nTOTAL: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
