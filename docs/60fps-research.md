# Engineering notes

Notes kept during development. Build names such as pt3 and pt5 are internal test builds.

# DOA Paradise (ULUS-10521) 60 FPS research

Target: PPSSPP (tested v1.20.4). Approach mirrors DOAX2 native-60: run the main
loop at 60 Hz and halve the game's own base speed, then fix whatever does not
read that speed (hardcoded per-update steps), one domain at a time.

## Identity

- Retail `PSP_GAME/SYSDIR/EBOOT.BIN`: encrypted `~PSP`, 5,225,872 bytes. `BOOT.BIN` is all zeros.
- Rebuild baseline: PPSSPP dump (`DumpFileTypes = 1`) at `research/EBOOT.ppsspp-dump.BIN`,
  SHA-256 `5dc782c28239d69d8f6587a96322a02c52ab4c9ba80af2e0496f1aa717375145`.
  Module `doap`, relocatable PRX, loaded by PPSSPP at `0x08804000`.
- Segment 0 (text/rodata/data) vaddr 0 at file offset 0x74; segment 1 (bss etc.) vaddr `0x44FEB0`.
  Code `lui 0 / lui 2 ...` pairs relocated with addrbase 1 point into segment 1
  (e.g. `lui 2 + 0x38B4` -> module `0x473764`).
- **Do not patch relocated words.** 87,050 relocations; the loader rewrites the low 16 bits
  of HI16/LO16 words. `tools/executable/relocs.py` checks a site; `tools/build_patch.py`
  refuses relocated patch sites.

## Frame gate (confirmed)

- Main loop `0x1D20`: `0x1FD4` (input/pre), `0x2148` (update+draw), `0x2008` (flip + frame counter).
- Flip `0x360`: `sceDisplayWaitVblankStartMulti(ctx, [0x289F00] + 1)` via wrapper `0x20466C`.
- `[0x289F00]` defaults to 1 (2 vblanks = 30 FPS). Setter `0x98`: movie start `0x1D03A8`
  sets 0 (FMVs already run at 1 vblank), movie stop `0x1D0380` sets 1.
- Patch: `0x380 addiu a1,v0,1 -> addiu a1,zero,1`. Verified: frame counter
  `0x450E64` advances 60.1/s (clean: 29.7/s), PPSSPP shows 60 FPS in menus.

## Global game speed (verified live)

- Global per-update step float at module `0x473764` (seg1 `0x238B4`) = base speed
  (`0x472D60`, cached `0x472D5C`) x slow factor (`0x472D68`, cached `0x472D64`). All 1.0 at runtime.
- Read by ~100 update sites (`tools/executable/xref.py EBOOT 238B4 238B8`).
- Init `0x3D48` is the only base-speed writer (via `0x3D94`). Slow factor setters:
  `0x3DA8` (callers 0x1A44, 0xBC548, 0x18AFE4), reset-to-1.0 `0x3DB8`
  (callers 0xB92B4, 0xBC45C, 0x1869E0, 0x18B02C). Recompute `0x3DF0` each frame from `0x2F18`.
- Base-speed getter `0x3DD8` has 23 callers (0x13DB08, 0x147D0C, ...).
- Patch `SPEED_HALF`: init loads 0.5 as the base speed and keeps the slow factor at 1.0.
- Verified 2026-10-27 (island tutorial, same scene at base 0.5 vs 1.0 written live to `0x472D60`):
  frame-unit clocks step 0.5 vs 1.0, i.e. most systems read the global step.

## Known domains that bypass the global step

- Scene-graph animation: `0xECE48` passes a literal 1.0 (or 0 when paused, `0xF1C`) as dt to
  `0xEDD84 -> 0x1EC350`, which does `time += dt` at `0x1EC388` (anim record +0x264,
  time-unit table at `0x287D30`). `0xECE48` is the only caller of `0xEDD84`, so one patch
  (`SCENE_DT_HALF`, `0xECE70` literal 1.0 -> 0.5) covers it.
- Verified: on the island screen the same object is stepped twice per frame (from `0x220C` and
  `0x246C` in `0x21C4`). Clean: 2 x 1.0 x 30 Hz = 60 units/s; patched: 2 x 0.5 x 60 Hz = 60 units/s.
  (Title/char select step once per frame via `0xEC814`.) `tools/bplog.py 1EC388 N` logs dt per call.
- The fps/time-unit table at `0x287D38` (3600, 60, 1, ms, 15/24/25/30/45/50/60 fps pairs)
  is the animation time-unit conversion, not a frame limiter.

## Tooling

- `tools/ppsspp/` portable PPSSPP with WebSocket debugger on `ws://127.0.0.1:45678/debugger`.
- `tools/ppdbg.py` (r32/w32/rate), `tools/press.py` (pad input), `tools/memwatch.py`
  (write breakpoint + backtrace), `tools/framediff.py` (per-update RAM snapshots, lists
  constant-step clocks), `tools/winshot.ps1` (window capture even when covered).
- `tools/build_patch.py PATCHSET --iso` builds `build/DOAP-PATCHSET.iso`.

## Builds

| Patchset | Contents | Status |
|---|---|---|
| `gate-speed` | gate + base speed 0.5 | 60 FPS in PPSSPP 1.20.4 (title, island) |
| `gate-speed-scene` | + scene-graph dt 0.5 | current test build; scene clocks match clean wall-clock |

## Next

0. User reported 30 FPS with `gate-speed`, while the same ISO reads 60 here. Check for a
   savestate made on the clean ISO (restores the old code) or other PPSSPP settings.
1. Play `gate-speed-scene` vs clean: note what runs fast (likely menus/scene-graph anims, UI
   timers, minigames with integer counters) or slow.
2. A/B clocks live: one session, toggle `0x380` and `[0x472D60]` through the debugger,
   compare `framediff` rates per second instead of per update.
3. Halve the `0xECE48` literal dt and any other hardcoded per-update steps found.

## Volleyball (2026-10-27, in progress)

User report on `gate-speed-scene`: passes/sets and spikes miss exactly like DOAX2 at 60 Hz.

- Gameplay runs on PSP threads started through `0x564` (event-flag wait, then a function
  pointer): volleyball thread root `0xBAC54` (module ~`0xA8000-0xC4000`), AI thread `0xA2300`.
  Spike slow-motion uses the title's own slow factor (`0xBC45C` reset, `0xBC548` set).
- Per-player records: player `seg1 0x22088 + p*292` (RAM `0x08C75F38`; +0x3E state byte,
  +0x40 motion id), AI script `seg1 0x829C0 + p*92` (RAM `0x08CD6870`), 56-byte
  `seg1 0x82680 + p*56`, per-player update `0xC043C(p)` with 24-byte records at `seg1 0x8A788`
  (+0xC queued-action delay set to 6 at `0xC0984`; rarely used).
- AI script VM `0xA7F78(p)`: wait countdowns u16 +0xA/+0xC/+0xE decremented once per update
  in `0xA9AD4` (call `0xA82E4`) and `0xA9CD0` (call `0xA83CC`); at zero the planned action
  starts via `0xA41CC(p, action)`. Phase timer +0x48 (`0xA9670`, call `0xA84EC`) counts up
  to 151. Same design as DOAX2's AI scheduler record (also 92 bytes, +10/+12/+14).
- `vb1` patchset: code cave in an appended third PT_LOAD segment at vaddr `0x532A00`
  (program headers moved to the file end; EBOOT grows ~800 bytes inside its ISO slack:
  extent 688, next file at 3248, `patch_iso.py --grow-in-slack`). It wraps the three routines and undoes exact one-step changes on odd updates
  (countdowns never held 1 -> 0). Position independent (`bal` + offsets, `jalr`), since cave
  words have no relocations; redirected `jal`s keep their J26 relocation.
- Do not use "unreferenced" functions as caves: `0x18D888` and every other function with no
  code reference is a C++ static initializer listed in `.cplinit` (segment 1, file offset =
  vaddr + 0x70, not + 0x74). The first vb1 ISO overwrote one and black-screened at boot.
  Savestates made before vb1 lack the cave segment: don't livepatch vb1 into them.
- Not yet proven to be the cause: with Kasumi idle the AI waits stayed 0 in short traces.
- Tools: `tools/livepatch.py on|off PATCHSET` (A/B in one session; JIT is invalidated),
  `tools/vblog.py NAME SECS` (non-pausing log breakpoints: ACT = 0xA41CC calls, REC/MOT =
  player state/motion changes, with FC), `tools/recordtrace.py`, `tools/watchset.py`,
  `tools/loadstate.ps1` (File > Load state via WM_COMMAND 40035; save is 40034).
- Save state slot 1 (user-made, `ULUS10521_1.01_0.ppst`): Niki Beach menu, X starts a match.
- User test of vb1: sets now work; spikes missed for both human and CPU.
- Spike cause candidate (vb2): attack state handler `0xBF31C(p)` (only call `0xBE9C0`, from the
  `0xBE8D4` state jump table) increments the jump-frames byte at seg1 `0x88140 + 0x19`
  (module `0x4D8009`) once per update while the attacker's state (+0x4A) is 2/3, then compares
  it with per-character thresholds from `0xBE86C` to pick the swing stage (+0x3B). Same byte
  and offset as DOAX2's jump-frames counter (block +25). vb2 wraps `0xBF31C` and undoes the +1
  on odd updates. (Hooking 0xBF4D4 directly is impossible: a new jal has no J26 relocation.)
- Debugger log breakpoints are unreliable for gameplay tracing (stale values from CPU log
  breakpoints, logging silently stops after code writes or breakpoint removal). Replaced by
  the in-game recorder in patchset `vb2d` (flip call `0x2010` -> cave): change log of player
  state/attack state/motion and the jump byte (`tools/recorder_dump.py`), plus a per-update
  attack trace with the attacker's record words +0..+0x3C and the ball (`tools/attack_dump.py`).
  `livepatch.py off vb2d` keeps the recorder hook (notes tagged `diag:`).
- 30 vs 60 comparison (rec30/rec60): attacker timing matches in seconds: jump byte 14 at
  +0.43 s after take-off (attack state +0x4A = 2), swing (state 3, motions 547/549/561)
  at +0.43..0.48 s both; approach 535 -> 567/569 -> 544 ~0.5 s before take-off both. One 60 Hz
  jump had the byte stuck at 1 and swung at +0.28 s. Attack states: 2 airborne, 3 swing, 4 landing.
- Ball: block seg1 `0x47C40` (module `0x497AF0`): x/y/z +0/+4/+8, analytic flight from clock
  +0x1CC4 (advanced by the global step in `0xB09A0`), drag +0x1CBC, y from `0xB31D0`.
- Open: spikes swing with the ball too high (user, human and CPU). Suspect jump physics
  per update; attack trace pending.

## User notes 2026-10-28 (not yet investigated)

- Relax scenes: girl animations look like 30 FPS while camera and scene run at 60.
- Pool Hopping: inputs register twice (as in DOAX2) and the partner moves at 2x speed.
- Volleyball: not yet retested on the latest build (vb2d recorder build pending a 60/30 run).
- Overall: girl animations appear to update at 30 FPS while the rest of the engine runs at
  double speed; same symptoms as DOAX2. PRIORITY: girl animation smoothness and correct
  overall engine speed, before further minigame work.

## 2026-10-30: animation fix, Pool Hopping pacing, harness

- **Girl animation (ANIM_FRAC, fixed).** Anim player 0x163F14 advances a float frame time (+0x34/+0x4C)
  by the global step but publishes trunc() frames (+0x38/+0x48); pose sampler 0x1685D0 used the truncated
  frame ((frame - s3) / 30 s), so pose + root motion changed every other update at 60 Hz. Patched
  0x1688F8..0x16891C to use the float time (clamp >= 1.0 kept). Verified: bone cache 0x4738E0 changes
  every update, smooth (relax + Pool Hopping). Shared by all girls (record table seg1 0xA9730).
- **Pool Hopping (ph2).** Logic thread 0xCE68C -> 0xD1378 counts updates (partner AI, phase timers,
  never reads the global step) -> 2x at 60. One tap = press + release edges -> two motions (the
  "double input"). Cave wrapper on the 0xCE6B4 call: logic every other update, pad edge fields
  (+4/+8/+0xC/+0x10 of 6 x 28-byte records at 0x471C50) from the skipped update ORed into the next run,
  DOAX2-style midpoint smoothing of the struct (0x4F4340-0x4F8C40) and camera struct (0x3C8A00-0x3C9700).
  Verified: partner motion times in seconds match 30 FPS (Kokoro run); girls move every update.
  Open: camera still steps at 30 Hz (its 30 Hz input not found yet); tap test after first hop inconclusive.
- **Still 30 Hz / 2x elsewhere:** module 0xC754C (per-girl, request bits +0x4C set to 3 on events, one
  parity per update; output 0x4DB000 updates every other update at 60) - likely facial/morph; dialog
  auto-advance timers count updates (2x); counter tables 0x4585A0 (wrap 360), 0x495F1C, 0x528B70.
- **Cave memory:** the game heap starts at the first 4 KB boundary after the module, so only memsz is
  safe. Cave now reserves 64 KB (CAVE_MEMSZ), data at CAVE+0x2000. The old vb2d recorder buffers at
  CAVE+0x1000+ overlapped live heap when loaded from older states (may explain odd vb2d sessions).
  Generic recorder (tools/rec.py) buffer at absolute 0x09D00000 (free in all snapshots), code at CAVE+0.
- **PPSSPP debugger:** memory writes do NOT invalidate JIT blocks (only a write over a block start takes
  effect); save/load after writes restores old code. So no live code patching: test each build by fresh
  boot + tools/mkstates.py (states hold code). 30 FPS reference = patchset ref30d (recorder only).
- Tools: harness.py (launch/save/load/shot/press/hold/stick), rec.py, mkstates.py, test_pool.py,
  ratecmp.py, track.py, padtrace.py, breakregs.py, hopcount.py.

## User test of DOAP-ph2.iso (2026-11-01)

- Girl animations 60 FPS, but **camera pans during win poses step at 30 FPS** in all minigames;
  check minigame intros for the same. Same as DOAX2 (docs/native60-timing-experiment.md
  "2026-10-01: pose camera"): the pose camera sequencer computed t = elapsed (int, ticks every other
  update) / total, and blended integer camera keys; DOAX2 fix ran the sequencer a second time with
  elapsed+1 on the lagging updates and wrote the mean of both outputs (camera at elapsed + 0.5).
  Look for the equivalent here: likely another trunc'd frame consumer (+0x38/+0x48 int frame or the
  0xC754C-style every-other-update module), plus the Pool Hopping camera struct 0x3C8xxx.
- Some relax-scene camera pans may still be 30 FPS.
- Some girl animations still slightly stuttery, mostly volleyball.
- Volleyball much better; spikes still miss sometimes (player and CPU), less often.

## 2026-11-01: cameras, physics, volleyball timers, casino (playtest build DOAP-pt3.iso)

- **Camera interpolation (cam1):** camera move commands ease with 0x13B0D8(trunc(elapsed), ...);
  elapsed advanced 0.5/update, so cameras stepped at 30 Hz (win poses, intros, relax, Pool Hopping).
  Cave wrapper on the 8 call sites (0x14EFC0: elapsed s2+0x80; 0x1504B0: s1+0x60) evaluates the
  easing at frame n and n+1 and lerps by frac(elapsed). Every camera tested moves every update.
- **Camera durations (cam2):** camera commands divided frame counts by base speed (0x3DD8 = 0.5) and
  advanced by the 0.5 step -> camera moves/waits 2x long (win pan 1.20 vs 2.40 units/s at 30).
  All 23 callers of 0x3DD8 (all camera code) -> stub returning 1.0. Pan speed matches 30 FPS.
- **Body physics (phys1):** 0xC5864 per girl: acc(+0x174) += step, 30 Hz step 0xC60B8 x trunc(acc),
  records 0x4DB000 + i*0x180 -> changed every other update (volleyball stutter). Midpoint smoothing at
  the step call 0xC5924, exact restore at the 0xC5864 call 0xCC094. Now changes every update.
- **Volleyball spikes:** 0xA4BC4 (second AI +0xA/+0xC countdown, called from 0xA8468/0xA7B68) was
  unheld -> CPU took off early, swung under a high ball. Held now: CPU spikes hit in every test rally.
- **Volleyball intro cuts:** intro script player 0xB9524 step timer 0x4DA610 (+1/update; shots 75)
  held -> 2.5 s shots like 30 FPS. NOT held: 0xA5754 time-in-state hub, 0xB7EB8 (crashed before the
  stack fix; untested since).
- **Crash lesson:** cave wrappers must keep sp 16-aligned; a 24-byte frame broke VFPU lv.q/sv.q on
  the stack in the attack code (PPSSPP "movaps" invalid access at 0xBBC44).
- **Casino:** dealer motion player 0x580F4 adds a literal 1.0/update (0x581B0) -> 0.5. Payouts x5:
  casino calls of set-money 0x1AAAF0 (18 sites, 0x564D8..0x79008) go through a wrapper that
  multiplies any increase (payouts incl. returned stakes) by 5. 0x9F4F8 (not casino) untouched.
- Known/not done: 0x4AD904 600-update countdown still 2x; between-point volleyball cutscenes use
  the same camera code (not separately measured); the save's time of day changes between boots.
- Tools: vbboot.py, test_cam.py, camcuts.py, spikes.py, spamx.py; mkstates.py waits for the map.

## 2026-11-04: pose camera, teleport fix, high-poly swap (DOAP-pt4.iso, DOAP-pt4-hipoly.iso)

- **Pose camera:** 0x15C608 (camera mode dispatcher 0x1459BC; win poses and point celebrations; the
  equivalent of DOAX2's 82430628) eases its orbit with 0x13B0D8(frame, length, 2, frame, 1) at
  0x15C984, where frame = 0x167398 (anim record +0x50, whole frames). Wrapper cam_pose takes the
  fraction from 0x1673C8 and reuses the shared lerp. The later key blends truncate at ~0.0001 rad, so
  DOAX2's double-run averaging is not needed. Outros not yet confirmed by the user.
- **Volleyball teleporting (regression from ANIM_FRAC):** fractional time in (last frame, last + 1)
  sampled past the motion's last key, so root motion lurched at motion ends. anim_clamp wraps
  0x16892C and limits time to (+0x70 - s3) / 30. Isolated snaps 51 -> 22 per 68 s, remainder mostly
  point-reset motions 89x and authored dash starts (30 FPS: 9 per 100 s). tools/teleport.py, snaps.py.
- **Archive:** doap.bin = 'PAA', count +8, 16-byte entries at +0x20 (name offset, size, tree
  left/right; name lookup), u32 offsets at table [+0x10]. doap.arc files are gzip; .tpr = PAR
  container with .pmd/.pta. char_h/wear_h mirror char/wear by name (1269 pairs, ~3x larger).
- **High-poly swap:** tools/hipoly.py repoints char/ and wear/ entries at the _h data: no code, 296 KB
  index change. Loads and plays. Same face rig in low and high (BS_face: face_eyeclose,
  matuge_eyeclose; BS_mouth: mouth_a/e/i/o/u/laugh; found by name). Blend-shape updater 0xCC9C8 ->
  0xCDF38 confirmed running on high models in a match. High adds hair bones blhair/brhair (low has
  bhair). Cost: rally needs more emulated CPU: 30 updates/s at 333 MHz (game half speed), 60 at 444+
  (PPSSPP CPUSpeed). Tested volleyball only; `hand/` files not swapped; real PSP not targeted.

## 2026-11-04: casino x10 with matching on-screen amounts (build pt6 / pt6-hipoly)

- pt4's x5 wrapper only changed the money, so the chips, win popup and balance kept base amounts.
  Same layout as DOAX2: seat chips at +0x408 (seat size 0x410), counter block seg1+0x5DB08 (+4 win,
  +8 shown, +0xC step, +0x10 balance, +0x14 shown, +0x18 step), 0x70C60 = add to win, 0x70BF8 = add
  to balance.
- Blackjack: `CASINO_ODDS_X10` scales the float odds, so one amount feeds chips, money and counter.
  Push (0x5AD58) and the 0.5 return (0x59E7C, probably surrender) stay base. Applies to all seats.
- Poker: `poker_win` at 0x79008 scales money and seat 0 chips; `poker_show` 0x7928C, `poker_take`
  0x75664 and `poker_bank` 0x75670 scale the counter updates, which recompute bet x pot.
- 0x9B000-0xA2000 is slots, not "not casino" as noted on 2026-11-01 (3 coins, pay table, count-up).
  A1244 picks the pay per coin (machine +0x64, -1 style on a loss); 0x9F44C commits it to the money
  when the spin starts, and 0x9EC1C/0xA017C show it. `slots_pay` at 0x9FD84 scales +0x64 when
  positive. pt4 never multiplied slots. No separate roulette payout code was found.
- Played at x20 (pt5) and working; pt6 changes only the multiplier to x10.

## 2026-11-04: menus still 2x (open)

- Pad repeat is real time (0x1FF908 compares elapsed seconds), so DOAX2's repeat scaling does not apply.
- At menus, task 0x172FC runs 0x160E4 (pad checks) and 0x1650C (page manager: +0x34 counter, +0x38
  duration, page handlers by jalr), then waits one tick. The task is idle in minigames.
- Tried: wait two ticks there plus reading the pad (0xBB0 from 0x1FE4) at the same rate. 2D assets
  flicker on the title screen, because the page handlers also submit the 2D every update.
- Next: split page update from draw, keep the 2D between updates, or scale the frame counters
  page by page as DOAX2 did.
