# Changelog

## 1.0.2

- Fixed the real cause of the volleyball load crash: a texture whose palette does
  not fit the fixed texture memory now loads into normal memory, so it keeps its
  real colours. This also fixes the odd textures from 1.0.1.
- The patcher detects the ISO it is given: clean, patched with an earlier
  version, or already up to date, and applies only what is needed.
- New UPDATE page: the patcher can download, verify and install new versions.
- See `release/RELEASE-NOTES-v1.0.2.md`.

## 1.0.1

- Fixed a crash while loading some volleyball matches. The game now skips a
  texture palette copy into a missing buffer; the root cause is still being
  looked into.
- Fixed the Pool Hopping timer and jump numbers flickering.
- See `release/RELEASE-NOTES-v1.0.1.md`.

## 1.0.0

First public release: a patcher for the clean USA ISO (ULUS-10521) with true
60 FPS at the original game speed, higher poly models everywhere and 10x casino
payouts (experimental). See `release/RELEASE-NOTES-v1.0.0.md`.
