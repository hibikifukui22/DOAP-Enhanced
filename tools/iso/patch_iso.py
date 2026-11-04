#!/usr/bin/env python3
"""Create an ISO copy with strictly same-size file replacements in original extents."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path

from iso9660 import Iso9660, IsoError


def parse_replacement(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("replacement must be ISO/PATH=LOCAL/PATH")
    iso_path, local_path = value.split("=", 1)
    iso_path = iso_path.replace("\\", "/").lstrip("/")
    if not iso_path or not local_path:
        raise argparse.ArgumentTypeError("replacement paths cannot be empty")
    return iso_path, Path(local_path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="original ISO (never modified)")
    parser.add_argument("output", type=Path, nargs="?", help="new patched ISO")
    parser.add_argument("--replace", action="append", type=parse_replacement, default=[],
                        metavar="ISO_PATH=LOCAL_PATH")
    parser.add_argument("--check-only", action="store_true", help="validate replacements without copying")
    parser.add_argument("--pad-smaller", action="store_true",
                        help="allow a smaller replacement and zero-pad it to the original ISO file size")
    parser.add_argument("--shrink-file", action="store_true",
                        help="allow a smaller replacement and update its ISO9660 directory-record size")
    parser.add_argument("--grow-in-slack", action="store_true",
                        help="allow a larger replacement when it still ends before the next file's extent")
    args = parser.parse_args()
    if not args.replace:
        parser.error("at least one --replace is required")
    if not args.check_only and not args.output:
        parser.error("output is required unless --check-only is used")
    if args.output and args.source.resolve() == args.output.resolve():
        parser.error("output must differ from the source ISO")

    requested = dict(args.replace)
    if len(requested) != len(args.replace):
        parser.error("duplicate ISO replacement path")
    with Iso9660(args.source) as iso:
        entries = {entry.path: entry for entry in iso.entries() if not entry.is_directory}
        plan = []
        for iso_path, local_path in requested.items():
            entry = entries.get(iso_path)
            if entry is None:
                raise IsoError(f"ISO file not found: {iso_path}")
            if not local_path.is_file():
                raise IsoError(f"replacement file not found: {local_path}")
            replacement_size = local_path.stat().st_size
            allow_smaller = (args.pad_smaller or args.shrink_file) and replacement_size < entry.size
            allow_larger = False
            if args.grow_in_slack and replacement_size > entry.size:
                later = [e.extent for e in iso.entries() if e.extent > entry.extent]
                next_extent = min(later) if later else None
                sectors = -(-replacement_size // iso.block_size)
                if next_extent is None or entry.extent + sectors > next_extent:
                    raise IsoError(f"{iso_path}: {replacement_size} bytes do not fit before extent {next_extent}")
                allow_larger = True
            if replacement_size != entry.size and not (allow_smaller or allow_larger):
                raise IsoError(
                    f"size mismatch for {iso_path}: ISO={entry.size}, replacement={replacement_size}; "
                    "use --pad-smaller only when trailing zero padding is valid for the file type"
                )
            padding_size = max(0, entry.size - replacement_size)
            recorded_size = replacement_size if (args.shrink_file and padding_size) or allow_larger else entry.size
            extent_digest = hashlib.sha256()
            with local_path.open("rb") as replacement:
                while block := replacement.read(1024 * 1024):
                    extent_digest.update(block)
            remaining_padding = padding_size
            zero_block = bytes(1024 * 1024)
            while remaining_padding:
                block = zero_block[: min(len(zero_block), remaining_padding)]
                extent_digest.update(block)
                remaining_padding -= len(block)
            plan.append({"iso_path": iso_path, "local_path": str(local_path.resolve()),
                         "size": entry.size, "extent": entry.extent,
                         "byte_offset": entry.extent * iso.block_size,
                         "replacement_size": replacement_size, "padding_size": padding_size,
                         "recorded_size": recorded_size, "directory_record_offset": entry.record_offset,
                         "replacement_sha256": sha256(local_path),
                         "expected_extent_sha256": extent_digest.hexdigest()})

    if not args.check_only:
        assert args.output is not None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.source, args.output)
        with args.output.open("r+b") as output:
            for item in plan:
                output.seek(int(item["byte_offset"]))
                local_path = Path(str(item["local_path"]))
                with local_path.open("rb") as replacement:
                    shutil.copyfileobj(replacement, output, 1024 * 1024)
                remaining_padding = int(item["padding_size"])
                zero_block = bytes(1024 * 1024)
                while remaining_padding:
                    block = zero_block[: min(len(zero_block), remaining_padding)]
                    output.write(block)
                    remaining_padding -= len(block)
                if int(item["recorded_size"]) != int(item["size"]):
                    record_offset = int(item["directory_record_offset"])
                    output.seek(record_offset + 10)
                    output.write(struct.pack("<I", int(item["recorded_size"])))
                    output.write(struct.pack(">I", int(item["recorded_size"])))
        # Re-open the result and verify every replacement directly from its ISO extent.
        with Iso9660(args.output) as iso:
            entries = {entry.path: entry for entry in iso.entries() if not entry.is_directory}
            for item in plan:
                entry = entries[str(item["iso_path"])]
                iso._fh.seek(entry.extent * iso.block_size)
                digest = hashlib.sha256()
                expected_size = int(item["recorded_size"])
                if entry.size != expected_size:
                    raise IsoError(
                        f"directory-size verification failed for {entry.path}: {entry.size} != {expected_size}"
                    )
                remaining = entry.size
                while remaining:
                    block = iso._fh.read(min(1024 * 1024, remaining))
                    if not block:
                        raise IsoError(f"truncated patched extent: {entry.path}")
                    digest.update(block)
                    remaining -= len(block)
                expected_digest = (
                    item["replacement_sha256"] if expected_size == int(item["replacement_size"])
                    else item["expected_extent_sha256"]
                )
                if digest.hexdigest() != expected_digest:
                    raise IsoError(f"post-write verification failed: {entry.path}")
    print(json.dumps({"source": str(args.source.resolve()),
                      "output": str(args.output.resolve()) if args.output else None,
                      "check_only": args.check_only, "replacements": plan}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
