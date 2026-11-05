# Building

Everything is built from your own game files, which stay out of git.

## Requirements

- Windows 10 or 11 (64-bit)
- Visual Studio with the C++ desktop tools and a Windows SDK
- Python 3 with `numpy` (and `capstone` for the analysis helpers)
- PPSSPP, to dump the decrypted EBOOT

## Game files

Put these in the repository root:

| Path | What it is |
|---|---|
| `Dead or Alive - Paradise (USA) (En,Ja,Fr,De).iso` | Your clean ISO (SHA-256 in the README) |
| `research/EBOOT.ppsspp-dump.BIN` | The decrypted EBOOT, from PPSSPP's option to dump it on game boot. SHA-256 `5dc782c28239d69d8f6587a96322a02c52ab4c9ba80af2e0496f1aa717375145` |
| `research/source-tree/PSP_GAME/USRDIR/doap.bin` | The archive index, extracted from your ISO with `tools/iso/iso9660.py --extract` |

## Build the patched ISOs

```powershell
python tools/build_patch.py pt14 --iso
python -I tools/hipoly.py pt14 pt14-hipoly
```

These write `build/DOAP-pt14.iso` and `build/DOAP-pt14-hipoly.iso`. The second is
what the patcher produces. To build the patcher's upgrades from earlier releases,
also build those ISOs: `pt6` is v1.0.0 and `pt12` is v1.0.1, the same way. Every word patch checks the original value first, so
a wrong EBOOT is refused.

## Build the patcher

```powershell
python patcher/build_payload.py
.\patcher\build.ps1
```

`build_payload.py` writes `patcher/payload.bin`, the XOR differences against the
clean ISO. `build.ps1` compiles `patcher/release/DOA Paradise 60 FPS Patcher.exe`
with the payload and the art in `patcher/resources/` embedded.

## Before publishing

Run the check on the repository and on the release file:

```powershell
.\scripts\check-personal-data.ps1 -Path .
```
