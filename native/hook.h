// hook.h -- minimal x86-64 inline hook (trampoline) engine.
//
// Overwrite a function's first bytes with a jump to our detour; the displaced
// original bytes are copied to a "trampoline" that ends with a jump back to the
// rest of the function, so calling the trampoline still runs the original.
//
// Three concerns, handled below:
//   1. Steal WHOLE instructions >= 14 bytes (ld_length) -- never split one.
//   2. Relocate position-dependent fields (RIP-relative disp32, rel32 branches)
//      when they move to the trampoline (ld_decode locates them). rel8 short
//      branches can't reach from a far trampoline, so we refuse rather than
//      corrupt.
//   3. Allocate the trampoline within +/-2GB of the target so relocated fields
//      still reach.
// Patching runs with other threads suspended and the icache flushed.

#pragma once
#include <windows.h>
#include <tlhelp32.h>
#include <stdint.h>
#include "lengthdis.h"

// jmp qword ptr [rip+0]; <abs64 target> -- reaches anywhere.
#define HOOK_JMP_LEN 14
static void hook__write_abs_jmp(uint8_t* at, const void* target) {
    at[0] = 0xFF; at[1] = 0x25;
    *(uint32_t*)(at + 2) = 0;
    *(const void**)(at + 6) = target;
}

// Executable memory within +/-2GB of target (keeps relocated rel32/RIP in range).
static void* hook__alloc_near(const void* target, size_t size) {
    SYSTEM_INFO si; GetSystemInfo(&si);
    uintptr_t gran = si.dwAllocationGranularity;
    uintptr_t t = (uintptr_t)target;
    for (uintptr_t off = gran; off < 0x7FFF0000ULL; off += gran) {
        uintptr_t cands[2] = { t - off, t + off };
        for (int i = 0; i < 2; ++i) {
            void* base = (void*)(cands[i] & ~(gran - 1));
            void* p = VirtualAlloc(base, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
            if (p) return p;
        }
    }
    return VirtualAlloc(NULL, size, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
}

// Copy `steal` bytes of code from src to dst, fixing up moved fields.
// Returns 1 on success, 0 if something (e.g. a rel8 in range) can't be moved.
static int hook__copy_relocate(const uint8_t* src, uint8_t* dst, size_t steal) {
    size_t o = 0;
    while (o < steal) {
        ld_insn insn;
        if (!ld_decode(src + o, &insn)) return 0;
        memcpy(dst + o, src + o, insn.length);

        // old_addr - new_addr for this instruction (the +o cancels).
        int64_t delta = (int64_t)(src - dst);

        if (insn.rip_rel) {
            int32_t* d = (int32_t*)(dst + o + insn.rip_disp_off);
            int64_t nd = (int64_t)(*d) + delta;
            if (nd < INT32_MIN || nd > INT32_MAX) return 0;
            *d = (int32_t)nd;
        }
        if (insn.rel_size == 4) {
            int32_t* r = (int32_t*)(dst + o + insn.rel_off);
            int64_t nr = (int64_t)(*r) + delta;
            if (nr < INT32_MIN || nr > INT32_MAX) return 0;
            *r = (int32_t)nr;
        } else if (insn.rel_size == 1) {
            return 0;  // rel8 can't reach a far trampoline; refuse
        }
        o += insn.length;
    }
    return 1;
}

// Suspend / resume every other thread in this process (skip the caller).
static void hook__each_other_thread(void (*fn)(HANDLE)) {
    DWORD pid = GetCurrentProcessId(), self = GetCurrentThreadId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snap == INVALID_HANDLE_VALUE) return;
    THREADENTRY32 te; te.dwSize = sizeof te;
    if (Thread32First(snap, &te)) {
        do {
            if (te.th32OwnerProcessID == pid && te.th32ThreadID != self) {
                HANDLE h = OpenThread(THREAD_SUSPEND_RESUME, FALSE, te.th32ThreadID);
                if (h) { fn(h); CloseHandle(h); }
            }
        } while (Thread32Next(snap, &te));
    }
    CloseHandle(snap);
}
static void hook__suspend(HANDLE h) { SuspendThread(h); }
static void hook__resume(HANDLE h) { ResumeThread(h); }

// Install an inline hook. Returns the trampoline (call it to run the original),
// or NULL on failure. `detour` is jumped to on every call of `target`.
static void* install_hook(void* target, const void* detour) {
    uint8_t* tgt = (uint8_t*)target;

    // 1. how many whole instructions to steal (>= HOOK_JMP_LEN).
    size_t steal = 0;
    while (steal < HOOK_JMP_LEN) {
        size_t l = ld_length(tgt + steal);
        if (l == 0) return NULL;
        steal += l;
    }

    // 2. trampoline = relocated stolen bytes + jump back past the patch.
    uint8_t* tramp = (uint8_t*)hook__alloc_near(target, steal + HOOK_JMP_LEN);
    if (!tramp) return NULL;
    if (!hook__copy_relocate(tgt, tramp, steal)) { VirtualFree(tramp, 0, MEM_RELEASE); return NULL; }
    hook__write_abs_jmp(tramp + steal, tgt + steal);
    FlushInstructionCache(GetCurrentProcess(), tramp, steal + HOOK_JMP_LEN);

    // 3. patch the entry (others suspended, then icache flushed).
    hook__each_other_thread(hook__suspend);
    DWORD old;
    VirtualProtect(tgt, steal, PAGE_EXECUTE_READWRITE, &old);
    hook__write_abs_jmp(tgt, detour);
    for (size_t i = HOOK_JMP_LEN; i < steal; ++i) tgt[i] = 0x90;  // nop the remainder
    VirtualProtect(tgt, steal, old, &old);
    FlushInstructionCache(GetCurrentProcess(), tgt, steal);
    hook__each_other_thread(hook__resume);

    return tramp;
}
