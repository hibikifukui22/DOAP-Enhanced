"""Tiny MIPS32 assembler for code-cave stubs (only the instructions the stubs use)."""

REG = {n: i for i, n in enumerate(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split())}


def _r(x):
    return REG[x.lstrip("$")]


def _imm(v):
    if not -0x8000 <= v <= 0xFFFF:
        raise ValueError("immediate out of range: %d" % v)
    return v & 0xFFFF


class Asm:
    """Assemble at a module-relative address; labels resolve for branches and jal."""

    def __init__(self, org):
        self.org = org
        self.items = []   # (kind, args)
        self.labels = {}

    @property
    def pc(self):
        return self.org + 4 * len(self.items)

    def label(self, name):
        self.labels[name] = self.pc

    def _emit(self, fn):
        self.items.append(fn)

    # --- encoders ---
    def i(self, op, rs, rt, imm):
        self._emit(lambda pc: (op << 26) | (_r(rs) << 21) | (_r(rt) << 16) | _imm(imm))

    def r(self, rs, rt, rd, sa, funct):
        self._emit(lambda pc: (_r(rs) << 21) | (_r(rt) << 16) | (_r(rd) << 11) | (sa << 6) | funct)

    def nop(self):
        self._emit(lambda pc: 0)

    def addiu(self, rt, rs, v): self.i(0x09, rs, rt, v)
    def andi(self, rt, rs, v): self.i(0x0C, rs, rt, v)
    def ori(self, rt, rs, v): self.i(0x0D, rs, rt, v)
    def lui(self, rt, v): self.i(0x0F, "zero", rt, v)
    def lw(self, rt, off, rs): self.i(0x23, rs, rt, off)
    def sw(self, rt, off, rs): self.i(0x2B, rs, rt, off)
    def lbu(self, rt, off, rs): self.i(0x24, rs, rt, off)
    def sb(self, rt, off, rs): self.i(0x28, rs, rt, off)
    def lhu(self, rt, off, rs): self.i(0x25, rs, rt, off)
    def sh(self, rt, off, rs): self.i(0x29, rs, rt, off)
    def addu(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x21)
    def subu(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x23)
    def or_(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x25)
    def move(self, rd, rs): self.addu(rd, rs, "zero")
    def sll(self, rd, rt, sa): self.r("zero", rt, rd, sa, 0x00)
    def jr(self, rs): self.r(rs, "zero", "zero", 0, 0x08)
    def jalr(self, rs): self.r(rs, "zero", "ra", 0, 0x09)
    def srl(self, rd, rt, sa): self.r("zero", rt, rd, sa, 0x02)
    def and_(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x24)
    def xor(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x26)
    def sltu(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x2B)
    def slt(self, rd, rs, rt): self.r(rs, rt, rd, 0, 0x2A)
    def xori(self, rt, rs, v): self.i(0x0E, rs, rt, v)
    def sltiu(self, rt, rs, v): self.i(0x0B, rs, rt, v)

    # --- FPU (single) ---
    def lwc1(self, ft, off, base): self._emit(lambda pc: (0x31 << 26) | (_r(base) << 21) | (ft << 16) | _imm(off))
    def swc1(self, ft, off, base): self._emit(lambda pc: (0x39 << 26) | (_r(base) << 21) | (ft << 16) | _imm(off))
    def mtc1(self, rt, fs): self._emit(lambda pc: 0x44800000 | (_r(rt) << 16) | (fs << 11))
    def mfc1(self, rt, fs): self._emit(lambda pc: 0x44000000 | (_r(rt) << 16) | (fs << 11))
    def _fop(self, funct, fd, fs, ft=0): self._emit(lambda pc: 0x46000000 | (ft << 16) | (fs << 11) | (fd << 6) | funct)
    def add_s(self, fd, fs, ft): self._fop(0x00, fd, fs, ft)
    def sub_s(self, fd, fs, ft): self._fop(0x01, fd, fs, ft)
    def mul_s(self, fd, fs, ft): self._fop(0x02, fd, fs, ft)
    def div_s(self, fd, fs, ft): self._fop(0x03, fd, fs, ft)
    def abs_s(self, fd, fs): self._fop(0x05, fd, fs)
    def mov_s(self, fd, fs): self._fop(0x06, fd, fs)
    def c_lt_s(self, fs, ft): self._fop(0x3C, 0, fs, ft)
    def c_eq_s(self, fs, ft): self._fop(0x32, 0, fs, ft)
    def trunc_w_s(self, fd, fs): self._fop(0x0D, fd, fs)
    def cvt_s_w(self, fd, fs): self._emit(lambda pc: 0x46800020 | (fs << 11) | (fd << 6))
    def c_le_s(self, fs, ft): self._fop(0x3E, 0, fs, ft)

    def _bc1(self, cond, label):
        def enc(pc):
            off = (self.labels[label] - (pc + 4)) >> 2
            return 0x45000000 | (cond << 16) | (off & 0xFFFF)
        self._emit(enc)

    def bc1f(self, label): self._bc1(0, label)
    def bc1t(self, label): self._bc1(1, label)

    def _branch(self, op, rs, rt, label):
        def enc(pc):
            off = (self.labels[label] - (pc + 4)) >> 2
            return (op << 26) | (_r(rs) << 21) | (_r(rt) << 16) | (off & 0xFFFF)
        self._emit(enc)

    def b(self, label): self._branch(0x04, "zero", "zero", label)
    def beq(self, rs, rt, label): self._branch(0x04, rs, rt, label)
    def bne(self, rs, rt, label): self._branch(0x05, rs, rt, label)

    def bal(self, label):  # bgezal zero, label
        def enc(pc):
            off = (self.labels[label] - (pc + 4)) >> 2
            return (0x01 << 26) | (0 << 21) | (0x11 << 16) | (off & 0xFFFF)
        self._emit(enc)

    # No jal here: cave words carry no J26 relocation, so calls must use jalr on a
    # PC-relative address (see build_patch.vb_ai_wait_hold).

    def li32(self, rt, v):
        """Load a 32-bit constant (two words, no relocation)."""
        v &= 0xFFFFFFFF
        self.lui(rt, v >> 16)
        self.ori(rt, rt, v & 0xFFFF)

    def assemble(self):
        out = []
        for n, fn in enumerate(self.items):
            out.append(fn(self.org + 4 * n) & 0xFFFFFFFF)
        return out


def jal_word(target):
    return (0x03 << 26) | ((target >> 2) & 0x03FFFFFF)
