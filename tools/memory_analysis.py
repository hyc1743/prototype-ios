#!/usr/bin/env python3
"""Offline evidence and page-congruence model for Prototype's Xbox arena.

This tool disassembles data already supplied locally. It does not install a VM
bridge, execute the guest, or claim that any Apple mapping has been tested.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

import capstone

from analyze_platform import SOURCE_SHA256
from nro import Nro

ATOMIC_HELPERS = {
    "cas_u8_relaxed": (0x2A89340, 0x30),
    "cas_u32_relaxed": (0x2A89370, 0x30),
    "cas_u32_acquire": (0x2A893A0, 0x30),
    "cas_u64_relaxed": (0x2A893D0, 0x30),
    "cas_u64_acq_rel": (0x2A89400, 0x30),
    "exchange_u8_relaxed": (0x2A89430, 0x28),
    "exchange_u32_acquire": (0x2A89460, 0x28),
    "exchange_u32_release": (0x2A89490, 0x28),
    "fetch_add_u32_release": (0x2A894C0, 0x2C),
    "fetch_add_u64_relaxed": (0x2A894F0, 0x2C),
    "fetch_add_u64_release": (0x2A89520, 0x2C),
}
FUNCTIONS = {
    "RexGmInit": 0xDEEE0,
    "RexGmBase": 0xDF1D0,
    "RexGmSize": 0xDF1E0,
    "RexGmWindowToOffset": 0xDF1F0,
    "RexGmLastResult": 0xDF3F0,
    "RexGmAddView": 0xDF450,
    "RexGmCommit": 0xDF640,
    "RexGmFaultIn": 0xDFCA0,
    "unclassified_window_range_operation": 0xDFF00,
    "RexGmProtect": 0xE0220,
    "RexGmQueryProtect": 0xE0F40,
    "RexGmShadowFor": 0xE1410,
    "EmulateViaShadow": 0xDC5E0,
    "HandleSwitchException": 0xDD250,
    "__libnx_exception_handler": 0xDDB40,
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def map_rows(source):
    start = source.index("} map_info[] = {")
    table = source[start:source.index("#if REX_PLATFORM_MAC", start)]
    rows = [tuple(int(number, 16) for number in row) for row in re.findall(
        r"\{\s*(0x[\da-fA-F]+),\s*(0x[\da-fA-F]+),\s*(0x[\da-fA-F]+)(?:ull)?,\s*\}", table)]
    if len(rows) != 9:
        raise ValueError("expected the reviewed nine-view Xbox arena layout")
    return rows


def translate(rows, address):
    for start, end, target in rows:
        if start <= address <= end:
            return target + address - start
    raise ValueError("address is outside the Xbox arena views")


def page_model(rows, page_size):
    if page_size <= 0 or page_size & (page_size - 1):
        raise ValueError("page size must be a power of two")
    return [{"start": hex(start), "end_inclusive": hex(end), "mapping_offset": hex(target),
             "direct_shared_alias_congruent": (target - start) % page_size == 0,
             "rounded_mapping_offset": hex(target & -page_size),
             "bytes_lost_by_rounding": target & (page_size - 1)}
            for start, end, target in rows]


def analyze(nro_path, sdk, boundaries_path, output):
    data = nro_path.read_bytes()
    image = Nro(data)
    if image.sha256 != SOURCE_SHA256:
        raise ValueError("memory evidence is bound to the pinned Prototype NRO")
    boundary_data = boundaries_path.read_bytes()
    boundaries = json.loads(boundary_data)
    if (boundaries["source_nro_sha256"] != image.sha256 or
            sha(data[boundaries["eh_frame_start"]:boundaries["eh_frame_end"]]) != boundaries["eh_frame_sha256"]):
        raise ValueError("FDE evidence does not match the supplied NRO")
    fdes = {item["start"]: item for item in boundaries["functions"]}
    source = sdk / "src/system/xmemory.cpp"
    rows = map_rows(source.read_text(encoding="utf8"))
    table = b"".join(struct.pack("<3Q", *row) for row in rows)
    location = data.find(table)
    if location != 0x2F3F5B0 or data.find(table, location + 1) != -1:
        raise ValueError("the complete source map_info table does not match the exact NRO")
    output.mkdir(parents=True, exist_ok=True)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    records = {}

    def export(name, address, size, boundary_kind):
        raw = data[address:address + size]
        instructions = list(md.disasm(raw, address))
        if sum(item.size for item in instructions) != size:
            raise ValueError("incomplete disassembly: " + name)
        filename = name + ".asm"
        (output / filename).write_text("# Inferred symbol; original NRO bytes; " + boundary_kind + "\n" +
            "\n".join(f"{item.address:#010x}  {item.bytes.hex()}  {item.mnemonic} {item.op_str}"
                      for item in instructions) + "\n", encoding="utf8")
        records[name] = {"address": hex(address), "size": size, "first16": raw[:16].hex(),
                         "sha256": sha(raw), "file": filename, "boundary_kind": boundary_kind}

    for name, address in FUNCTIONS.items():
        export(name, address, fdes[address]["size"], "exact FDE")
    for name, (address, size) in ATOMIC_HELPERS.items():
        if fdes[address]["size"] != size:
            raise ValueError("outlined atomic FDE size differs")
        export(name, address, size, "exact FDE")
    if data[0xDBC34:0xDBC38] != bytes.fromhex("45520000"):
        raise ValueError("resume trap signature differs")
    if data[0xDBB98:0xDBBA8] != bytes.fromhex("098440f9290110d129ed7c92ea0300aa"):
        raise ValueError("exception resume assembly prefix differs")
    export("RexResumeFromException", 0xDBB98, 0xA0,
           "assembly window from context-copy prefix to unique UDF trap; no FDE")

    # A real alias must preserve this equivalence after a write through any view.
    addresses = [0xA0001000, 0xC0001000, 0xE0000000, 0x100001000, 0x7F001000]
    offsets = [translate(rows, address) for address in addresses]
    assert len(set(offsets)) == 1 and offsets[0] == 0x100001000
    models = {str(page): page_model(rows, page) for page in (4096, 16384)}
    assert all(row["direct_shared_alias_congruent"] for row in models["4096"])
    assert [row["start"] for row in models["16384"] if not row["direct_shared_alias_congruent"]] == ["0xe0000000"]
    report = {
        "authority": "offline NRO bytes, FDE-bounded Capstone, and arithmetic model",
        "source_nro_sha256": image.sha256,
        "boundary_sha256": sha(boundary_data),
        "map_info_address": hex(location), "map_info_bytes": table.hex(), "map_info_sha256": sha(table),
        "map_info_source_sha256": sha(source.read_bytes()),
        "models_by_host_page_bytes": models,
        "required_alias_equivalence": {hex(address): hex(offset) for address, offset in zip(addresses, offsets)},
        "rounded_e0_counterexample": {"guest": "0xe0000000", "required_offset": "0x100001000",
                                      "incorrect_16kb_rounded_offset": "0x100000000"},
        "functions": records,
        "runtime_executed": False, "apple_mapping_verified": False,
        "limitations": [
            "Function labels are inferred from call flow and source correspondence, not recovered symbols.",
            "Atomic helper inventory is bounded to the recognized GCC helper cluster; inline atomics elsewhere remain unaudited.",
            "Arithmetic proves a page-phase mismatch under a page-aligned shared-mapping model; it is not an Apple runtime API test.",
            "RexResumeFromException is assembly without FDE metadata; its bounded window requires separate hook policy.",
            "The checked-in SDK source and NRO share the exact map table, not an established complete source/binary identity.",
        ],
    }
    (output / "memory.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"output": str(output), "map_info": hex(location), "e0_direct_16kb_alias": False,
                      "required_e0_phase": "0x1000", "runtime_executed": False}))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nro", type=Path, default=Path("switch/prototype-nx/prototype.nro"))
    parser.add_argument("--sdk", type=Path, default=Path("references/masseffect-nx/sdk"))
    parser.add_argument("--boundaries", type=Path, default=Path("work/analysis/boundaries.json"))
    parser.add_argument("--out", type=Path, default=Path("work/analysis/memory"))
    args = parser.parse_args()
    analyze(args.nro, args.sdk, args.boundaries, args.out)


if __name__ == "__main__":
    main()
