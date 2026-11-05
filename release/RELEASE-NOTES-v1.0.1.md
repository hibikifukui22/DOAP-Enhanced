> [!NOTE]
> If you notice **anything that looks or feels off** (stutters, speed, camera, models, wrong-coloured textures, payouts, anything at all), please [open an issue](https://github.com/hibikifukui22/DOAP-Enhanced/issues) with what happened, where in the game, which girls and swimsuits were on screen, your PPSSPP version and your emulated CPU clock setting.

> [!WARNING]
> **Set PPSSPP's emulated CPU clock to 444 MHz or higher** under Settings > System. High poly models are used everywhere in the game, including all the places that originally used lower poly ones. At the default clock, volleyball drops to 30 FPS and runs at half speed.
>
> This build is for the PPSSPP emulator only. PSP hardware is unsupported and untested. Minigames are fixed but still need testing, and the **10x casino payouts** are experimental.

**This download does not include the game.** You need your own clean ISO of Dead or Alive Paradise for the PSP (USA, ULUS-10521). The patcher stores only the differences between the original and patched discs, which are useless without it.

## New in v1.0.1

### Fixed: crash when loading a volleyball match
In v1.0.0, some volleyball matches could crash the game during the loading transition.

**What happened:** while loading the match, the game tried to copy a texture's colour palette into a buffer that did not exist, and PPSSPP stopped with an Invalid Memory Access error. Giving the game more memory made no difference, so this is not a memory shortage.

**What changed:** the game now skips that copy when the buffer is missing, so the match loads. Some odd textures may be visible during this testing period, but nothing game breaking.

**We are still looking into it.** This stops the crash, but we don't yet know why the buffer is missing or which model causes it. If you see a wrong-coloured or missing texture in a match, please [tell us](https://github.com/hibikifukui22/DOAP-Enhanced/issues) which girls and swimsuits were on screen.

### Fixed: flickering numbers in Pool Hopping
The timer and the numbers shown as you jump no longer flicker.

## Known issues
- **Menus run at double speed.** Fixing this needs a big rebuild of the menu code. It does not affect gameplay.

## Install
1. Download `DOAP-Enhanced-Patcher-v1.0.1.exe`, run it, choose your clean ISO and press **PATCH**. It needs the original clean ISO, not one patched with v1.0.0.
2. Open `Dead or Alive Paradise 60 FPS.iso` in PPSSPP.
3. Set the emulated CPU clock to 444 MHz or higher.

Your saves carry over. Save states made on v1.0.0 keep the old code, so boot the game fresh instead of loading one.

The patcher is not code signed, so Windows SmartScreen may warn you. Compare the file with `SHA256SUMS.txt` to check your download.
