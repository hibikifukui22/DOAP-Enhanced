#!/usr/bin/env python3
"""Replace one growing ISO9660 file by shifting later extents in a new image.

This is intentionally conservative: the target must precede only regular files,
all directory records must be before it, and every original file is rehashed.
The source image is never modified. Does not support multi-extent ISO files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

from iso9660 import Iso9660, IsoError, SECTOR_SIZE


def digest_extent(iso: Iso9660, extent: int, size: int) -> str:
    result = hashlib.sha256()
    iso._fh.seek(extent * SECTOR_SIZE)
    remaining = size
    while remaining:
        block = iso._fh.read(min(1024 * 1024, remaining))
        if not block:
            raise IsoError("truncated file extent")
        result.update(block)
        remaining -= len(block)
    return result.hexdigest()


def copy_bytes(source, output, count: int) -> None:
    while count:
        block = source.read(min(1024 * 1024, count))
        if not block:
            raise IsoError("truncated source ISO")
        output.write(block)
        count -= len(block)


def both_endian_u32(output, offset: int, value: int) -> None:
    if not 0 <= value <= 0xFFFFFFFF:
        raise IsoError("ISO9660 32-bit field overflow")
    output.seek(offset)
    output.write(struct.pack("<I", value) + struct.pack(">I", value))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("iso_path", help="path inside ISO, e.g. PSP_GAME/USRDIR/data.cpk")
    parser.add_argument("replacement", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("source and output must differ")
    if not args.replacement.is_file():
        parser.error("replacement does not exist")

    with Iso9660(args.source) as iso:
        entries = list(iso.entries())
        matches = [e for e in entries if e.path == args.iso_path and not e.is_directory]
        if len(matches) != 1:
            raise IsoError("target file not found exactly once")
        target = matches[0]
        start = target.extent * SECTOR_SIZE
        following = [e for e in entries if e.extent > target.extent]
        if any(e.is_directory for e in following):
            raise IsoError("directory data after target is unsupported")
        if any(e.record_offset >= start for e in entries):
            raise IsoError("directory record after target is unsupported")
        next_offset = min((e.extent * SECTOR_SIZE for e in following), default=args.source.stat().st_size)
        if next_offset < start + target.size:
            raise IsoError("target overlaps a following file")
        replacement_size = args.replacement.stat().st_size
        if replacement_size <= target.size:
            raise IsoError("this tool is only for a growing replacement")
        # Keep the original inter-file gap and 16-sector alignment of this disc.
        extra = replacement_size - target.size
        shift_sectors = (extra + 16 * SECTOR_SIZE - 1) // (16 * SECTOR_SIZE) * 16
        shift = shift_sectors * SECTOR_SIZE
        new_size = args.source.stat().st_size + shift
        if new_size % SECTOR_SIZE:
            raise IsoError("source ISO is not sector-aligned")
        source_hashes = {e.path: digest_extent(iso, e.extent, e.size)
                         for e in entries if not e.is_directory and e.path != args.iso_path}
        replacement_digest = hashlib.sha256()
        with args.replacement.open("rb") as replacement:
            while block := replacement.read(1024 * 1024):
                replacement_digest.update(block)
        replacement_hash = replacement_digest.hexdigest()
        plan = {"target": args.iso_path, "old_size": target.size,
                "new_size": replacement_size, "shift_sectors": shift_sectors,
                "output_size": new_size, "later_files": len(following),
                "replacement_sha256": replacement_hash}
        if args.check_only:
            print(json.dumps(plan, indent=2))
            return 0

        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.source.open("rb") as source, args.output.open("wb") as output:
            copy_bytes(source, output, start)
            with args.replacement.open("rb") as replacement:
                shutil.copyfileobj(replacement, output, 1024 * 1024)
            source.seek(start + target.size)
            copy_bytes(source, output, next_offset - (start + target.size))
            output.write(bytes(shift - extra))
            if output.tell() != next_offset + shift:
                raise IsoError("shifted suffix position mismatch")
            source.seek(next_offset)
            shutil.copyfileobj(source, output, 1024 * 1024)
        with args.output.open("r+b") as output:
            both_endian_u32(output, target.record_offset + 10, replacement_size)
            for entry in following:
                both_endian_u32(output, entry.record_offset + 2, entry.extent + shift_sectors)
            for sector in range(16, 80):
                output.seek(sector * SECTOR_SIZE)
                desc = output.read(7)
                if len(desc) != 7 or desc[1:6] != b"CD001":
                    break
                if desc[0] in (1, 2):
                    both_endian_u32(output, sector * SECTOR_SIZE + 80, new_size // SECTOR_SIZE)
                if desc[0] == 255:
                    break

    with Iso9660(args.output) as rebuilt:
        actual = {e.path: e for e in rebuilt.entries() if not e.is_directory}
        if rebuilt.volume_sectors != new_size // SECTOR_SIZE:
            raise IsoError("volume size did not update")
        if digest_extent(rebuilt, actual[args.iso_path].extent, actual[args.iso_path].size) != replacement_hash:
            raise IsoError("replacement verification failed")
        for path, original_hash in source_hashes.items():
            entry = actual[path]
            if digest_extent(rebuilt, entry.extent, entry.size) != original_hash:
                raise IsoError(f"unchanged file verification failed: {path}")
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
