"""PSP PRX relocation map (type-1 .rel.* sections): relocs.py EBOOT [ADDR...]"""
import struct, sys
sys.path.insert(0, __import__('os').path.dirname(__file__))

def relocs(d):
    shoff, = struct.unpack_from('<I', d, 0x20)
    shnum, shstrndx = struct.unpack_from('<HH', d, 0x30)
    hdrs = [struct.unpack_from('<10I', d, shoff + i * 40) for i in range(shnum)]
    phoff, = struct.unpack_from('<I', d, 0x1C)
    segs = [struct.unpack_from('<8I', d, phoff + 32 * i)[2] for i in range(2)]  # vaddr
    out = {}
    for h in hdrs:
        if h[1] != 0x700000A0:
            continue
        for o in range(h[4], h[4] + h[5], 8):
            off, info = struct.unpack_from('<II', d, o)
            typ, ofsbase, addrbase = info & 0xFF, (info >> 8) & 0xFF, (info >> 16) & 0xFF
            out[segs[ofsbase] + off] = (typ, ofsbase, addrbase, o)
    return out

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read()
    r = relocs(d)
    print('relocs', len(r))
    names = {0: 'NONE', 2: 'R32', 4: 'J26', 5: 'HI16', 6: 'LO16', 7: 'GPREL16'}
    for a in sys.argv[2:]:
        a = int(a, 16)
        v = r.get(a)
        print('%08X: %s' % (a, (names.get(v[0], v[0]), 'ofsbase', v[1], 'addrbase', v[2], 'fileoff %X' % v[3]) if v else 'not relocated'))
