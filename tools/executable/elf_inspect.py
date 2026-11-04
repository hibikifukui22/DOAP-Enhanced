#!/usr/bin/env python3
"""Inspect a 32-bit little-endian ELF/PSP PRX without external dependencies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def cstring(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return "<invalid>"
    return data[offset : data.find(b"\0", offset) if b"\0" in data[offset:] else len(data)].decode("ascii", "replace")


def ascii_strings(data: bytes, minimum: int = 4) -> list[dict[str, object]]:
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % minimum)
    return [{"offset": match.start(), "text": match.group().decode("ascii")} for match in pattern.finditer(data)]


def inspect(path: Path, minimum: int) -> tuple[dict[str, object], list[dict[str, object]]]:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4:6] != b"\x01\x01":
        raise ValueError("expected a 32-bit little-endian ELF")
    fields = struct.unpack_from("<16sHHIIIIIHHHHHH", data, 0)
    (_, e_type, machine, version, entry, phoff, shoff, flags, ehsize,
     phentsize, phnum, shentsize, shnum, shstrndx) = fields
    section_rows = []
    raw_sections = []
    for index in range(shnum):
        off = shoff + index * shentsize
        if off + 40 > len(data):
            raise ValueError("section table exceeds file")
        raw_sections.append(struct.unpack_from("<IIIIIIIIII", data, off))
    shstr = b""
    if shstrndx < len(raw_sections):
        row = raw_sections[shstrndx]
        shstr = data[row[4] : row[4] + row[5]]
    for index, row in enumerate(raw_sections):
        name_off, kind, addr, offset, size, link, info, align, entsize = row[0], row[1], row[3], row[4], row[5], row[6], row[7], row[8], row[9]
        section_rows.append({
            "index": index,
            "name": cstring(shstr, name_off),
            "type": kind,
            "address": addr,
            "offset": offset,
            "size": size,
            "link": link,
            "info": info,
            "alignment": align,
            "entry_size": entsize,
        })
    module_info = None
    module_section = next((row for row in section_rows if row["name"] == ".rodata.sceModuleInfo"), None)
    if module_section and int(module_section["size"]) >= 52:
        offset = int(module_section["offset"])
        attributes, module_version = struct.unpack_from("<HH", data, offset)
        module_info = {
            "attributes": attributes,
            "version": module_version,
            "name": data[offset + 4 : offset + 32].split(b"\0", 1)[0].decode("ascii", "replace"),
            "gp": struct.unpack_from("<I", data, offset + 32)[0],
            "export_top": struct.unpack_from("<I", data, offset + 36)[0],
            "export_end": struct.unpack_from("<I", data, offset + 40)[0],
            "import_top": struct.unpack_from("<I", data, offset + 44)[0],
            "import_end": struct.unpack_from("<I", data, offset + 48)[0],
        }
    program_rows = []
    for index in range(phnum):
        off = phoff + index * phentsize
        if off + 32 > len(data):
            raise ValueError("program table exceeds file")
        kind, offset, vaddr, paddr, filesz, memsz, pflags, align = struct.unpack_from("<IIIIIIII", data, off)
        program_rows.append({"index": index, "type": kind, "offset": offset, "vaddr": vaddr,
                             "paddr": paddr, "file_size": filesz, "memory_size": memsz,
                             "flags": pflags, "alignment": align})
    strings = ascii_strings(data, minimum)
    needles = re.compile(
        r"(?:[A-Za-z]:\\|(?:[A-Za-z0-9_.-]+[/\\]){2,}|\.(?:ccs|cpk|afs|pbd|phd|bin|ccdat|ccbin|ccprm|ccbpm|cctxt|scpck|prx)(?:$|[^A-Za-z])|"
        r"sce[A-Z]|CCSF|CRILAYLA|Cpk|CPK|Load|load|battle|Battle|character|Character|script|Script)"
    )
    relevant = [item for item in strings if needles.search(str(item["text"]))]
    result = {
        "path": str(path), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "elf": {"type": e_type, "machine": machine, "version": version, "entry": entry,
                "flags": flags, "header_size": ehsize, "program_offset": phoff,
                "program_entry_size": phentsize, "program_count": phnum,
                "section_offset": shoff, "section_entry_size": shentsize,
                "section_count": shnum, "section_string_index": shstrndx},
        "program_headers": program_rows, "sections": section_rows, "psp_module_info": module_info,
        "ascii_string_count": len(strings), "relevant_strings": relevant,
    }
    return result, strings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--minimum", type=int, default=4)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--strings-json", type=Path, help="write every extracted ASCII string")
    args = parser.parse_args()
    result, strings = inspect(args.input, args.minimum)
    text = json.dumps(result, indent=2) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    if args.strings_json:
        args.strings_json.parent.mkdir(parents=True, exist_ok=True)
        args.strings_json.write_text(json.dumps(strings, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "relevant_strings"}, indent=2))
    print(f"relevant_strings: {len(result['relevant_strings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
