#!/usr/bin/env python3
"""Build the patcher's payload from the clean ISO and the two finished ISOs.

python patcher/build_payload.py

Every differing byte range becomes an XOR run against the clean ISO, so the payload holds no
readable game code and is useless without the original disc. Runs closer than GAP bytes are merged.

payload.bin, little endian:
  'DOAP' u32 version, u64 iso size, sha256 clean ISO, sha256 normal ISO, sha256 high-poly ISO,
  sha256 of the source bytes under all runs, u32 run count, then runs sorted by offset:
  u64 offset, u32 length, u32 group (0 = always, 1 = high-poly only), then all run data in order.
"""
import hashlib
import os
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(ROOT, "Dead or Alive - Paradise (USA) (En,Ja,Fr,De).iso")
NORMAL = os.path.join(ROOT, "build", "DOAP-pt6.iso")
HIPOLY = os.path.join(ROOT, "build", "DOAP-pt6-hipoly.iso")
OUT = os.path.join(ROOT, "patcher", "payload.bin")
CHUNK = 1 << 24
GAP = 64


def diff_runs(a_path, b_path):
    """Yield (offset, length) of differing ranges, merging gaps smaller than GAP."""
    import numpy as np
    runs = []
    with open(a_path, "rb") as a, open(b_path, "rb") as b:
        pos = 0
        while True:
            x, y = a.read(CHUNK), b.read(CHUNK)
            if not x:
                break
            idx = np.nonzero(np.frombuffer(x, "u1") != np.frombuffer(y, "u1"))[0]
            if len(idx):
                breaks = np.nonzero(np.diff(idx) > GAP)[0]
                starts = np.concatenate(([0], breaks + 1))
                ends = np.concatenate((breaks, [len(idx) - 1]))
                for s, e in zip(starts, ends):
                    o, n = pos + int(idx[s]), int(idx[e] - idx[s]) + 1
                    if runs and o - (runs[-1][0] + runs[-1][1]) <= GAP:
                        runs[-1] = (runs[-1][0], o + n - runs[-1][0])
                    else:
                        runs.append((o, n))
            pos += len(x)
    return runs


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(CHUNK):
            h.update(block)
    return h.digest()


def read(path, off, n):
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(n)


def xor(a, b):
    n = len(a)
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(n, "little")


def main():
    size = os.path.getsize(CLEAN)
    assert size == os.path.getsize(NORMAL) == os.path.getsize(HIPOLY)
    base = diff_runs(CLEAN, NORMAL)
    extra = diff_runs(NORMAL, HIPOLY)
    print("base runs", [(hex(o), n) for o, n in base][:12], "total", sum(n for _, n in base))
    print("hipoly runs", len(extra), "total", sum(n for _, n in extra))
    overlap = [r for r in extra for q in base if r[0] < q[0] + q[1] and q[0] < r[0] + r[1]]
    assert not overlap, overlap
    runs = sorted([(o, n, 0, NORMAL) for o, n in base] + [(o, n, 1, HIPOLY) for o, n in extra])
    src = hashlib.sha256()
    table, data = b"", b""
    for o, n, group, target in runs:
        clean = read(CLEAN, o, n)
        src.update(clean)
        table += struct.pack("<QII", o, n, group)
        data += xor(clean, read(target, o, n))
    head = b"DOAP" + struct.pack("<IQ", 1, size) + sha(CLEAN) + sha(NORMAL) + sha(HIPOLY) + src.digest()
    open(OUT, "wb").write(head + struct.pack("<I", len(runs)) + table + data)
    print("payload", os.path.getsize(OUT), "bytes,", len(runs), "runs")
    print("clean sha", sha(CLEAN).hex())


if __name__ == "__main__":
    main()
