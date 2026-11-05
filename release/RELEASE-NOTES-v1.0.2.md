> [!NOTE]
> If you notice **anything that looks or feels off** (stutters, speed, camera, models, textures, payouts, anything at all), please [open an issue](https://github.com/hibikifukui22/DOAP-Enhanced/issues) with what happened, where in the game, your PPSSPP version and your emulated CPU clock setting.

> [!WARNING]
> **Set PPSSPP's emulated CPU clock to 444 MHz or higher** under Settings > System. High poly models are used everywhere in the game, including all the places that originally used lower poly ones. At the default clock, volleyball drops to 30 FPS and runs at half speed.
>
> This build is for the PPSSPP emulator only. PSP hardware is unsupported and untested. Minigames are fixed but still need testing, and the **10x casino payouts** are experimental.

**This download does not include the game.** You need your own copy of Dead or Alive Paradise for the PSP (USA, ULUS-10521). The patcher stores only the differences between game versions, which are useless without your own ISO.

## New in v1.0.2

### Fixed: the real cause of the volleyball crash
v1.0.1 stopped this crash by skipping a texture's colour palette, which could leave odd textures, such as wrong-looking eyes. We found the cause, so that workaround is no longer what keeps the game running.

**What happened:** textures use a small fixed block of memory, and the larger high poly textures could fill it. The game handled that for a texture's pixels, but not for its palette, so a texture could end up with no palette at all.

**What changed:** when a palette doesn't fit, the whole texture now loads into normal memory instead, with its real colours. Please [tell us](https://github.com/hibikifukui22/DOAP-Enhanced/issues) if you still see odd textures.

### New: update from any version
The patcher now detects which ISO you give it and applies only what's needed:
- **A clean ISO** gets the full patch, as before.
- **An ISO patched with v1.0.0 or v1.0.1** is updated to the latest version, so you don't need to keep a clean ISO around.
- **An ISO that's already up to date** is left alone.

### New: patcher updates
The new **UPDATE** button checks GitHub for a newer patcher, then downloads it, checks its SHA-256 and installs it. The patcher contacts a server only while this page is open, and nothing about you or your PC is sent.

This update has to be downloaded by hand once, because older patchers don't have the button.

## Known issues
- **Menus run at double speed.** Fixing this needs a big rebuild of the menu code. It does not affect gameplay.

## Install
1. Download `DOAP-Enhanced-Patcher-v1.0.2.exe`, run it, choose your ISO (clean or patched) and press **PATCH**.
2. Open `Dead or Alive Paradise 60 FPS.iso` in PPSSPP.
3. Set the emulated CPU clock to 444 MHz or higher.

The new ISO is written next to the one you chose. If your ISO already has that name, the patcher asks before replacing it.

Your saves carry over. Save states from older versions keep the old code, so boot the game fresh instead of loading one.

The patcher is not code signed, so Windows SmartScreen may warn you. Compare the file with `SHA256SUMS.txt` to check your download.
