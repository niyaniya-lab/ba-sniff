r"""
Length-decoder test: prove native/lengthdis.h against a real disassembler.

A wrong instruction-length decoder is exactly what corrupts a hooked process
(you displace half an instruction, relocate garbage, and the game jumps into the
middle of an opcode). So we don't eyeball it -- we check it against capstone,
the reference x86-64 disassembler, over TENS OF THOUSANDS of real, compiler-
emitted instructions from system DLLs.

For every instruction capstone decodes, we feed the same starting bytes to our
ld_length() and assert it returns the identical length. Any mismatch is a
decoder bug, printed with the offending bytes so it can be fixed.

capstone is a TEST-ONLY oracle; it is never linked into the shipped observer DLL.

Run:  .venv\Scripts\python.exe native\ld_test.py
Build the DLL first:  native\build.bat ld_test_dll.cpp ld_test.dll
"""
import ctypes as C
import os
import struct
import sys

try:
    from capstone import Cs, CS_ARCH_X86, CS_MODE_64
except ImportError:
    print("[-] capstone not installed. Run: .venv\\Scripts\\python.exe -m pip install capstone")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
DLL = os.path.join(HERE, "ld_test.dll")

# Real x64 code to test against. System DLLs are always present and are dense
# with the exact instruction forms a compiler emits (the forms we'll hook).
CORPUS_DLLS = [
    r"C:\Windows\System32\ntdll.dll",
    r"C:\Windows\System32\kernelbase.dll",
    r"C:\Windows\System32\kernel32.dll",
]

# Hand-picked encodings that exercise the tricky paths, with the length a human
# can verify. We assert both capstone AND our decoder agree with these.
CURATED = [
    (b"\x90", 1, "nop"),
    (b"\x55", 1, "push rbp"),
    (b"\xC3", 1, "ret"),
    (b"\xCC", 1, "int3"),
    (b"\x48\x83\xEC\x28", 4, "sub rsp, 0x28"),
    (b"\x48\x89\x5C\x24\x08", 5, "mov [rsp+8], rbx"),
    (b"\x48\x8D\x05\x00\x00\x00\x00", 7, "lea rax, [rip+0]  (RIP-relative disp32)"),
    (b"\xE9\x00\x00\x00\x00", 5, "jmp rel32"),
    (b"\xE8\x00\x00\x00\x00", 5, "call rel32"),
    (b"\xFF\x25\x00\x00\x00\x00", 6, "jmp qword [rip+0]  (our own hook jump)"),
    (b"\x48\xB8\x11\x22\x33\x44\x55\x66\x77\x88", 10, "mov rax, imm64  (REX.W)"),
    (b"\x0F\x1F\x40\x00", 4, "nop dword [rax+0]"),
    (b"\xF3\x0F\x1E\xFA", 4, "endbr64"),
    (b"\x0F\x84\x00\x00\x00\x00", 6, "je rel32  (two-byte jcc)"),
    (b"\x0F\xB6\xC0", 3, "movzx eax, al"),
    (b"\x66\x90", 2, "xchg ax, ax  (operand-size prefix)"),
    (b"\xF7\xC0\x01\x00\x00\x00", 6, "test eax, 1  (F7 /0 -> imm32)"),
    (b"\xF6\xC0\x01", 3, "test al, 1  (F6 /0 -> imm8)"),
    (b"\x48\x8B\x04\x25\x00\x00\x00\x00", 8, "mov rax, [disp32]  (SIB, no base)"),
    (b"\xC7\x44\x24\x20\x00\x00\x00\x00", 8, "mov dword [rsp+0x20], 0  (imm32 + disp8)"),
]


def load_decoder():
    if not os.path.exists(DLL):
        print(f"[-] {DLL} missing. Build it: native\\build.bat ld_test_dll.cpp ld_test.dll")
        sys.exit(1)
    lib = C.CDLL(DLL)
    lib.ld_length_at.restype = C.c_size_t
    lib.ld_length_at.argtypes = [C.c_void_p]
    return lib


def parse_pe(path):
    """Minimal PE parse. Returns (data, sections, exception_dir_rva, size).

    sections: list of (virtual_addr, virtual_size, raw_ptr, raw_size) used to map
    an RVA to a file offset. exception_dir: the .pdata directory (RVA + size).
    """
    with open(path, "rb") as fh:
        data = fh.read()
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError(f"{path}: not a PE file")
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_opt = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic != 0x20B:
        raise ValueError(f"{path}: not PE32+ (x64)")
    # PE32+ optional header: NumberOfRvaAndSizes at +108, DataDirectory at +112.
    # Exception directory (.pdata) is data-directory index 3.
    exc_rva, exc_size = struct.unpack_from("<II", data, opt + 112 + 3 * 8)

    sect_table = opt + size_opt
    sections = []
    for i in range(num_sections):
        off = sect_table + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((vaddr, vsize, rawptr, rawsize))
    return data, sections, exc_rva, exc_size


def rva_to_off(sections, rva):
    for vaddr, vsize, rawptr, rawsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return rawptr + (rva - vaddr)
    return None


def check_curated(md, ld):
    print("== curated cases ==")
    fails = 0
    for raw, expected, desc in CURATED:
        buf = C.create_string_buffer(raw + b"\x00" * 16, len(raw) + 16)
        got = ld.ld_length_at(C.addressof(buf))
        cap = list(md.disasm(raw, 0, count=1))
        cap_len = cap[0].size if cap else -1
        ok = (got == expected == cap_len)
        if not ok:
            fails += 1
        print(f"  [{'OK ' if ok else 'FAIL'}] {raw.hex():<24} want={expected} capstone={cap_len} ours={got}  {desc}")
    print(f"   curated: {len(CURATED) - fails}/{len(CURATED)} passed\n")
    return fails


def sweep_dll(path, md, ld):
    """Walk every real function via .pdata; compare our length on each insn.

    x64 requires an exception-unwind record per function, so .pdata enumerates
    every function's [begin, end) in code -- disassembling only those ranges
    means we test pure instructions and never mistake inter-function data (jump
    tables, padding) for code.
    """
    data, sections, exc_rva, exc_size = parse_pe(path)
    if not exc_rva or not exc_size:
        raise ValueError(f"{path}: no exception directory")
    pdata_off = rva_to_off(sections, exc_rva)

    tested = 0
    mism = []
    n_funcs = exc_size // 12  # RUNTIME_FUNCTION = {begin, end, unwind} RVAs (12 bytes)
    for i in range(n_funcs):
        begin, end, _unwind = struct.unpack_from("<III", data, pdata_off + i * 12)
        if end <= begin:
            continue
        fstart = rva_to_off(sections, begin)
        if fstart is None:
            continue
        code = data[fstart:fstart + (end - begin)]
        # One buffer per function so the decoder reads real trailing bytes,
        # padded so a decode near the end can't over-read.
        buf = C.create_string_buffer(bytes(code) + b"\x00" * 16, len(code) + 16)
        base = C.addressof(buf)

        offset = 0
        for insn in md.disasm(bytes(code), 0):
            got = ld.ld_length_at(base + offset)
            tested += 1
            if got != insn.size and len(mism) < 40:
                mism.append((begin + offset, insn.bytes.hex(), insn.mnemonic, insn.op_str, insn.size, got))
            offset += insn.size
    return tested, mism


def main():
    ld = load_decoder()
    md = Cs(CS_ARCH_X86, CS_MODE_64)

    total_fail = check_curated(md, ld)

    grand_tested = 0
    grand_mism = 0
    for path in CORPUS_DLLS:
        if not os.path.exists(path):
            print(f"== {os.path.basename(path)} == SKIPPED (not found)")
            continue
        tested, mism = sweep_dll(path, md, ld)
        grand_tested += tested
        grand_mism += len(mism)
        print(f"== {os.path.basename(path)} ==  {tested} instructions, {len(mism)} mismatches")
        for off, hexb, mn, ops, want, got in mism:
            print(f"   MISMATCH @+0x{off:X}: {hexb:<30} {mn} {ops}   capstone={want} ours={got}")

    print()
    print(f"TOTAL: {grand_tested} real instructions tested, {grand_mism} mismatches; "
          f"curated failures: {total_fail}")
    if grand_mism == 0 and total_fail == 0:
        print("PASS -- decoder matches capstone on every instruction.")
        sys.exit(0)
    print("FAIL -- fix the decoder for the mismatches above.")
    sys.exit(1)


if __name__ == "__main__":
    main()
