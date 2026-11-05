"""Apply guarded word patches to the decrypted ULUS-10521 EBOOT and build a test ISO.

python -I tools/build_patch.py PATCHSET [--iso]

Each patch is (module-relative address, original word, new word, note). The
original word is verified before writing, so a wrong source EBOOT is refused.
"""
import hashlib
import os
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_EBOOT = os.path.join(ROOT, "research", "EBOOT.ppsspp-dump.BIN")
SRC_EBOOT_SHA = "5dc782c28239d69d8f6587a96322a02c52ab4c9ba80af2e0496f1aa717375145"
SRC_ISO = os.path.join(ROOT, "Dead or Alive - Paradise (USA) (En,Ja,Fr,De).iso")
SEG_FILE_OFF = 0x74

# Frame gate: flip at 0x360 calls sceDisplayWaitVblankStartMulti([0x289F00] + 1).
# [0x289F00] defaults to 1 (2 vblanks = 30 FPS); the movie player sets it to 0.
# (0xAC, the lw in the getter, is LO16-relocated, so the add at 0x380 is patched instead.)
GATE = [
    (0x00000380, 0x24450001, 0x24050001, "flip: addiu a1,v0,1 -> addiu a1,zero,1 (always 1 vblank)"),
]

# Global frame step [seg1+0x238B4] = base speed [+0x22EB0] * slow factor [+0x22EB8],
# read by ~100 update sites. Init 0x3D48 sets both to 1.0; set base to 0.5, keep slow at 1.0.
SPEED_HALF = [
    (0x00003D4C, 0x3C043F80, 0x3C043F00, "speed init: lui a0,0x3f80 -> 0x3f00 (0.5)"),
    (0x00003D54, 0x4484A000, 0x44846000, "speed init: mtc1 a0,f20 -> mtc1 a0,f12 (base speed arg = 0.5)"),
    (0x00003D60, 0x4600A306, 0x460C6500, "speed init: mov.s f12,f20 -> add.s f20,f12,f12 (slow factor stays 1.0)"),
]

# Scene-graph animation: 0xECE48 (only caller of 0xEDD84 -> 0x1EC350 time += dt) passes a
# literal 1.0 (0 when paused) instead of the global step.
SCENE_DT_HALF = [
    (0x000ECE70, 0x3C043F80, 0x3C043F00, "scene-graph dt: lui a0,0x3f80 -> 0x3f00 (0.5 per update)"),
]

# Character animation (all girls: relax scenes, minigames, island). The anim player 0x163F14
# advances a float frame time (+0x34, copy +0x4C) by the global step, but the pose sampler
# 0x1685D0 truncates it to a whole frame before converting to seconds ((frame - s3) / 30), so at
# 60 Hz (0.5 per update) the pose and root motion only change every other update.
# Use the fractional time instead (still clamped to >= 1.0 like the original).
ANIM_FRAC = [
    (0x001688F8, 0x4600634D, 0x4616603C, "anim: trunc.w.s f13,f12 -> c.lt.s f12,f22 (time < 1.0)"),
    (0x00168908, 0x44046800, 0x45030001, "anim: mfc1 a0,f13 -> bc1tl +1"),
    (0x0016890C, 0x58800001, 0x4600B306, "anim: blezl a0 -> mov.s f12,f22 (clamp to 1.0)"),
    (0x00168910, 0x34040001, 0x44936800, "anim: ori a0,1 -> mtc1 s3,f13"),
    (0x00168914, 0x00932023, 0x46806B60, "anim: subu a0,a0,s3 -> cvt.s.w f13,f13"),
    (0x00168918, 0x44846000, 0x460D6301, "anim: mtc1 a0,f12 -> sub.s f12,f12,f13"),
    (0x0016891C, 0x46806320, 0x00000000, "anim: cvt.s.w f12,f12 -> nop (div.s f24,f12,f26 follows)"),
]

# Pool Hopping: its logic thread (0xCE68C) runs 0xD1378 then waits one scheduler tick (0x920(1)
# via 0xD135C). The logic counts updates (partner AI, hop charge, phase timers) and never reads
# the global step, so at 60 Hz all of it ran 2x. Wait two ticks instead: logic at 30 Hz.
POOL_HOP_PACE = [
    (0x000CE6C4, 0x34050001, 0x34050002, "pool hopping: logic thread waits 2 ticks (30 Hz logic)"),
]

# Casino dealer (and other users of motion player 0x580F4): motion time +0x50 += literal 1.0 per
# update (0x581B0), so at 60 Hz the dealer animated 2x. Step 0.5 per update instead.
CASINO_ANIM_HALF = [
    (0x000581B0, 0x3C043F80, 0x3C043F00, "motion 0x580F4: time += 1.0 -> 0.5 per update (casino dealer)"),
]

# Casino payouts x5 (user request 2026-11-01): casino games call set-money 0x1AAAF0(new) for bets and
# payouts. These sites go through a wrapper that reads the balance (0x1AAAD0) and, when the new
# value is higher (a payout, incl. returned stakes), multiplies the increase by CASINO_PAYOUT_MULT.
CASINO_PAYOUT_MULT = 5
CASINO_SETMONEY_SITES = [0x564D8, 0x58B8C, 0x58EB4, 0x59044, 0x59D84, 0x59ECC, 0x5A364, 0x5A910,
                         0x5AA4C, 0x5AB5C, 0x5AC90, 0x5AD9C, 0x749D4, 0x78048, 0x781F0, 0x78B24,
                         0x78CB8, 0x79008]


# Casino payouts x10 with matching on-screen amounts (same idea as the DOAX2 winnings multiplier).
# Blackjack: payouts are bet x a float odds constant, and that one amount feeds the seat chips, the
# money and the win counter, so the odds themselves are scaled. Left alone: the push (no multiply at
# 0x5AD58) and the 0.5 return at 0x59E7C, which hand back a stake rather than pay a win. These
# constants apply to every seat, so other players' table chips grow the same way.
CASINO_ODDS_X10 = [
    (0x00058AC8, 0x3C043FC0, 0x3C044170, "blackjack odds 1.5 -> 15"),
    (0x00058E7C, 0x3C064000, 0x3C0641A0, "blackjack odds 2 -> 20"),
    (0x0005A5C0, 0x3C044020, 0x3C0441C8, "blackjack odds 2.5 -> 25"),
    (0x0005A5CC, 0x3C044080, 0x3C044220, "blackjack odds 4 -> 40"),
    (0x0005A5D4, 0x3C044000, 0x3C0441A0, "blackjack odds 2 -> 20"),
]


def emit_x9(a, reg):
    """t0 = reg * 9."""
    a.sll("t0", reg, 3)
    a.addu("t0", "t0", reg)


def emit_x10(a, reg):
    """reg *= 10, using t8."""
    a.sll("t8", reg, 3)
    a.sll(reg, reg, 1)
    a.addu(reg, reg, "t8")


def emit_tail(a, name, target):
    """Jump to the module-relative target with ra and the argument registers untouched."""
    a.move("t9", "ra")
    a.bal(name + "_pc")
    a.nop()
    a.label(name + "_pc")
    here = a.labels[name + "_pc"]
    a.li32("t8", target - here)
    a.addu("t8", "t8", "ra")
    a.move("ra", "t9")
    a.jr("t8")
    a.nop()


def emit_casino_ui(a):
    # Poker: the player's win pays bet x pot, and each display step recomputes that from the base
    # values, so the money, the table chips and the three counter updates are scaled separately.
    a.label("poker_win")                     # set-money(balance + bet x pot); s0 = table, s1 = win
    emit_x9(a, "s1")
    a.addu("a0", "a0", "t0")
    a.lw("t1", 0xE8, "s0")                   # the player's chips (seat 0)
    a.addu("t1", "t1", "t0")
    a.sw("t1", 0xE8, "s0")
    emit_tail(a, "poker_win", 0x1AAAF0)
    a.label("poker_show")                    # win counter += bet x pot - shown
    a.lw("t1", 0x4BB8, "s0")
    a.lw("t2", 0x4BC8, "s0")
    a.r("t1", "t2", "zero", 0, 0x18)         # mult
    a.r("zero", "zero", "t1", 0, 0x12)       # mflo
    emit_x9(a, "t1")
    a.addu("a1", "a1", "t0")
    emit_tail(a, "poker_show", 0x70C60)
    a.label("poker_take")                    # win counter -= bet x pot
    emit_x10(a, "a1")
    emit_tail(a, "poker_take", 0x70C60)
    a.label("poker_bank")                    # balance counter += bet x pot
    emit_x10(a, "a1")
    emit_tail(a, "poker_bank", 0x70BF8)
    # Slots: the spin's pay per coin (machine +0x64, negative on a loss) is chosen just before
    # 0x9F44C commits the result to the money, and the win shown later is computed from it too.
    a.label("slots_pay")
    a.lw("t1", 0x64, "a0")
    a.slt("t2", "zero", "t1")
    a.beq("t2", "zero", "slots_commit")
    a.nop()
    emit_x10(a, "t1")
    a.sw("t1", 0x64, "a0")
    a.label("slots_commit")
    emit_tail(a, "slots_pay", 0x9F44C)


# Crash loading some volleyball matches with high poly models: 0x20B918 calls 0x21631C to copy palette
# data into object +0x5C, which was 0 on the object in the crash, so memcpy wrote to an invalid address
# (seen at 32 and 64 MB, so not a memory shortage). Why the buffer is missing is not known yet. Both
# copy routines reached from 0x20AFD4 now skip the copy unless the destination is at or above VRAM
# (0x04000000, uncached bit ignored). 0x21631C takes arguments in a0-a3 and t0-t3, so the check uses t4.
def emit_copy_guards(a):
    a.label("tex_guard")
    a.lw("t4", 0x24, "a0")
    a.srl("t4", "t4", 24)
    a.andi("t4", "t4", 0x3F)
    a.sltiu("t4", "t4", 4)
    a.bne("t4", "zero", "tex_skip")
    a.nop()
    emit_tail(a, "tex_copy", 0x21625C)
    a.label("tex_skip")
    a.jr("ra")
    a.nop()
    a.label("pal_guard")
    a.lw("t4", 0x5C, "a0")
    a.srl("t4", "t4", 24)
    a.andi("t4", "t4", 0x3F)
    a.sltiu("t4", "t4", 4)
    a.bne("t4", "zero", "pal_skip")
    a.nop()
    emit_tail(a, "pal_copy", 0x21631C)
    a.label("pal_skip")
    a.jr("ra")
    a.nop()


def emit_casino_mult(a):
    a.label("casino_setmoney")
    a.addiu("sp", "sp", -16)
    a.sw("ra", 0, "sp")
    a.sw("a0", 4, "sp")
    a.bal("casino_pc")
    a.nop()
    a.label("casino_pc")
    here = a.labels["casino_pc"]
    a.sw("ra", 8, "sp")
    a.li32("t9", 0x1AAAD0 - here)            # current balance
    a.addu("t9", "t9", "ra")
    a.jalr("t9")
    a.nop()
    a.lw("a0", 4, "sp")
    a.slt("t0", "v0", "a0")
    a.beq("t0", "zero", "casino_set")        # not an increase: unchanged
    a.nop()
    a.subu("t1", "a0", "v0")                 # increase
    a.sll("t2", "t1", 2)
    a.addu("t1", "t1", "t2")                 # x5
    a.addu("a0", "v0", "t1")
    a.label("casino_set")
    a.lw("t9", 8, "sp")
    a.li32("t0", 0x1AAAF0 - here)
    a.addu("t9", "t9", "t0")
    a.jalr("t9")                             # set-money clamps to 99,999,999
    a.nop()
    a.lw("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)


# ANIM_FRAC end clamp: the original sampled whole frames, so never past the motion's last frame
# (+0x70); a fractional time in (last, last + 1) lands past the last key and the root position
# lurched at motion ends (girls teleporting 0.3-0.9 units in volleyball). The set-time call 0x16892C
# goes through a stub limiting f12/f24 ((frame - s3) / 30 seconds) to (+0x70 - s3) / 30.
def emit_anim_end_clamp(a):
    a.label("anim_clamp")
    a.move("t1", "ra")
    a.lhu("t0", 0x70, "s1")
    a.mtc1("t0", 14)
    a.cvt_s_w(14, 14)
    a.mtc1("s3", 15)
    a.cvt_s_w(15, 15)
    a.sub_s(14, 14, 15)
    a.div_s(14, 14, 26)                      # f26 = 30.0 in 0x1685D0
    a.c_lt_s(14, 12)
    a.nop()
    a.bc1f("anim_clamp_ok")
    a.nop()
    a.mov_s(12, 14)
    a.mov_s(24, 14)                          # also used for the 0x220460 call at 0x168940
    a.label("anim_clamp_ok")
    a.bal("anim_clamp_pc")
    a.nop()
    a.label("anim_clamp_pc")
    a.li32("t9", 0x220328 - a.labels["anim_clamp_pc"])
    a.addu("t9", "t9", "ra")
    a.move("ra", "t1")
    a.jr("t9")
    a.nop()


# Code cave: a third PT_LOAD segment appended to the ELF at vaddr 0x532A00, just past
# segment 1 (bss ends 0x5329A4). (0x18D888 looked dead but is a .cplinit static
# initializer, so overwriting it black-screened at boot.) Cave words have no relocations.
CAVE = 0x532A00
FRAME_COUNTER = 0x450E64        # main-loop update counter (+1 per update, 0x2048)
VB_AI_RECORDS = 0x4D2870        # seg1 0x829C0: volleyball AI script record, 92 bytes per player


VB_ATTACK_BLOCK = 0x4D7FF0      # seg1 0x88140: volleyball attack/jump state (one block)
VB_STATE_TIMERS = 0x4D2610      # seg1 0x82760: game state byte/timer, then 4 x 12-byte player timers
VB_SCRIPT_TIMER = 0x4DA610      # seg1 0x8A760: volleyball intro script step timer

# Volleyball per-update counters: (call site, routine, base, per-player 92-byte stride, fields).
# Fields are (offset, "b"|"h"|"w", "dec"|"inc"): 30 Hz frame counts.
VB_AI_WRAPS = [
    # AI script VM (0xA7F78): wait countdowns and the phase-2 timer (compared against 151)
    (0xA82E4, 0xA9AD4, VB_AI_RECORDS, True, [(0xA, "h", "dec"), (0xC, "h", "dec"), (0xE, "h", "dec")]),
    (0xA83CC, 0xA9CD0, VB_AI_RECORDS, True, [(0xA, "h", "dec"), (0xC, "h", "dec"), (0xE, "h", "dec")]),
    (0xA84EC, 0xA9670, VB_AI_RECORDS, True, [(0x48, "w", "inc")]),
    # 0xA4BC4: the other +0xA/+0xC countdown (pre-jump waits; found 2026-11-01 when CPU spikers
    # took off early: their waits ran every update at 60 Hz). Called from the VM and 0xA7A48.
    (0xA8468, 0xA4BC4, VB_AI_RECORDS, True, [(0xA, "h", "dec"), (0xC, "h", "dec")]),
    (0xA7B68, 0xA4BC4, VB_AI_RECORDS, True, [(0xA, "h", "dec"), (0xC, "h", "dec")]),
    # 0xA5754: time-in-state counters, +1 per update: game state timer (+4; the intro cuts every
    # 75 of it) and per player p: state timer +0xC + 0xC*p, motion timer +0x12 + 0xC*p (halfword).
    # NOT held: holding them crashed a match (2026-11-01, invalid access in the attack code
    # 0xBBFA0); the intro cut timing comes from 0xB9524 below instead.
    # (0xA25E4, 0xA5754, VB_STATE_TIMERS, False, [...]),
    # 0xB7EB8 -> 0xB8398 (via pointer): 600-update countdown at 0x4AD904
    # 0xB9524: volleyball intro/cut-scene script player; step timer 0x4DA610 +1 per call, steps
    # end at script durations (intro shots: 75). Wrapping its caller 0xB7EB8 crashed the match.
    (0xB83D0, 0xB9524, VB_SCRIPT_TIMER, False, [(0x0, "w", "inc")]),
    # Attack state handler (0xBE8D4 jump table): jump-frames byte +0x19, +1 per update while
    # the attacker's state +0x4A is 2/3, compared with per-character swing thresholds (0xBE86C)
    (0xBE9C0, 0xBF31C, VB_ATTACK_BLOCK, False, [(0x19, "b", "inc")]),
]
LOAD = {"b": "lbu", "h": "lhu", "w": "lw"}
STORE = {"b": "sb", "h": "sh", "w": "sw"}


def vb_ai_wait_hold(recorder=False, pool=False, vb=True, cam=False, phys=False, skip_targets=(), casino=False, anim_clamp=False, ph_hud=False, tex_guard=False):
    """Hold the volleyball AI script's per-update counters on alternate updates.

    Each wrapped routine runs once per update per player. The wrapper snapshots the listed
    fields, calls the routine, and on odd updates undoes an exact -1 (countdown, except
    1 -> 0 so the waited action still fires: DOAX2's rule) or an exact +1 (timer).
    Position independent: addresses are formed from the bal return address, and calls use
    jalr because cave words carry no J26 relocation.
    """
    from mips import Asm, jal_word
    a = Asm(CAVE)
    calls = []
    if recorder == "generic":
        # First, at a fixed address: the main thread sleeps inside it (vblank wait), so a live
        # patch that moved it would return into other code.
        emit_generic_recorder(a)
        calls.append((0x2010, 0x360, "recorder"))
    for site, target, base, per_player, fields in (VB_AI_WRAPS if vb else []):
        if target in skip_targets:
            continue
        name = "wrap_%x_%x" % (target, site)
        frame = (20 + 4 * len(fields) + 15) & ~15     # sp stays 16-aligned (VFPU lv.q/sv.q on the stack)
        a.label(name)
        a.addiu("sp", "sp", -frame)
        a.sw("ra", 0, "sp")
        a.sw("s0", 4, "sp")
        a.sw("s1", 8, "sp")
        a.move("s0", "a0")
        a.bal(name + "_pc")
        a.nop()
        a.label(name + "_pc")
        here = a.labels[name + "_pc"]
        a.sw("ra", 12, "sp")                     # absolute address of label "here"
        a.li32("s1", base - here)
        a.addu("s1", "s1", "ra")                 # record table / block
        if per_player:
            a.sll("t0", "s0", 6)
            a.sll("t1", "s0", 4)
            a.addu("t0", "t0", "t1")
            a.sll("t1", "s0", 3)
            a.addu("t0", "t0", "t1")
            a.sll("t1", "s0", 2)
            a.addu("t0", "t0", "t1")             # p * 92
            a.addu("s1", "s1", "t0")             # this player's record
        for k, (off, size, kind) in enumerate(fields):
            getattr(a, LOAD[size])("t0", off, "s1")
            a.sw("t0", 20 + 4 * k, "sp")
        a.li32("t0", FRAME_COUNTER - here)
        a.addu("t0", "t0", "ra")
        a.lw("t0", 0, "t0")
        a.andi("t0", "t0", 1)
        a.sw("t0", 16, "sp")                     # hold flag for this update
        a.li32("t9", target - here)
        a.lw("t0", 12, "sp")
        a.addu("t9", "t9", "t0")
        a.jalr("t9")
        a.move("a0", "s0")
        a.lw("t0", 16, "sp")
        a.beq("t0", "zero", name + "_done")
        a.nop()
        for k, (off, size, kind) in enumerate(fields):
            skip = "%s_k%d" % (name, k)
            getattr(a, LOAD[size])("t0", off, "s1")              # new
            a.lw("t1", 20 + 4 * k, "sp")                          # old
            if kind == "dec":
                a.beq("t0", "zero", skip)        # 1 -> 0 (or idle): keep
                a.addiu("t2", "t0", 1)
            else:
                a.addiu("t2", "t0", -1)
            a.bne("t2", "t1", skip)              # not an exact one-step change
            a.nop()
            getattr(a, STORE[size])("t1", off, "s1")             # hold
            a.label(skip)
        a.label(name + "_done")
        a.lw("ra", 0, "sp")
        a.lw("s0", 4, "sp")
        a.lw("s1", 8, "sp")
        a.jr("ra")
        a.addiu("sp", "sp", frame)
        calls.append((site, target, name))
    if cam:
        emit_cam_lerp(a)
        for site in CAM_EASE_S2:
            calls.append((site, 0x13B0D8, "cam_s2"))
        for site in CAM_EASE_S1:
            calls.append((site, 0x13B0D8, "cam_s1"))
        calls.append((0x15C984, 0x13B0D8, "cam_pose"))
        for site in CAM_BASE_CALLERS:
            calls.append((site, 0x3DD8, "cam_base_one"))
    if anim_clamp:
        emit_anim_end_clamp(a)
        calls.append((0x16892C, 0x220328, "anim_clamp"))
    if casino == "ui":
        emit_casino_ui(a)
        calls.append((0x79008, 0x1AAAF0, "poker_win"))
        calls.append((0x7928C, 0x70C60, "poker_show"))
        calls.append((0x75664, 0x70C60, "poker_take"))
        calls.append((0x75670, 0x70BF8, "poker_bank"))
        calls.append((0x9FD84, 0x9F44C, "slots_pay"))
    elif casino:
        assert CASINO_PAYOUT_MULT == 5
        emit_casino_mult(a)
        for site in CASINO_SETMONEY_SITES:
            calls.append((site, 0x1AAAF0, "casino_setmoney"))
    if tex_guard:
        emit_copy_guards(a)
        calls.append((0x20B1C8, 0x21625C, "tex_guard"))
        calls.append((0x20B610, 0x21625C, "tex_guard"))
        calls.append((0x20B918, 0x21631C, "pal_guard"))
    if phys:
        emit_phys_smooth(a)
        calls.append((0xCC094, 0xC5864, "phys_outer"))
        calls.append((0xC5924, 0xC60B8, "phys_step"))
    if pool:
        emit_pool_hop(a, ph_hud)
        calls.append((0xCE6B4, 0xD1378, "ph_wrap"))
        if ph_hud:
            emit_ph_text(a)
            calls.append((0xD0704, 0x2FCE0, "ph_text"))
            calls.append((0xD1194, 0x2FCE0, "ph_text"))
    if recorder == "generic":
        pass
    elif recorder:
        emit_recorder(a)
        calls.append((0x2010, 0x360, "recorder"))
    words = a.assemble()
    if 4 * len(words) > CAVE_CODE_MAX:
        raise SystemExit("cave code is %d bytes; data starts at +0x%X" % (4 * len(words), CAVE_CODE_MAX))
    patches = []
    for n, w in enumerate(words):
        patches.append((CAVE + 4 * n, None, w, "cave: volleyball AI counter hold"))
    for site, target, name in calls:
        tag = "diag: " if name == "recorder" else ""
        patches.append((site, jal_word(target), jal_word(a.labels[name]), "%s0x%X -> cave %s" % (tag, target, name)))
    return patches


# Diagnostic recorder (vb2d): the main loop's flip call (0x2010, jal 0x360) goes through the
# cave, which appends a 32-byte entry to a ring buffer whenever a watched value changes:
#   +0 frame counter, +4 jump-frames byte, +8..+0x14 per player (state +0x3E | +0x4A << 8 |
#   motion +0x40 << 16), +0x18/+0x1C spare. Buffer: [0] entry count, [0x20] last, [0x40] current,
#   entries from +0x80. Read it with tools/recorder_dump.py.
REC_BUF = CAVE + 0x1000
REC_ENTRIES = 4096
REC_BUF_SIZE = 0x80 + 32 * REC_ENTRIES
PLAYER_RECORDS = 0x471F38       # seg1 0x22088, 292 bytes per player
VB_BALL = 0x497AF0              # seg1 0x47C40: ball x/y/z at +0/+4/+8, flight clock +0x1CC4
# Attack trace (second buffer): every update while a player's attack state +0x4A != 0,
# a 96-byte entry: FC, jump byte, player, 0, ball x/y/z/clock, then that player's record
# words +0x00..+0x3C (16 words). [ATK_BUF] = entry count, entries from +0x10.
ATK_BUF = REC_BUF + REC_BUF_SIZE
ATK_ENTRIES = 2048
ATK_BUF_SIZE = 0x10 + 96 * ATK_ENTRIES


# Generic recorder (diag builds since anim1d): every update, while enabled, appends a 64-byte
# entry: frame counter + the u32 at each of up to 15 configured RAM addresses. Configure and
# read it with tools/rec.py. Layout at REC2_ABS: +0 count, +4 enable, +8 write index,
# +0x40 absolute addresses (15 words), entries from +0x100 (REC2_ENTRIES x 64 bytes).
# The game takes its heap from the lowest free memory right after the module, and save states
# made on older builds only reserved the cave code, so nothing may live past the cave code
# (the vb2d buffers at CAVE+0x1000 overlapped live heap). REC2_ABS sits in a 3 MB block that is
# zero in every RAM snapshot (0x09B87000-0x09E97000: boot, menus, volleyball, Pool Hopping, relax).
REC2_ABS = 0x09D00000
REC2_ENTRIES = 4096
REC2_SIZE = 0x100 + 64 * REC2_ENTRIES
# Cave memory: code, then data at CAVE + CAVE_CODE_MAX, CAVE_MEMSZ reserved by the loader in every
# build. Save states made on builds before ph2 only reserved their cave code (heap follows at the
# next 4 KB boundary, CAVE + 0x600), so test states must come from a fresh boot of a ph2+ build.
CAVE_CODE_MAX = 0x2000
CAVE_MEMSZ = 0x10000
DIAG_MEMSZ = CAVE_MEMSZ


# Pool Hopping (ph2): the logic thread 0xCE68C calls 0xD1378 once per update and the logic counts
# updates (partner AI, hop charge, phase timers; it never reads the global step), so at 60 Hz all
# of it ran 2x and a tap's press and release edges were both seen as hops. The wrapper at the
# 0xCE6B4 call runs the logic on alternate updates (30 Hz, original timing) and:
#  - ORs the pad edge fields (+4 release, +8 press, +0xC, +0x10 of the six 28-byte pad records at
#    0x471C50) seen on the skipped update into the next run, so no tap is lost; restores them after.
#  - smooths the Pool Hopping struct (girls, floats, camera target) for the in-between frame, as
#    DOAX2's phase smoother: on run updates it snapshots the block, runs the logic, then writes the
#    midpoint of old/new for float words that moved <= 4.0 (ints and pointers read as denormals
#    and are skipped); on skipped updates it restores the exact value unless someone else wrote it.
PAD = 0x471C50
# Smoothed blocks: the Pool Hopping struct, and the camera struct the logic feeds through setters
# (the camera thread runs every update but its inputs only changed on logic updates).
PH_BLOCKS = [(0x4F4340, 0x4F8C40), (0x3C8A00, 0x3C9700)]
PH_N = sum((hi - lo) // 4 for lo, hi in PH_BLOCKS)
PH_DATA = CAVE + CAVE_CODE_MAX  # +0 toggle, +0x10 pad acc[24], +0x80 pad saved[24], +0x100 N[], M[]


# Camera script interpolation (cam1): camera move commands call the easing 0x13B0D8(elapsed, total,
# ease, sub, subdiv) with elapsed = trunc(float elapsed frames) (struct +0x80 in 0x14EFC0, +0x60 in
# 0x1504B0). At 60 Hz elapsed advances 0.5 per update, so the camera moved every other update (win
# poses, intros, relax pans). The wrapper evaluates the easing at the whole frame and the next one
# and interpolates by the fraction (DOAX2's pose camera averaging, generalised).
CAM_EASE_S2 = [0x14F298, 0x14F3CC, 0x14F500, 0x14F634, 0x14F768]   # elapsed float at s2+0x80
CAM_EASE_S1 = [0x150614, 0x15065C, 0x1506A4]                       # elapsed float at s1+0x60


# Camera script durations (cam2): camera commands convert frame counts with N / base speed
# (getter 0x3DD8 = 0.5 after SPEED_HALF) and then advance their clocks by the global step (0.5), so
# every camera move/wait took twice as long in seconds (win-pose pan: 1.20 vs 2.40 units/s at 30).
# All 23 callers of 0x3DD8 are camera code; they get a stub that returns the original 1.0.
CAM_BASE_CALLERS = [0x13DB08, 0x147D0C, 0x14815C, 0x1482C0, 0x1483A0, 0x148560, 0x148654, 0x1486F0,
                    0x14879C, 0x1488B0, 0x1489F8, 0x148AAC, 0x14BEE8, 0x14BF00, 0x14BF5C, 0x14F880,
                    0x14F89C, 0x14F8C0, 0x150920, 0x150A40, 0x150BA4, 0x1510D4, 0x15190C]


# Girl body physics (phys1): 0xC5864 (per girl, main thread) is a fixed-step accumulator:
# acc(+0x174) += global step, then the 30 Hz physics step 0xC60B8 runs trunc(acc) times, then the
# record is post-processed for rendering. At 60 Hz the state changed every other update (stutter,
# most visible in volleyball). Like DOAX2's phase smoother: the step call (0xC5924) snapshots the
# record, steps, and leaves the midpoint of old/new in float words that moved <= 4.0, so this
# update's post-processing shows the half step; the 0xC5864 call (0xCC094) first restores the
# exact state (unless the word changed meanwhile). Records: 0x4DB000 + idx * 0x180, idx < 16.
PHYS_REC, PHYS_STRIDE, PHYS_SLOTS = 0x4DB000, 0x180, 16
PHYS_WORDS = PHYS_STRIDE // 4
PHYS_ACC_WORD = 0x174 // 4
PHYS_DATA = CAVE + 0xD000       # N[16][96], M[16][96] = 0x3000 bytes, up to CAVE_MEMSZ


def emit_phys_smooth(a):
    def setup(tag):
        """t1 = record, t2 = N row, t7 = M row for index a1; branch to tag_skip if a1 >= 16."""
        a.bal(tag + "_pc")
        a.nop()
        a.label(tag + "_pc")
        here = a.labels[tag + "_pc"]
        a.sltiu("t0", "a1", PHYS_SLOTS)
        a.beq("t0", "zero", tag + "_call")
        a.nop()
        a.ori("t0", "zero", PHYS_STRIDE)
        a.sll("t3", "a1", 7)
        a.sll("t4", "a1", 8)
        a.addu("t3", "t3", "t4")             # a1 * 0x180
        a.li32("t1", PHYS_REC - here)
        a.addu("t1", "t1", "ra")
        a.addu("t1", "t1", "t3")
        a.li32("t2", PHYS_DATA - here)
        a.addu("t2", "t2", "ra")
        a.addu("t2", "t2", "t3")
        a.li32("t7", PHYS_SLOTS * PHYS_STRIDE)
        a.addu("t7", "t7", "t2")
        return here

    def restore_loop(tag):
        a.ori("t3", "zero", PHYS_WORDS)
        a.label(tag)
        a.lw("t8", 0, "t7")
        a.beq("t8", "zero", tag + "_n")
        a.nop()
        a.lw("t5", 0, "t1")
        a.bne("t5", "t8", tag + "_c")
        a.nop()
        a.lw("t6", 0, "t2")
        a.sw("t6", 0, "t1")
        a.label(tag + "_c")
        a.sw("zero", 0, "t7")
        a.label(tag + "_n")
        a.addiu("t1", "t1", 4)
        a.addiu("t2", "t2", 4)
        a.addiu("t7", "t7", 4)
        a.addiu("t3", "t3", -1)
        a.bne("t3", "zero", tag)
        a.nop()

    # --- 0xC5864 call site: restore exact state, then run it
    a.label("phys_outer")
    a.addiu("sp", "sp", -16)
    a.sw("ra", 0, "sp")
    a.sw("a0", 4, "sp")
    a.sw("a1", 8, "sp")
    here = setup("phys_o")
    restore_loop("phys_o_rs")
    a.label("phys_o_call")
    a.bal("phys_o_pc2")
    a.nop()
    a.label("phys_o_pc2")
    h2 = a.labels["phys_o_pc2"]
    a.li32("t9", 0xC5864 - h2)
    a.addu("t9", "t9", "ra")
    a.lw("a0", 4, "sp")
    a.jalr("t9")
    a.lw("a1", 8, "sp")
    a.lw("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)

    # --- 0xC60B8 step call: restore, snapshot, step, write midpoints
    a.label("phys_step")
    a.addiu("sp", "sp", -16)
    a.sw("ra", 0, "sp")
    a.sw("a0", 4, "sp")
    a.sw("a1", 8, "sp")
    setup("phys_s")
    a.sw("t1", 12, "sp")
    restore_loop("phys_s_rs")
    a.lw("t1", 12, "sp")                     # snapshot record -> N
    a.addiu("t1", "t1", 0)
    a.lw("a1", 8, "sp")
    setup("phys_s2")
    a.ori("t3", "zero", PHYS_WORDS)
    a.label("phys_snap")
    a.lw("t5", 0, "t1")
    a.sw("t5", 0, "t2")
    a.addiu("t1", "t1", 4)
    a.addiu("t2", "t2", 4)
    a.addiu("t3", "t3", -1)
    a.bne("t3", "zero", "phys_snap")
    a.nop()
    a.label("phys_s_call")
    a.label("phys_s2_call")
    a.bal("phys_s_pc2")
    a.nop()
    a.label("phys_s_pc2")
    h3 = a.labels["phys_s_pc2"]
    a.li32("t9", 0xC60B8 - h3)
    a.addu("t9", "t9", "ra")
    a.lw("a0", 4, "sp")
    a.jalr("t9")
    a.lw("a1", 8, "sp")
    a.lw("a1", 8, "sp")
    a.sltiu("t0", "a1", PHYS_SLOTS)
    a.beq("t0", "zero", "phys_s_ret")
    a.nop()
    setup("phys_s3")
    a.label("phys_s3_call")
    a.lui("t0", 0x3F00)
    a.mtc1("t0", 1)
    a.lui("t0", 0x4080)
    a.mtc1("t0", 2)
    a.ori("t3", "zero", PHYS_WORDS)
    a.label("phys_mid")
    a.lw("t5", 0, "t2")                      # old
    a.lw("t6", 0, "t1")                      # new
    a.sw("t6", 0, "t2")
    a.sw("zero", 0, "t7")
    a.ori("t0", "zero", PHYS_WORDS - PHYS_ACC_WORD)
    a.beq("t3", "t0", "phys_mid_n")          # accumulator word: leave exact
    a.nop()
    a.beq("t5", "t6", "phys_mid_n")
    a.nop()
    for k, x in enumerate(("t5", "t6")):
        ok = "phys_mid_ok%d" % k
        a.sll("t8", x, 1)
        a.beq("t8", "zero", ok)
        a.srl("t8", "t8", 24)
        a.addiu("t8", "t8", -0x66)
        a.sltiu("t8", "t8", 0x8A - 0x66 + 1)
        a.beq("t8", "zero", "phys_mid_n")
        a.nop()
        a.label(ok)
    a.mtc1("t5", 3)
    a.mtc1("t6", 4)
    a.sub_s(5, 4, 3)
    a.abs_s(5, 5)
    a.c_le_s(5, 2)
    a.nop()
    a.bc1f("phys_mid_n")
    a.nop()
    a.add_s(6, 3, 4)
    a.mul_s(6, 6, 1)
    a.mfc1("t8", 6)
    a.beq("t8", "zero", "phys_mid_n")
    a.nop()
    a.sw("t8", 0, "t1")
    a.sw("t8", 0, "t7")
    a.label("phys_mid_n")
    a.addiu("t1", "t1", 4)
    a.addiu("t2", "t2", 4)
    a.addiu("t7", "t7", 4)
    a.addiu("t3", "t3", -1)
    a.bne("t3", "zero", "phys_mid")
    a.nop()
    a.label("phys_s_ret")
    a.lw("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)


def emit_cam_lerp(a):
    a.label("cam_base_one")
    a.lui("a0", 0x3F80)
    a.jr("ra")
    a.mtc1("a0", 0)
    # Pose camera 0x15C608 (win poses, point celebrations; DOAX2's 82430628): eases the orbit with
    # the posing girl's whole animation frame (+0x50, 0x167398) at 0x15C984. The fraction comes from
    # 0x1673C8(girl = caller's 0x40(sp), slot 0), then the shared lerp below.
    a.label("cam_pose")
    a.lbu("t8", 0x40, "sp")
    a.addiu("sp", "sp", -32)
    a.sw("ra", 0, "sp")
    for k, r in enumerate(("a0", "a1", "a2", "a3", "t0")):
        a.sw(r, 4 + 4 * k, "sp")
    a.bal("cam_pose_pc")
    a.nop()
    a.label("cam_pose_pc")
    a.li32("t9", 0x1673C8 - a.labels["cam_pose_pc"])
    a.addu("t9", "t9", "ra")
    a.move("a0", "t8")
    a.jalr("t9")
    a.move("a1", "zero")
    a.mfc1("t8", 0)
    a.lw("ra", 0, "sp")
    for k, r in enumerate(("a0", "a1", "a2", "a3", "t0")):
        a.lw(r, 4 + 4 * k, "sp")
    a.b("cam_common")
    a.addiu("sp", "sp", 32)

    a.label("cam_s2")
    a.lw("t8", 0x80, "s2")
    a.b("cam_common")
    a.nop()
    a.label("cam_s1")
    a.lw("t8", 0x60, "s1")
    a.label("cam_common")
    a.addiu("sp", "sp", -48)
    a.sw("ra", 0, "sp")
    for k, r in enumerate(("a0", "a1", "a2", "a3", "t0")):
        a.sw(r, 4 + 4 * k, "sp")
    a.mtc1("t8", 12)
    a.trunc_w_s(13, 12)
    a.cvt_s_w(13, 13)
    a.sub_s(14, 12, 13)                  # frac(elapsed)
    a.swc1(14, 24, "sp")
    a.bal("cam_pc")
    a.nop()
    a.label("cam_pc")
    here = a.labels["cam_pc"]
    a.li32("t9", 0x13B0D8 - here)
    a.addu("t9", "t9", "ra")
    a.sw("t9", 28, "sp")
    a.jalr("t9")                         # args a0..t0 untouched so far
    a.nop()
    a.swc1(0, 32, "sp")
    a.lwc1(14, 24, "sp")
    a.mtc1("zero", 15)
    a.c_eq_s(14, 15)
    a.nop()
    a.bc1t("cam_whole")
    a.nop()
    a.lw("a0", 4, "sp")
    a.addiu("a0", "a0", 1)
    a.lw("a1", 8, "sp")
    a.lw("a2", 12, "sp")
    a.lw("a3", 16, "sp")
    a.lw("t0", 20, "sp")
    a.lw("t9", 28, "sp")
    a.jalr("t9")
    a.nop()
    a.lwc1(12, 32, "sp")
    a.sub_s(13, 0, 12)
    a.lwc1(14, 24, "sp")
    a.mul_s(13, 13, 14)
    a.add_s(0, 12, 13)
    a.b("cam_ret")
    a.nop()
    a.label("cam_whole")
    a.lwc1(0, 32, "sp")
    a.label("cam_ret")
    a.lw("ra", 0, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 48)


# Pool Hopping HUD: the timer (0xD0704) and the hop numbers (0xD1194, up to 8) are drawn by the text
# routine 0x2FCE0 inside the logic, which runs every other update, so they showed on alternate
# frames. ph_text keeps a copy of each text record and its string, and the skipped update draws the
# copies again; no game state moves. Cave data between the Pool Hopping and physics blocks:
# +0 count, entries from +0x10: record 0x28 bytes, then its string (0x20 bytes).
PH_TEXT = CAVE + 0xCD00
PH_TEXT_MAX, PH_TEXT_REC, PH_TEXT_STR = 9, 0x28, 0x20


def emit_ph_text(a):
    a.label("ph_text")
    a.move("t9", "ra")
    a.bal("ph_text_pc")
    a.nop()
    a.label("ph_text_pc")
    here = a.labels["ph_text_pc"]
    a.li32("t8", PH_TEXT - here)
    a.addu("t8", "t8", "ra")
    a.lw("t0", 0, "t8")
    a.sltiu("t1", "t0", PH_TEXT_MAX)
    a.beq("t1", "zero", "ph_text_draw")
    a.nop()
    a.sll("t2", "t0", 6)
    a.sll("t3", "t0", 3)
    a.addu("t2", "t2", "t3")             # entry size 0x48
    a.addu("t2", "t2", "t8")
    a.addiu("t2", "t2", 0x10)
    for k in range(0, PH_TEXT_REC, 4):
        a.lw("t5", k, "a0")
        a.sw("t5", k, "t2")
    a.lw("t6", 4, "a0")
    a.beq("t6", "zero", "ph_text_count")
    a.nop()
    for k in range(0, PH_TEXT_STR, 4):   # byte copy: the string may not be word aligned
        for b in range(4):
            a.lbu("t5", k + b, "t6")
            a.sb("t5", PH_TEXT_REC + k + b, "t2")
    a.addiu("t5", "t2", PH_TEXT_REC)
    a.sw("t5", 4, "t2")
    a.label("ph_text_count")
    a.addiu("t0", "t0", 1)
    a.sw("t0", 0, "t8")
    a.label("ph_text_draw")
    a.li32("t8", 0x2FCE0 - here)
    a.addu("t8", "t8", "ra")
    a.move("ra", "t9")
    a.jr("t8")
    a.nop()


def emit_pool_hop(a, hud=False):
    a.label("ph_wrap")
    a.addiu("sp", "sp", -48)
    a.sw("ra", 0, "sp")
    for k, r in enumerate(("s0", "s1", "s2", "s3", "s4")):
        a.sw(r, 4 + 4 * k, "sp")
    a.move("s0", "a0")
    a.bal("ph_pc")
    a.nop()
    a.label("ph_pc")
    here = a.labels["ph_pc"]
    a.move("s1", "ra")
    a.li32("s2", PH_DATA - here)
    a.addu("s2", "s2", "s1")
    a.li32("s3", PAD - here)
    a.addu("s3", "s3", "s1")
    a.lw("t0", 0, "s2")
    a.xori("t0", "t0", 1)
    a.sw("t0", 0, "s2")
    a.beq("t0", "zero", "ph_skip")
    a.nop()

    def pad_loop(tag, body):
        a.move("t1", "s3")
        a.move("t2", "s2")
        a.ori("t3", "zero", 6)
        a.label(tag + "_r")
        a.ori("t4", "zero", 4)
        a.label(tag + "_f")
        body()
        a.addiu("t1", "t1", 4)
        a.addiu("t2", "t2", 4)
        a.addiu("t4", "t4", -1)
        a.bne("t4", "zero", tag + "_f")
        a.nop()
        a.addiu("t1", "t1", 12)          # next 28-byte record
        a.addiu("t3", "t3", -1)
        a.bne("t3", "zero", tag + "_r")
        a.nop()

    def block_loop(tag, body):
        """For each block: t1 = word, t2 = N entry, t7 = M entry; body; advance."""
        nbuf = 0
        for b, (lo, hi) in enumerate(PH_BLOCKS):
            n = (hi - lo) // 4
            lab = "%s_%d" % (tag, b)
            a.li32("t1", lo - here)
            a.addu("t1", "t1", "s1")
            a.li32("t2", 0x100 + 4 * nbuf)
            a.addu("t2", "t2", "s2")
            a.li32("t0", 4 * PH_N)
            a.addu("t7", "t2", "t0")
            a.li32("t3", n)
            a.label(lab)
            body(lab + "_next")
            a.label(lab + "_next")
            a.addiu("t1", "t1", 4)
            a.addiu("t2", "t2", 4)
            a.addiu("t7", "t7", 4)
            a.addiu("t3", "t3", -1)
            a.bne("t3", "zero", lab)
            a.nop()
            nbuf += n

    def merge():
        a.lw("t5", 4, "t1")
        a.sw("t5", 0x80, "t2")           # saved
        a.lw("t6", 0x10, "t2")           # acc from the skipped update
        a.or_("t5", "t5", "t6")
        a.sw("t5", 4, "t1")
        a.sw("zero", 0x10, "t2")
    pad_loop("ph_pad", merge)

    def snap(nxt):
        a.lw("t5", 0, "t1")
        a.sw("t5", 0, "t2")
    block_loop("ph_snap", snap)
    if hud:
        a.li32("t0", PH_TEXT - here)
        a.addu("t0", "t0", "s1")
        a.sw("zero", 0, "t0")
    a.li32("t9", 0xD1378 - here)
    a.addu("t9", "t9", "s1")
    a.jalr("t9")
    a.move("a0", "s0")

    def unmerge():
        a.lw("t5", 0x80, "t2")
        a.sw("t5", 4, "t1")
    pad_loop("ph_unpad", unmerge)
    a.lui("t0", 0x3F00)
    a.mtc1("t0", 1)                      # f1 = 0.5
    a.lui("t0", 0x4080)
    a.mtc1("t0", 2)                      # f2 = 4.0

    def mid(nxt):
        a.lw("t5", 0, "t2")              # old
        a.lw("t6", 0, "t1")              # new
        a.sw("t6", 0, "t2")
        a.sw("zero", 0, "t7")
        a.beq("t5", "t6", nxt)
        a.nop()
        for k, x in enumerate(("t5", "t6")):
            ok = "%s_ok%d" % (nxt, k)
            a.sll("t8", x, 1)
            a.beq("t8", "zero", ok)      # +-0.0
            a.srl("t8", "t8", 24)        # exponent (delay slot)
            a.addiu("t8", "t8", -0x66)   # ~1e-7 .. ~1.6e4
            a.sltiu("t8", "t8", 0x8A - 0x66 + 1)
            a.beq("t8", "zero", nxt)
            a.nop()
            a.label(ok)
        a.mtc1("t5", 3)
        a.mtc1("t6", 4)
        a.sub_s(5, 4, 3)
        a.abs_s(5, 5)
        a.c_le_s(5, 2)
        a.nop()
        a.bc1f(nxt)
        a.nop()
        a.add_s(6, 3, 4)
        a.mul_s(6, 6, 1)
        a.mfc1("t8", 6)
        a.beq("t8", "zero", nxt)
        a.nop()
        a.sw("t8", 0, "t1")
        a.sw("t8", 0, "t7")
    block_loop("ph_sm", mid)
    a.b("ph_ret")
    a.nop()

    a.label("ph_skip")

    def accumulate():
        a.lw("t5", 4, "t1")
        a.lw("t6", 0x10, "t2")
        a.or_("t6", "t6", "t5")
        a.sw("t6", 0x10, "t2")
    pad_loop("ph_acc", accumulate)

    def restore(nxt):
        a.lw("t8", 0, "t7")
        a.beq("t8", "zero", nxt)
        a.nop()
        a.lw("t5", 0, "t1")
        a.bne("t5", "t8", nxt + "_clr")  # someone else wrote it: keep theirs
        a.nop()
        a.lw("t6", 0, "t2")
        a.sw("t6", 0, "t1")
        a.label(nxt + "_clr")
        a.sw("zero", 0, "t7")
    block_loop("ph_rs", restore)
    if hud:
        a.li32("s3", PH_TEXT - here)
        a.addu("s3", "s3", "s1")
        a.ori("s4", "zero", 0)
        a.label("ph_redraw")
        a.lw("t0", 0, "s3")
        a.sltu("t1", "s4", "t0")
        a.beq("t1", "zero", "ph_ret")
        a.nop()
        a.sll("t2", "s4", 6)
        a.sll("t3", "s4", 3)
        a.addu("a0", "t2", "t3")
        a.addu("a0", "a0", "s3")
        a.addiu("a0", "a0", 0x10)
        a.li32("t9", 0x2FCE0 - here)
        a.addu("t9", "t9", "s1")
        a.jalr("t9")
        a.ori("a1", "zero", 1)
        a.addiu("s4", "s4", 1)
        a.b("ph_redraw")
        a.nop()

    a.label("ph_ret")
    a.lw("ra", 0, "sp")
    for k, r in enumerate(("s0", "s1", "s2", "s3", "s4")):
        a.lw(r, 4 + 4 * k, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 48)


def emit_generic_recorder(a):
    a.label("recorder")
    a.addiu("sp", "sp", -16)
    a.sw("ra", 0, "sp")
    a.sw("s0", 4, "sp")
    a.bal("recorder_pc")
    a.nop()
    a.label("recorder_pc")
    here = a.labels["recorder_pc"]
    a.sw("ra", 12, "sp")
    a.li32("s0", REC2_ABS)                   # absolute, outside the module
    a.lw("t0", 4, "s0")
    a.beq("t0", "zero", "recorder_call")
    a.nop()
    a.lw("t2", 8, "s0")
    a.andi("t3", "t2", REC2_ENTRIES - 1)
    a.sll("t3", "t3", 6)
    a.addu("t3", "t3", "s0")                   # entry - 0x100
    a.li32("t0", FRAME_COUNTER - here)
    a.lw("t1", 12, "sp")
    a.addu("t0", "t0", "t1")
    a.lw("t0", 0, "t0")
    a.sw("t0", 0x100, "t3")
    a.lw("t4", 0, "s0")
    a.move("t7", "s0")
    a.move("t8", "t3")
    a.label("recorder_loop")
    a.beq("t4", "zero", "recorder_next")
    a.nop()
    a.lw("t5", 0x40, "t7")
    a.lw("t6", 0, "t5")
    a.sw("t6", 0x104, "t8")
    a.addiu("t7", "t7", 4)
    a.addiu("t8", "t8", 4)
    a.b("recorder_loop")
    a.addiu("t4", "t4", -1)
    a.label("recorder_next")
    a.addiu("t2", "t2", 1)
    a.sw("t2", 8, "s0")
    a.label("recorder_call")
    a.li32("t9", 0x360 - here)
    a.lw("t0", 12, "sp")
    a.addu("t9", "t9", "t0")
    a.jalr("t9")
    a.nop()
    a.lw("ra", 0, "sp")
    a.lw("s0", 4, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)


def emit_recorder(a):
    a.label("recorder")
    a.addiu("sp", "sp", -16)
    a.sw("ra", 0, "sp")
    a.sw("s0", 4, "sp")
    a.sw("s1", 8, "sp")
    a.bal("recorder_pc")
    a.nop()
    a.label("recorder_pc")
    here = a.labels["recorder_pc"]
    a.sw("ra", 12, "sp")
    a.li32("s0", PLAYER_RECORDS - here)
    a.addu("s0", "s0", "ra")
    a.li32("s1", REC_BUF - here)
    a.addu("s1", "s1", "ra")
    a.li32("t0", FRAME_COUNTER - here)
    a.addu("t0", "t0", "ra")
    a.lw("t0", 0, "t0")
    a.sw("t0", 0x40, "s1")
    a.li32("t0", VB_ATTACK_BLOCK - here)
    a.addu("t0", "t0", "ra")
    a.lbu("t0", 0x19, "t0")
    a.sw("t0", 0x44, "s1")
    for p in range(4):
        o = 292 * p
        a.lbu("t0", o + 0x3E, "s0")
        a.lbu("t1", o + 0x4A, "s0")
        a.sll("t1", "t1", 8)
        a.or_("t0", "t0", "t1")
        a.lhu("t1", o + 0x40, "s0")
        a.sll("t1", "t1", 16)
        a.or_("t0", "t0", "t1")
        a.sw("t0", 0x48 + 4 * p, "s1")
    for k in range(1, 6):
        a.lw("t0", 0x40 + 4 * k, "s1")
        a.lw("t1", 0x20 + 4 * k, "s1")
        a.bne("t0", "t1", "recorder_append")
        a.nop()
    a.b("atk_check")
    a.nop()
    a.label("recorder_append")
    a.lw("t2", 0, "s1")
    a.andi("t3", "t2", REC_ENTRIES - 1)
    a.sll("t3", "t3", 5)
    a.addu("t3", "t3", "s1")
    for k in range(8):
        a.lw("t0", 0x40 + 4 * k, "s1")
        a.sw("t0", 0x80 + 4 * k, "t3")
        a.sw("t0", 0x20 + 4 * k, "s1")
    a.addiu("t2", "t2", 1)
    a.sw("t2", 0, "s1")
    a.label("atk_check")
    # attack trace: first player with +0x4A != 0
    a.li32("t4", ATK_BUF - here)
    a.lw("t0", 12, "sp")
    a.addu("t4", "t4", "t0")
    for p in range(4):
        a.lbu("t0", 292 * p + 0x4A, "s0")
        a.bne("t0", "zero", "atk_p%d" % p)
        a.nop()
    a.b("recorder_call")
    a.nop()
    for p in range(4):
        a.label("atk_p%d" % p)
        a.addiu("t5", "s0", 292 * p)          # this player's record
        a.b("atk_write")
        a.ori("t6", "zero", p)
    a.label("atk_write")
    a.lw("t2", 0, "t4")
    a.andi("t3", "t2", ATK_ENTRIES - 1)
    a.sll("t1", "t3", 6)                       # *64
    a.sll("t3", "t3", 5)                       # *32
    a.addu("t3", "t3", "t1")                   # *96
    a.addu("t3", "t3", "t4")
    a.lw("t0", 0x40, "s1")
    a.sw("t0", 0x10, "t3")                     # FC
    a.lw("t0", 0x44, "s1")
    a.sw("t0", 0x14, "t3")                     # jump byte
    a.sw("t6", 0x18, "t3")                     # player
    a.sw("zero", 0x1C, "t3")
    a.li32("t7", VB_BALL - here)
    a.lw("t0", 12, "sp")
    a.addu("t7", "t7", "t0")
    for k, off in enumerate((0, 4, 8)):
        a.lw("t0", off, "t7")
        a.sw("t0", 0x20 + 4 * k, "t3")
    a.lui("t0", 0)
    a.ori("t0", "t0", 0x1CC4)
    a.addu("t7", "t7", "t0")
    a.lw("t0", 0, "t7")
    a.sw("t0", 0x2C, "t3")                     # ball clock
    for k in range(16):
        a.lw("t0", 4 * k, "t5")
        a.sw("t0", 0x30 + 4 * k, "t3")
    a.addiu("t2", "t2", 1)
    a.sw("t2", 0, "t4")
    a.label("recorder_call")
    a.li32("t9", 0x360 - here)
    a.lw("t0", 12, "sp")
    a.addu("t9", "t9", "t0")
    a.jalr("t9")
    a.nop()
    a.lw("ra", 0, "sp")
    a.lw("s0", 4, "sp")
    a.lw("s1", 8, "sp")
    a.jr("ra")
    a.addiu("sp", "sp", 16)


PATCHSETS = {
    "gate-only": lambda: GATE,
    "gate-speed": lambda: GATE + SPEED_HALF,
    "gate-speed-scene": lambda: GATE + SPEED_HALF + SCENE_DT_HALF,
    "vb2": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + vb_ai_wait_hold(),
    "vb2d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + vb_ai_wait_hold(recorder=True),
    "anim1": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(),
    # 30 FPS reference: the unmodified game plus the generic recorder (same cave address)
    "ref30d": lambda: vb_ai_wait_hold(recorder="generic", vb=False),
    "ph2": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(pool=True),
    "ph2d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(recorder="generic", pool=True),
    "cam1": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(pool=True, cam=True),
    "cam1d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(recorder="generic", pool=True, cam=True),
    # playtest build 2026-11-01: anim + Pool Hopping + camera interp/durations + body physics + vb holds
    "pt3": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + vb_ai_wait_hold(pool=True, cam=True, phys=True, casino=True),
    "pt3d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + vb_ai_wait_hold(recorder="generic", pool=True, cam=True, phys=True, casino=True),
    "pt4": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + vb_ai_wait_hold(pool=True, cam=True, phys=True, casino=True, anim_clamp=True),
    "pt6": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + CASINO_ODDS_X10 + vb_ai_wait_hold(pool=True, cam=True, phys=True, casino="ui", anim_clamp=True),
    "pt12": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + CASINO_ODDS_X10 + vb_ai_wait_hold(pool=True, cam=True, phys=True, casino="ui", anim_clamp=True, ph_hud=True, tex_guard=True),
    "pt4d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + CASINO_ANIM_HALF + vb_ai_wait_hold(recorder="generic", pool=True, cam=True, phys=True, casino=True, anim_clamp=True),
    "bisA": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(pool=True, cam=True),
    "bisB": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(pool=True, cam=True, skip_targets=(0xA4BC4,)),
    "phys1d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(recorder="generic", pool=True, cam=True, phys=True),
    "ph1d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + POOL_HOP_PACE + vb_ai_wait_hold(recorder="generic"),
    "anim1d": lambda: GATE + SPEED_HALF + SCENE_DT_HALF + ANIM_FRAC + vb_ai_wait_hold(recorder="generic"),
}


def append_cave_segment(data, words, memsz=0):
    """Append words as PT_LOAD segment 2 at vaddr CAVE and move the program headers to the end."""
    phoff, = struct.unpack_from("<I", data, 0x1C)
    phnum, = struct.unpack_from("<H", data, 0x2C)
    if phnum != 2:
        raise SystemExit("expected 2 program headers, found %d" % phnum)
    phdrs = data[phoff: phoff + 32 * phnum]
    seg1 = struct.unpack_from("<8I", phdrs, 32)
    if CAVE < seg1[2] + seg1[5]:
        raise SystemExit("cave overlaps segment 1")
    out = bytearray(data)
    while len(out) % 16:
        out.append(0)
    code_off = len(out)
    for w in words:
        out += struct.pack("<I", w)
    while len(out) % 16:
        out.append(0)
    new_phoff = len(out)
    out += phdrs
    size = 4 * len(words)
    memsz = max(memsz, size)
    out += struct.pack("<8I", 1, code_off, CAVE, 0, size, memsz, 7, 16)  # PT_LOAD, RWX (recorder buffer)
    struct.pack_into("<I", out, 0x1C, new_phoff)
    struct.pack_into("<H", out, 0x2C, 3)
    return bytes(out)


def apply(data, patches):
    sys.path.insert(0, os.path.join(ROOT, "tools", "executable"))
    from relocs import relocs
    rel = relocs(data)
    cave = sorted((a, new) for a, orig, new, note in patches if a >= CAVE)
    patches = [p for p in patches if p[0] < CAVE]
    data = bytearray(data)
    for addr, orig, new, note in patches:
        if addr in rel and rel[addr][0] == 4 and (orig >> 26) == 3 and (new >> 26) == 3:
            pass  # J26: the loader adds the module base to any module-relative jal target
        elif addr in rel:
            raise SystemExit("%08X is relocated (type %d); patch a different word" % (addr, rel[addr][0]))
        off = addr + SEG_FILE_OFF
        cur, = struct.unpack_from("<I", data, off)
        if cur != orig:
            raise SystemExit("%08X: expected %08X, found %08X (%s)" % (addr, orig, cur, note))
        struct.pack_into("<I", data, off, new)
        print("%08X: %08X -> %08X  %s" % (addr, orig, new, note))
    if cave:
        if [a for a, _ in cave] != [CAVE + 4 * i for i in range(len(cave))]:
            raise SystemExit("cave words must be contiguous from CAVE")
        memsz = CAVE_MEMSZ
        data = append_cave_segment(bytes(data), [w for _, w in cave], memsz)
        print("cave segment: %d words at %08X" % (len(cave), CAVE))
    return bytes(data)


def main():
    name = sys.argv[1]
    data = open(SRC_EBOOT, "rb").read()
    if hashlib.sha256(data).hexdigest() != SRC_EBOOT_SHA:
        raise SystemExit("source EBOOT hash mismatch")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    out = apply(data, PATCHSETS[name]())
    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    eboot = os.path.join(ROOT, "build", "EBOOT.%s.BIN" % name)
    open(eboot, "wb").write(out)
    print("EBOOT", eboot, hashlib.sha256(out).hexdigest())
    if "--iso" in sys.argv:
        iso = os.path.join(ROOT, "build", "DOAP-%s.iso" % name)
        if os.path.exists(iso):
            os.remove(iso)
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "iso", "patch_iso.py"),
                               SRC_ISO, iso, "--shrink-file", "--grow-in-slack",
                               "--replace", "PSP_GAME/SYSDIR/EBOOT.BIN=" + eboot])
        print("ISO", iso)


if __name__ == "__main__":
    main()
