"""Static helpers for the decrypted ULUS-10521 EBOOT (relocatable PRX, base 0).

Usage:
  python -I psp_disasm.py EBOOT.BIN stubs              # list imports with stub addresses
  python -I psp_disasm.py EBOOT.BIN callers NAME|ADDR  # jal sites calling a stub/function
  python -I psp_disasm.py EBOOT.BIN dis ADDR [COUNT]   # disassemble at ADDR (hex)
  python -I psp_disasm.py EBOOT.BIN func ADDR          # disassemble function containing ADDR

Addresses are module-relative (PPSSPP normally loads the module at 0x08804000).
"""
import struct
import sys

import capstone

SEG_FILE_OFF = 0x74  # program header 0: vaddr 0 at file offset 0x74

NIDS = {
    0x984C27E7: "sceDisplayWaitVblankStart",
    0x46F186C3: "sceDisplayWaitVblankStartCB",
    0x36CDFADE: "sceDisplayWaitVblank",
    0x8EB9EC49: "sceDisplayWaitVblankCB",
    0x289D82FE: "sceDisplaySetFrameBuf",
    0xEEDA2E54: "sceDisplayGetFrameBuf",
    0x9C6EAAD7: "sceDisplayGetVcount",
    0x0E20F177: "sceDisplaySetMode",
    0xDEA197D4: "sceDisplayGetMode",
    0x4D4E10EC: "sceDisplayIsVblank",
    0x773DD3A3: "sceDisplayGetCurrentHcount",
    0x210EAB3A: "sceDisplayGetAccumulatedHcount",
    0x40F1469C: "sceDisplayWaitVblankStartMulti",
    0x77ED8B3A: "sceDisplayWaitVblankStartMultiCB",
    0x7ED59BC4: "sceDisplaySetHoldMode",
    0xA544C486: "sceDisplaySetResumeMode",
    0xDBA6C4C4: "sceDisplayGetFramePerSec",
    0xAB49E76A: "sceGeListEnQueue",
    0x1C0D95A6: "sceGeListEnQueueHead",
    0xE0D68148: "sceGeListUpdateStallAddr",
    0x03444EB4: "sceGeListSync",
    0xB287BD61: "sceGeDrawSync",
    0xA4FC06A4: "sceGeSetCallback",
    0x05DB22CE: "sceGeUnsetCallback",
    0xE47E40E4: "sceGeEdramGetAddr",
    0x1F6752AD: "sceGeEdramGetSize",
    0xB448EC0D: "sceGeBreak",
    0x4C06E472: "sceGeContinue",
    0x5FB86AB0: "sceGeListDeQueue",
    0x57C8945B: "sceGeGetMtx",
    0x438A385A: "sceGeSaveContext",
    0x0BF608FB: "sceGeRestoreContext",
    0xDC93CFEF: "sceGeGetCmd",
    0xCEADEB47: "sceKernelDelayThread",
    0x68DA9E36: "sceKernelDelayThreadCB",
    0x369ED59D: "sceKernelGetSystemTimeLow",
    0x82BC5777: "sceKernelGetSystemTimeWide",
    0xDB738F35: "sceKernelGetSystemTime",
    0x4E3A1105: "sceKernelWaitSema",
    0x3F53E640: "sceKernelSignalSema",
    0xD6DA4BA1: "sceKernelCreateSema",
    0x402FCF22: "sceKernelWaitEventFlag",
    0x1FB15A32: "sceKernelSetEventFlag",
    0x812346E4: "sceKernelClearEventFlag",
    0x55C20A00: "sceKernelCreateEventFlag",
    0x446D8DE6: "sceKernelCreateThread",
    0xF475845D: "sceKernelStartThread",
    0x9ACE131E: "sceKernelSleepThread",
    0x82826F70: "sceKernelSleepThreadCB",
    0xD59EAD2F: "sceKernelWakeupThread",
    0x6652B8CA: "sceKernelSetAlarm",
    0x20FFF560: "sceKernelCreateVTimer",
    0xC68D9437: "sceKernelStartVTimer",
    0xD8B299AE: "sceKernelSetVTimerHandler",
    0x110DEC9A: "sceKernelUSec2SysClock",
    0x27CC57F0: "sceKernelSysClock2USec",
    0x71EC4271: "sceKernelLibcGettimeofday",
    0x27CC57F0 ^ 0: "sceKernelLibcTime?",
    0x91E4F6A7: "sceKernelLibcClock",
    0x79D1C3FA: "sceKernelDcacheWritebackAll",
    0xB435DEC5: "sceKernelDcacheWritebackInvalidateAll",
    0x3EE30821: "sceKernelDcacheWritebackRange",
    0x34B9FA9E: "sceKernelDcacheWritebackInvalidateRange",
    0x1F803938: "sceCtrlReadBufferPositive",
    0x3A622550: "sceCtrlPeekBufferPositive",
    0x6A2774F3: "sceCtrlSetSamplingCycle",
    0x1F4011E6: "sceCtrlSetSamplingMode",
    0x3E65A0EA: "sceSuspendForUser_3E65A0EA",
    0xEADB1BD7: "sceKernelPowerLock",
    0x3AEE7261: "sceKernelPowerUnlock",
    0x090CCB3F: "sceKernelPowerTick",
    0x13A5ABEF: "sceAudioChangeChannelConfig",
    0x136CAF51: "sceAudioOutputBlocking",
    0x13F592BC: "sceAudioOutputPannedBlocking",
    0x5EC81C55: "sceAudioChReserve",
    0x6FC46853: "sceAudioChRelease",
    0xE2D56B2D: "sceAudioOutputPanned",
    0x8C1009B2: "sceAudioOutput",
    0x38553111: "sceAudioSRCChReserve",
    0x5C37C0AE: "sceAudioSRCChRelease",
    0xE0727056: "sceAudioSRCOutputBlocking",
    0x01562BA3: "sceAudioOutput2Reserve",
    0x2D53F36E: "sceAudioOutput2OutputBlocking",
    0xA2BEAA6C: "sceSasSetADSRmode?",
    0xA3589D81: "__sceSasCore",
    0x50A14DFC: "__sceSasCoreWithMix",
    0x42778A9F: "__sceSasInit",
}

MIPS = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 + capstone.CS_MODE_LITTLE_ENDIAN)


def load(path):
    data = open(path, "rb").read()
    return data


def sections(data):
    shoff, = struct.unpack_from("<I", data, 0x20)
    shnum, shstrndx = struct.unpack_from("<HH", data, 0x30)
    hdrs = [struct.unpack_from("<10I", data, shoff + i * 40) for i in range(shnum)]
    stroff = hdrs[shstrndx][4]
    out = {}
    for h in hdrs:
        name = data[stroff + h[0]: data.index(b"\0", stroff + h[0])].decode()
        out[name] = {"addr": h[3], "off": h[4], "size": h[5]}
    return out


def word(data, addr):
    return struct.unpack_from("<I", data, addr + SEG_FILE_OFF)[0]


def cstr(data, addr):
    off = addr + SEG_FILE_OFF
    return data[off: data.index(b"\0", off)].decode(errors="replace")


def stubs(data):
    sec = sections(data)
    s = sec[".lib.stub"]
    res = {}
    addr = s["addr"]
    end = addr + s["size"]
    while addr < end:
        name_p, ver_flags, sizes, nid_p, func_p = struct.unpack_from("<IIIII", data, addr + SEG_FILE_OFF)
        entsize = sizes & 0xFF
        funcs = sizes >> 16
        lib = cstr(data, name_p) if name_p else "?"
        for i in range(funcs):
            nid = word(data, nid_p + 4 * i)
            res[func_p + 8 * i] = (lib, nid, NIDS.get(nid, "%s_%08X" % (lib, nid)))
        addr += entsize * 4
    return res


def dis(data, addr, count, names=None):
    names = names or {}
    code = data[addr + SEG_FILE_OFF: addr + SEG_FILE_OFF + 4 * count]
    lines = []
    for ins in MIPS.disasm(code, addr):
        extra = ""
        if ins.mnemonic in ("jal", "j"):
            tgt = int(ins.op_str, 16)
            if tgt in names:
                extra = "  ; " + names[tgt]
        lines.append("%08X: %08X  %-8s %s%s" % (ins.address, struct.unpack("<I", ins.bytes)[0], ins.mnemonic, ins.op_str, extra))
    return lines


def text_range(data):
    s = sections(data)[".text"]
    return s["addr"], s["addr"] + s["size"]


def jal_callers(data, target):
    lo, hi = text_range(data)
    enc = 0x0C000000 | ((target >> 2) & 0x03FFFFFF)
    out = []
    for a in range(lo, hi, 4):
        if word(data, a) == enc:
            out.append(a)
    return out


def func_start(data, addr):
    # walk back to the nearest "addiu sp, sp, -N" preceded by jr ra (+delay) or nop padding
    a = addr
    lo, _ = text_range(data)
    while a > lo:
        w = word(data, a)
        if (w & 0xFFFF0000) == 0x27BD0000 and (w & 0x8000):
            prev = word(data, a - 8)
            if prev == 0x03E00008 or word(data, a - 4) == 0:
                return a
        a -= 4
    return lo


def func_end(data, start):
    _, hi = text_range(data)
    a = start
    while a < hi:
        if word(data, a) == 0x03E00008:
            # keep going if more code follows that is not a new prologue
            nxt = word(data, a + 8)
            if (nxt & 0xFFFF8000) == 0x27BD8000 or a + 8 >= hi:
                return a + 8
        a += 4
    return hi


def main():
    data = load(sys.argv[1])
    cmd = sys.argv[2]
    st = stubs(data)
    names = {a: v[2] for a, v in st.items()}
    if cmd == "stubs":
        for a, (lib, nid, n) in sorted(st.items()):
            print("%08X %-22s %08X %s" % (a, lib, nid, n))
    elif cmd == "callers":
        arg = sys.argv[3]
        tgt = None
        for a, n in names.items():
            if n == arg:
                tgt = a
        if tgt is None:
            tgt = int(arg, 16)
        for c in jal_callers(data, tgt):
            print("%08X (func %08X)" % (c, func_start(data, c)))
    elif cmd == "dis":
        a = int(sys.argv[3], 16)
        n = int(sys.argv[4]) if len(sys.argv) > 4 else 64
        print("\n".join(dis(data, a, n, names)))
    elif cmd == "func":
        a = func_start(data, int(sys.argv[3], 16))
        e = func_end(data, a)
        print("\n".join(dis(data, a, (e - a) // 4, names)))


if __name__ == "__main__":
    main()
