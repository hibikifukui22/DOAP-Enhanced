> [!NOTE]
> If you notice **anything that looks or feels off** (stutters, speed, camera, models, payouts, anything at all), please [open an issue](https://github.com/hibikifukui22/DOAP-Enhanced/issues) with what happened, where in the game, your PPSSPP version and your emulated CPU clock setting.

> [!WARNING]
> **Set PPSSPP's emulated CPU clock to 444 MHz or higher** under Settings > System. High poly models are used everywhere in the game, including all the places that originally used lower poly ones. At the default clock, volleyball drops to 30 FPS and runs at half speed.
>
> This build is for the PPSSPP emulator only. PSP hardware is unsupported and untested. Minigames are fixed but still need testing, and the **10x casino payouts** are experimental.

**This download does not include the game.** You need your own clean ISO of Dead or Alive Paradise for the PSP (USA, ULUS-10521). The patcher stores only the differences between the original and patched discs, which are useless without it.

## New in v1.0.0

First public release.

### True 60 FPS
- The whole game is uncapped to 60 FPS at the original game speed.
- Girl animation, body physics and camera pans run at 60 FPS, including minigame intros, win poses, point celebrations and relax scenes.
- Volleyball, Pool Hopping, relax scenes and the casino keep their original speed.

### Higher poly models
Every place the game used lower poly models of the girls now uses the higher poly models from the cutscenes, bringing it closer to Dead or Alive Xtreme 2.

### Casino payouts (experimental)
- Payouts are 10x in blackjack, poker and slots.
- The chips, win amounts and balance shown on screen match what you receive.
- Blackjack pushes and returned half bets still pay back the original amount.

### The patcher
- Checks your ISO before changing anything, and rejects other regions and already patched ISOs.
- Writes a new ISO next to your original, which is never changed.
- Checks the finished ISO against its expected SHA-256 when your source is the exact clean ISO.
- Does not use the internet.

## Known issues
- **Menus run at double speed.** Fixing this needs a big rebuild of the menu code. It does not affect gameplay.

## Install
1. Download `DOAP-Enhanced-Patcher-v1.0.0.exe`, run it, choose your clean ISO and press **PATCH**.
2. Open `Dead or Alive Paradise 60 FPS.iso` in PPSSPP.
3. Set the emulated CPU clock to 444 MHz or higher.

The patcher is not code signed, so Windows SmartScreen may warn you. Compare the file with `SHA256SUMS.txt` to check your download.
