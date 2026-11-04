#!/usr/bin/env python3
"""Minimal, dependency-free ISO9660 reader for reproducible PSP disc extraction.

This intentionally preserves the original ISO as the authority.  It records disc
extents and raw ISO9660 names so later rebuilding work can reproduce layout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator


SECTOR_SIZE = 2048


@dataclass(frozen=True)
class Entry:
    path: str
    iso_name: str
    extent: int
    size: int
    flags: int
    recorded_at: str | None
    record_offset: int

    @property
    def is_directory(self) -> bool:
        return bool(self.flags & 0x02)


class IsoError(RuntimeError):
    pass


def _u16le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32le(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _recorded_at(raw: bytes) -> str | None:
    if len(raw) != 7 or raw[0] == 0:
        return None
    year = 1900 + raw[0]
    offset = int.from_bytes(raw[6:7], "little", signed=True)
    try:
        stamp = datetime(year, raw[1], raw[2], raw[3], raw[4], raw[5], tzinfo=timezone.utc)
    except ValueError:
        return None
    # ISO9660 stores a signed count of 15-minute intervals from GMT.
    stamp = stamp.replace(tzinfo=timezone.utc)
    seconds = int(stamp.timestamp()) - offset * 15 * 60
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def _clean_name(raw_name: bytes) -> tuple[str, str]:
    iso_name = raw_name.decode("ascii", errors="replace")
    display = iso_name
    if ";" in display:
        display = display.rsplit(";", 1)[0]
    if display.endswith("."):
        display = display[:-1]
    return display, iso_name


class Iso9660:
    def __init__(self, path: Path):
        self.path = path
        self._fh: BinaryIO = path.open("rb")
        self.pvd = self._read_primary_volume_descriptor()
        self.block_size = _u16le(self.pvd, 128)
        if self.block_size != SECTOR_SIZE:
            raise IsoError(f"unsupported logical block size: {self.block_size}")
        self.volume_id = self.pvd[40:72].decode("ascii", errors="replace").rstrip(" ")
        self.system_id = self.pvd[8:40].decode("ascii", errors="replace").rstrip(" ")
        self.volume_sectors = _u32le(self.pvd, 80)
        self.root = self._parse_record(self.pvd, 156, "", "", 16 * SECTOR_SIZE + 156)

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "Iso9660":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _read_primary_volume_descriptor(self) -> bytes:
        self._fh.seek(16 * SECTOR_SIZE)
        for _ in range(64):
            descriptor = self._fh.read(SECTOR_SIZE)
            if len(descriptor) != SECTOR_SIZE:
                raise IsoError("truncated volume descriptor sequence")
            if descriptor[1:6] != b"CD001" or descriptor[6] != 1:
                raise IsoError("not an ISO9660 volume descriptor")
            if descriptor[0] == 1:
                return descriptor
            if descriptor[0] == 255:
                break
        raise IsoError("primary volume descriptor not found")

    def _parse_record(
        self, data: bytes, offset: int, path: str, fallback_name: str, record_offset: int
    ) -> Entry:
        length = data[offset]
        if length < 34 or offset + length > len(data):
            raise IsoError(f"invalid directory record at byte {offset}")
        extent = _u32le(data, offset + 2)
        size = _u32le(data, offset + 10)
        flags = data[offset + 25]
        name_length = data[offset + 32]
        raw_name = data[offset + 33 : offset + 33 + name_length]
        if raw_name in (b"\x00", b"\x01"):
            display_name, iso_name = fallback_name, fallback_name
        else:
            display_name, iso_name = _clean_name(raw_name)
        full_path = str(PurePosixPath(path, display_name)) if display_name else path
        return Entry(
            full_path, iso_name, extent, size, flags,
            _recorded_at(data[offset + 18 : offset + 25]), record_offset,
        )

    def _read_extent(self, extent: int, size: int) -> bytes:
        self._fh.seek(extent * self.block_size)
        data = self._fh.read(size)
        if len(data) != size:
            raise IsoError(f"truncated extent {extent}: expected {size}, read {len(data)}")
        return data

    def _children(self, directory: Entry) -> Iterator[Entry]:
        data = self._read_extent(directory.extent, directory.size)
        offset = 0
        while offset < len(data):
            length = data[offset]
            if length == 0:
                offset = ((offset // self.block_size) + 1) * self.block_size
                continue
            record = self._parse_record(
                data, offset, directory.path, "", directory.extent * self.block_size + offset
            )
            raw_name_length = data[offset + 32]
            raw_name = data[offset + 33 : offset + 33 + raw_name_length]
            offset += length
            if raw_name in (b"\x00", b"\x01"):
                continue
            yield record

    def entries(self) -> Iterator[Entry]:
        stack = list(reversed(list(self._children(self.root))))
        while stack:
            entry = stack.pop()
            yield entry
            if entry.is_directory:
                stack.extend(reversed(list(self._children(entry))))

    def extract(self, entry: Entry, destination: Path) -> str | None:
        target = destination.joinpath(*PurePosixPath(entry.path).parts)
        resolved_root = destination.resolve()
        resolved_target = target.resolve()
        if resolved_root != resolved_target and resolved_root not in resolved_target.parents:
            raise IsoError(f"unsafe output path: {entry.path}")
        if entry.is_directory:
            target.mkdir(parents=True, exist_ok=True)
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        remaining = entry.size
        self._fh.seek(entry.extent * self.block_size)
        with target.open("wb") as out:
            while remaining:
                chunk = self._fh.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise IsoError(f"truncated file: {entry.path}")
                out.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--extract", type=Path, help="extract all files to this directory")
    parser.add_argument("--manifest", type=Path, help="write a JSON inventory")
    args = parser.parse_args()

    with Iso9660(args.iso) as iso:
        entries = list(iso.entries())
        manifest_entries: list[dict[str, object]] = []
        for entry in entries:
            item = asdict(entry)
            item["is_directory"] = entry.is_directory
            item["sha256"] = None
            if args.extract:
                item["sha256"] = iso.extract(entry, args.extract)
            manifest_entries.append(item)

        manifest = {
            "schema_version": 1,
            "source_iso": str(args.iso.resolve()),
            "source_size": args.iso.stat().st_size,
            "system_id": iso.system_id,
            "volume_id": iso.volume_id,
            "logical_block_size": iso.block_size,
            "volume_sectors": iso.volume_sectors,
            "entries": manifest_entries,
        }
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2) + os.linesep, encoding="utf-8")
        print(json.dumps({key: value for key, value in manifest.items() if key != "entries"}, indent=2))
        print(f"entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
