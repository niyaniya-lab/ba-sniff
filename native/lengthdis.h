// lengthdis.h -- a minimal x86-64 instruction decoder (length + relocation info).
//
// WHY THIS EXISTS
//   To hook a function we overwrite the first bytes of its code with a jump to
//   our own code. On x64 an absolute jump we can place anywhere is 14 bytes:
//       FF 25 00 00 00 00        jmp qword ptr [rip+0]
//       <8-byte absolute target>
//   But x86-64 instructions are variable length (1..15 bytes). We must NOT
//   overwrite a partial instruction -- we steal *whole* instructions totalling
//   >= 14 bytes, copy them to a "trampoline", and run them there before jumping
//   back. Two jobs, both served here:
//     1. ld_length()  -- how many bytes is the instruction at this address?
//     2. ld_decode()  -- same, plus WHERE the position-dependent fields are:
//        a RIP-relative displacement, or a rel8/rel32 branch target. Those
//        encode an address relative to the instruction, so once we move the
//        instruction to the trampoline they must be fixed up or it jumps/reads
//        to the wrong place.
//
//   Neither function interprets what an instruction DOES; they only measure it
//   and locate its relocatable fields.
//
// HOW AN x86-64 INSTRUCTION IS LAID OUT (the order we walk):
//   [legacy prefixes]* [REX | VEX | EVEX] opcode [ModRM] [SIB] [disp] [imm]
//
//   Correctness is verified against capstone over ~880k real instructions from
//   system DLLs by native/ld_test.py -- run it after any change here.

#pragma once
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Decoded facts a hook installer needs. Offsets are relative to the instruction.
typedef struct {
    size_t length;       // total instruction length in bytes
    int    rip_rel;      // 1 if it has a RIP-relative disp32 memory operand
    int    rip_disp_off; // byte offset of that disp32 (valid iff rip_rel)
    int    rel_size;     // branch/call relative operand: 0 none, 1 rel8, 4 rel32
    int    rel_off;      // byte offset of the rel operand (valid iff rel_size)
} ld_insn;

static int ld__in(uint8_t b, const uint8_t* table, size_t n) {
    for (size_t i = 0; i < n; ++i) if (table[i] == b) return 1;
    return 0;
}
#define LD_IN(b, tbl) ld__in((b), (tbl), sizeof(tbl))

// VEX/EVEX opcodes (in the 0F map) that carry an imm8. The 0F3A map always does;
// the 0F38 map never does; these are the 0F-map exceptions.
static int ld__vex_0f_imm8(uint8_t opc) {
    return opc == 0x70 || opc == 0x71 || opc == 0x72 || opc == 0x73 ||
           opc == 0xC2 || opc == 0xC4 || opc == 0xC5 || opc == 0xC6;
}

// Full decode. Returns instruction length (0 on failure) and fills *out.
static size_t ld_decode(const void* address, ld_insn* out) {
    const uint8_t* start = (const uint8_t*)address;
    const uint8_t* p = start;

    static const uint8_t prefixes[] = {
        0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E, 0x26, 0x64, 0x65, 0x66, 0x67
    };
    static const uint8_t op1_modrm[] = {
        0x00,0x01,0x02,0x03, 0x08,0x09,0x0A,0x0B, 0x10,0x11,0x12,0x13,
        0x18,0x19,0x1A,0x1B, 0x20,0x21,0x22,0x23, 0x28,0x29,0x2A,0x2B,
        0x30,0x31,0x32,0x33, 0x38,0x39,0x3A,0x3B, 0x62,0x63,0x69,0x6B,
        0x80,0x81,0x82,0x83, 0x84,0x85,0x86,0x87, 0x88,0x89,0x8A,0x8B,
        0x8C,0x8D,0x8E,0x8F, 0xC0,0xC1,0xC4,0xC5, 0xC6,0xC7,0xD0,0xD1,
        0xD2,0xD3,0xD8,0xD9, 0xDA,0xDB,0xDC,0xDD, 0xDE,0xDF,0xF6,0xF7,
        0xFE,0xFF
    };
    static const uint8_t op1_imm8[] = {
        0x04,0x0C,0x14,0x1C,0x24,0x2C,0x34,0x3C, 0x6A,0x6B, 0x80,0x82,
        0x83,0xA8, 0xC0,0xC1,0xC6, 0xCD,0xD4,0xD5, 0xEB
    };
    static const uint8_t op1_imm32[] = {
        0x05,0x0D,0x15,0x1D,0x25,0x2D,0x35,0x3D, 0x68,0x69, 0x81,0xA9,
        0xC7, 0xE8,0xE9
    };
    static const uint8_t op2_modrm[] = {
        0x00,0x01,0x02,0x03,0x0D, 0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
        0x18,0x19,0x1A,0x1B,0x1C,0x1D,0x1E,0x1F, 0x28,0x29,0x2A,0x2B,0x2C,
        0x2D,0x2E,0x2F, 0x40,0x41,0x42,0x43,0x44,0x45,0x46,0x47,0x48,0x49,
        0x4A,0x4B,0x4C,0x4D,0x4E,0x4F, 0x50,0x51,0x52,0x53,0x54,0x55,0x56,
        0x57,0x58,0x59,0x5A,0x5B,0x5C,0x5D,0x5E,0x5F, 0x60,0x61,0x62,0x63,
        0x64,0x65,0x66,0x67,0x68,0x69,0x6A,0x6B,0x6C,0x6D,0x6E,0x6F, 0x70,
        0x71,0x72,0x73,0x74,0x75,0x76,0x77, 0x7C,0x7D,0x7E,0x7F, 0x90,0x91,
        0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9A,0x9B,0x9C,0x9D,0x9E,
        0x9F, 0xA3,0xA4,0xA5, 0xAB,0xAC,0xAD,0xAE,0xAF, 0xB0,0xB1,0xB2,
        0xB3,0xB4,0xB5,0xB6,0xB7,0xB8,0xB9,0xBA,0xBB,0xBC,0xBD,0xBE,0xBF,
        0xC0,0xC1,0xC2,0xC3,0xC4,0xC5,0xC6,0xC7, 0xD0,0xD1,0xD2,0xD3,0xD4,
        0xD5,0xD6,0xD7,0xD8,0xD9,0xDA,0xDB,0xDC,0xDD,0xDE,0xDF, 0xE0,0xE1,
        0xE2,0xE3,0xE4,0xE5,0xE6,0xE7,0xE8,0xE9,0xEA,0xEB,0xEC,0xED,0xEE,
        0xEF, 0xF0,0xF1,0xF2,0xF3,0xF4,0xF5,0xF6,0xF7,0xF8,0xF9,0xFA,0xFB,
        0xFC,0xFD,0xFE
    };
    static const uint8_t op2_imm8[] = {
        0x70,0x71,0x72,0x73, 0xA4,0xAC, 0xBA, 0xC2,0xC4,0xC5,0xC6
    };
    static const uint8_t op2_imm32[] = {
        0x80,0x81,0x82,0x83,0x84,0x85,0x86,0x87,
        0x88,0x89,0x8A,0x8B,0x8C,0x8D,0x8E,0x8F   // 0F 8x = jcc rel32
    };

    int operand_override = 0;   // 0x66 seen -> 16-bit operands (imm32 -> imm16)
    int address_override = 0;   // 0x67 seen
    int rex_w = 0;
    int has_modrm = 0;
    int imm8 = 0, imm16 = 0, imm32 = 0, imm64 = 0;
    int disp8 = 0, disp32 = 0;
    uint8_t op = 0;             // legacy opcode (0 for SIMD paths)

    int rip_rel = 0, rip_disp_off = 0;
    int rel_size = 0;

    // ---- 1. legacy prefixes -------------------------------------------------
    while (LD_IN(*p, prefixes)) {
        if (*p == 0x66) operand_override = 1;
        if (*p == 0x67) address_override = 1;
        p++;
    }

    // ---- 2/3. VEX / EVEX escape, else REX + opcode -------------------------
    uint8_t lead = *p;
    if (lead == 0xC5) {                       // 2-byte VEX: C5 <p1> opcode  (0F map)
        p += 2;
        uint8_t opc = *p++;
        if (opc == 0x77) has_modrm = 0;       // vzeroupper/vzeroall: no ModRM
        else { has_modrm = 1; if (ld__vex_0f_imm8(opc)) imm8 = 1; }
    } else if (lead == 0xC4) {                // 3-byte VEX: C4 <p1> <p2> opcode
        uint8_t map = p[1] & 0x1F;            // m-mmmm: 1=0F, 2=0F38, 3=0F3A
        p += 3;
        uint8_t opc = *p++;
        if (map == 1 && opc == 0x77) has_modrm = 0;
        else {
            has_modrm = 1;
            if (map == 3) imm8 = 1;
            else if (map == 1 && ld__vex_0f_imm8(opc)) imm8 = 1;
        }
    } else if (lead == 0x62) {                // EVEX: 62 <p0> <p1> <p2> opcode
        uint8_t map = p[1] & 0x03;            // mm: 1=0F, 2=0F38, 3=0F3A
        p += 4;
        uint8_t opc = *p++;
        has_modrm = 1;
        if (map == 3) imm8 = 1;
        else if (map == 1 && ld__vex_0f_imm8(opc)) imm8 = 1;
    } else {
        // REX prefix (64-bit): 0x40..0x4F. Only the REX immediately before the
        // opcode is effective; consume any consecutive run and keep the last.
        while ((*p & 0xF0) == 0x40) { rex_w = (*p & 0x08) != 0; p++; }

        op = *p++;
        int two_byte_jcc = 0;
        if (op == 0x0F) {
            uint8_t op2 = *p++;
            if (op2 == 0x38) {                // three-byte 0F 38: ModRM, no imm
                p++;
                has_modrm = 1;
            } else if (op2 == 0x3A) {         // three-byte 0F 3A: ModRM + imm8
                p++;
                has_modrm = 1;
                imm8 = 1;
            } else {
                has_modrm = LD_IN(op2, op2_modrm);
                if (LD_IN(op2, op2_imm8))  imm8 = 1;
                if (LD_IN(op2, op2_imm32)) imm32 = 1;
                if (op2 >= 0x80 && op2 <= 0x8F) { two_byte_jcc = 1; rel_size = 4; } // jcc rel32
            }
        } else {
            has_modrm = LD_IN(op, op1_modrm);
            if (LD_IN(op, op1_imm8))  imm8 = 1;
            if (LD_IN(op, op1_imm32)) imm32 = 1;

            // Opcodes the compact tables don't cover explicitly:
            if (op >= 0x70 && op <= 0x7F) { imm8 = 1; rel_size = 1; }  // jcc rel8
            if (op >= 0xE0 && op <= 0xE3) { imm8 = 1; rel_size = 1; }  // loop/jrcxz rel8
            if (op >= 0xE4 && op <= 0xE7) imm8 = 1;                     // in/out imm8
            if (op >= 0xB0 && op <= 0xB7) imm8 = 1;                     // mov r8, imm8
            if (op >= 0xB8 && op <= 0xBF) { if (rex_w) imm64 = 1; else imm32 = 1; } // mov r, imm
            if (op >= 0xA0 && op <= 0xA3) { if (address_override) imm32 = 1; else imm64 = 1; } // mov moffs
            if (op == 0xC2 || op == 0xCA) imm16 = 1;                    // ret/retf imm16
            if (op == 0xC8) { imm16 = 1; imm8 = 1; }                    // enter imm16, imm8
            if (op == 0xE8 || op == 0xE9) rel_size = 4;                 // call/jmp rel32
            if (op == 0xEB) rel_size = 1;                               // jmp rel8
        }

        // 0x66 shrinks a 32-bit immediate to 16-bit for data ops -- but NOT for
        // E8/E9 or 0F 8x, whose rel32 stays 32-bit in 64-bit mode.
        if (operand_override && imm32 && op != 0xE8 && op != 0xE9 && !two_byte_jcc) {
            imm32 = 0; imm16 = 1;
        }
    }

    // ---- 4. ModRM (+ group-1 immediate quirks; legacy only) ----------------
    if (has_modrm) {
        int modrm_off = (int)(p - start);
        uint8_t modrm = *p++;
        uint8_t mod = modrm >> 6;
        uint8_t reg = (modrm >> 3) & 7;
        uint8_t rm  = modrm & 7;

        // F6 /0,/1 (TEST r/m8, imm8) and F7 /0,/1 (TEST r/m, imm) carry an
        // immediate the opcode alone doesn't reveal -- it depends on reg.
        if (op == 0xF6 && (reg == 0 || reg == 1)) imm8 = 1;
        if (op == 0xF7 && (reg == 0 || reg == 1)) { if (operand_override) imm16 = 1; else imm32 = 1; }

        // ---- 5. SIB byte: present for mod != 3 and rm == 100b ---------------
        if (mod != 3 && rm == 4) {
            uint8_t sib = *p++;
            if ((sib & 7) == 5 && mod == 0) disp32 = 1; // base==101b, mod==00 -> disp32 (absolute)
        }
        // ---- 6. displacement by addressing mode ----------------------------
        else if (mod == 0 && rm == 5) {
            // RIP-relative [rip + disp32] -- the one displacement we must relocate.
            disp32 = 1;
            rip_rel = 1;
            rip_disp_off = modrm_off + 1;   // disp32 sits right after the ModRM byte
        }
        if (mod == 1)      disp8  = 1;       // [reg + disp8]
        else if (mod == 2) disp32 = 1;       // [reg + disp32]
    }

    // ---- 7. total up -------------------------------------------------------
    size_t len = (size_t)(p - start);
    if (disp8)  len += 1;
    if (disp32) len += 4;
    if (imm8)   len += 1;
    if (imm16)  len += 2;
    if (imm32)  len += 4;
    if (imm64)  len += 8;

    if (out) {
        out->length = len;
        out->rip_rel = rip_rel;
        out->rip_disp_off = rip_disp_off;
        out->rel_size = rel_size;
        out->rel_off = rel_size ? (int)(len - rel_size) : 0;  // rel is the trailing field
    }
    return len;
}

// Length-only convenience wrapper (what most callers and the corpus test use).
static size_t ld_length(const void* address) {
    return ld_decode(address, 0);
}

#undef LD_IN

#ifdef __cplusplus
}
#endif
