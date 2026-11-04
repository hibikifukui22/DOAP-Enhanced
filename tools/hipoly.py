"""Point the low-poly girl files at the high-poly data in the archive index (doap.bin).

python -I tools/hipoly.py EBOOT_PATCHSET OUT_NAME [char] [wear]
Keeps every name; each char/X and wear/X entry takes the size and archive offset of char_h/X and
wear_h/X. Index: 'PAA', count at +8, 16-byte entries at +0x20 (name offset, size, tree left, right),
u32 archive offsets at +16's table (header word 4). Writes build/doap.OUT_NAME.bin and
build/DOAP-OUT_NAME.iso from the EBOOT of the patchset.
"""
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "research", "source-tree", "PSP_GAME", "USRDIR", "doap.bin")


def main(ps, out, kinds):
    b = bytearray(open(SRC, "rb").read())
    count, table = struct.unpack_from("<I", b, 8)[0], 0x20
    offs = struct.unpack_from("<I", b, 0x10)[0]
    names = {}
    for i in range(count):
        no = struct.unpack_from("<I", b, table + 16 * i)[0]
        names[b[no:b.index(b"\0", no)].decode("latin1")] = i
    done = 0
    for name, i in names.items():
        top, _, rest = name.partition("/")
        if top in kinds and top + "_h/" + rest in names:
            j = names[top + "_h/" + rest]
            struct.pack_into("<I", b, table + 16 * i + 4, struct.unpack_from("<I", b, table + 16 * j + 4)[0])
            struct.pack_into("<I", b, offs + 4 * i, struct.unpack_from("<I", b, offs + 4 * j)[0])
            done += 1
    bin_path = os.path.join(ROOT, "build", "doap.%s.bin" % out)
    open(bin_path, "wb").write(b)
    print("repointed", done, "files")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import build_patch
    iso = os.path.join(ROOT, "build", "DOAP-%s.iso" % out)
    if os.path.exists(iso):
        os.remove(iso)
    subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "iso", "patch_iso.py"),
                           build_patch.SRC_ISO, iso, "--shrink-file", "--grow-in-slack",
                           "--replace", "PSP_GAME/SYSDIR/EBOOT.BIN=" + os.path.join(ROOT, "build", "EBOOT.%s.BIN" % ps),
                           "--replace", "PSP_GAME/USRDIR/doap.bin=" + bin_path])
    print("ISO", iso)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:] or ["char", "wear"])
