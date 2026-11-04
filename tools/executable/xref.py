"""Find lui/imm address references to a module-relative range: xref.py EBOOT LO HI"""
import struct, sys
d = open(sys.argv[1], 'rb').read()
lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
B = 0x74
for a in range(0, 0x25c278, 4):
    w = struct.unpack_from('<I', d, a + B)[0]
    op = w >> 26
    if op in (0x09, 0x23, 0x31, 0x2B, 0x39, 0x20, 0x24, 0x21, 0x25, 0x28, 0x29, 0x0D):
        rs = (w >> 21) & 31
        imm = w & 0xffff
        if op != 0x0D and imm >= 0x8000:
            imm -= 0x10000
        for k in range(1, 16):
            p = struct.unpack_from('<I', d, a + B - 4 * k)[0]
            if (p >> 26) == 0xF and ((p >> 16) & 31) == rs:
                tgt = ((p & 0xffff) << 16) + imm
                if lo <= tgt < hi:
                    print('%08X %08X -> %08X' % (a, w, tgt))
                break
