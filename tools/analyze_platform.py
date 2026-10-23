"""Reproduce bounded, offline platform evidence for the exact Prototype NRO.

This is static byte/disassembly evidence, not a runtime test or REA evidence.
Requires capstone; never executes the NRO or downloads analysis engines.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

import capstone

from nro import Nro

SOURCE_SHA256 = "16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330"

# Bounded disassembly windows, not asserted function boundaries. Identity and
# entry/branch assertions below prevent reuse on a different executable build.
WINDOWS = {
    "entry": (0x80, 0x70),
    "libnx_init": (0x2D07FB0, 0x2C),
    "app_init": (0x2D07E70, 0xEC),
    "init_heap": (0x2D07DA0, 0xD0),
    "init_thread_vars": (0x2D08910, 0x98),
    "get_reent": (0x2D08020, 0x34),
    "newlib_get_reent": (0x2DBB1C0, 0x20),
    "find_device": (0x2DBB6E0, 0xB8),
    "add_device": (0x2DBB7E0, 0xA4),
    "close_reent": (0x2DBB5A0, 0xF4),
    "main_prefix": (0x58510, 0x80),
    "fsdev_fstat": (0x2D09520, 0xEC),
    "fsdev_stat_prefix": (0x2D096B0, 0x17C),
    "fseek_stat_size": (0x2DA5F2C, 0x34),
    "sdk_guest_memory_setup": (0x227EFC, 0xE0),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def branch(data: bytes, address: int, link: bool) -> int:
    word = struct.unpack_from("<I", data, address)[0]
    assert word & 0xFC000000 == (0x94000000 if link else 0x14000000)
    value = word & 0x03FFFFFF
    if value & (1 << 25):
        value -= 1 << 26
    return address + value * 4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nro", type=Path, default=Path("switch/prototype-nx/prototype.nro"))
    parser.add_argument("--output", type=Path, default=Path("work/analysis/platform"))
    parser.add_argument("--boundaries", type=Path, default=Path("work/analysis/boundaries.json"))
    args = parser.parse_args()
    nro = Nro(args.nro.read_bytes())
    if nro.sha256 != SOURCE_SHA256:
        raise ValueError("platform evidence is bound to Prototype build " + SOURCE_SHA256)
    data = nro.data
    assert branch(data, 0xC0, True) == 0x2D07FB0
    assert branch(data, 0xDC, False) == 0x58510
    assert branch(data, 0x2D07FD0, True) == 0x2D07E70
    assert struct.unpack_from("<Q", data, 0x3CA1270)[0] == 0x3D9DFB0
    assert data[0x2DBB6E0:0x2DBB6F0].hex() == "fd7bbca941078052fd030091f76303a9"
    assert data[0x2D07E70:0x2D07E80].hex() == "fd7bafa9fd030091faefff97c0050035"
    args.output.mkdir(parents=True, exist_ok=True)
    disassembler = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    windows = []
    for name, (address, size) in WINDOWS.items():
        body = data[address:address + size]
        instructions = list(disassembler.disasm(body, address))
        assert sum(item.size for item in instructions) == size
        filename = name + ".asm"
        text = "# Bounded original NRO window; inferred labels, no asserted function boundary.\n"
        text += "\n".join(f"{item.address:#010x}  {item.bytes.hex()}  {item.mnemonic} {item.op_str}"
                          for item in instructions) + "\n"
        (args.output / filename).write_text(text, encoding="utf-8")
        windows.append({"name": name, "start": hex(address), "size": size,
                        "sha256": digest(body), "first16": body[:16].hex(), "file": filename})

    pointers = {}
    for name, address in {
        "devoptab_list": 0x3CA1270,
        "impure_ptr": 0x3CA2CB0,
        "tls_start": 0x3CA6A48,
        "tls_alignment": 0x3CA6300,
        "heap_start": 0x3CA4030,
        "heap_end": 0x3CA3CC8,
        "requested_heap_size": 0x3CA7590,
    }.items():
        pointers[name] = {"got_address": hex(address), "unrelocated_value": hex(struct.unpack_from("<Q", data, address)[0]),
                          "bytes": data[address:address + 8].hex()}
    symbols = {
        "__libnx_init": 0x2D07FB0,
        "main": 0x58510,
        "__stack_top": 0x4022418,
        "appInit": 0x2D07E70,
        "initHeap": 0x2D07DA0,
        "findDevice": 0x2DBB6E0,
        "devoptab_list": 0x3D9DFB0,
        "default_device": 0x3D9E0C8,
        "__libc_init_array": 0x2DA44C0,
        "newlib_get_reent": 0x2DBB1C0,
        "libnx_get_reent": 0x2D08020,
        "fsdev_fstat": 0x2D09520,
        "fsdev_stat": 0x2D096B0,
        "sdk_Memory_Initialize": 0x227DC0,
    }
    report = {
        "authority": "standalone-python-capstone-static-byte-evidence",
        "source_nro_sha256": nro.sha256,
        "capstone_version": capstone.__version__,
        "symbols": {name: hex(value) for name, value in symbols.items()},
        "hooks": [
            {"offset": "0x2d07e70", "symbol": "prototype_libnx_app_init", "expected": data[0x2D07E70:0x2D07E80].hex()},
            {"offset": "0x2dbb6e0", "symbol": "prototype_find_device", "expected": data[0x2DBB6E0:0x2DBB6F0].hex()},
        ],
        "pointers": pointers,
        "abi_observations": {
            "thread_vars_tls_offset": "0x1e0", "thread_vars_magic": "0x21545624",
            "thread_vars_reent_offset": "0x10", "thread_vars_tls_pointer_offset": "0x18",
            "devoptab_device_data_offset": "0xb0", "reent_device_data_offset": "0x238",
            "devoptab_list_entries": 35,
            "stat_size": 104, "stat_file_size_offset": 16,
            "stat_mode_offset": 4, "stat_nlink_offset": 8,
            "stat_timestamps_seconds_offsets": [24, 40, 56],
            "stat_blksize_semantics": "offset72 agrees with Biko3 ABI but no independent named semantic access found; libnx zeros this field",
        },
        "windows": windows,
        "limitations": [
            "Offline disassembly; no NRO execution or observed iOS behavior.",
            "Labels are inferred from entry data flow and Biko3 control-flow/ABI comparison, not recovered symbols.",
            "No REA MCP evidence is claimed; parent investigation found its Windows provider unavailable.",
            "Bounded windows are not FDE function boundaries; no whole-image control-flow coverage.",
            "Biko3 and Prototype findDevice implementations differ; Biko3 offsets or hashes must not be reused.",
        ],
    }
    if args.boundaries.is_file():
        boundary_bytes = args.boundaries.read_bytes()
        boundaries = json.loads(boundary_bytes)
        assert boundaries["source_nro_sha256"] == nro.sha256
        eh_start, eh_end = boundaries["eh_frame_start"], boundaries["eh_frame_end"]
        assert digest(data[eh_start:eh_end]) == boundaries["eh_frame_sha256"]
        fdes = {item["start"]: item for item in boundaries["functions"]}
        report["fde_evidence"] = {
            "file": str(args.boundaries), "sha256": digest(boundary_bytes),
            "functions": {name: fdes[address] for name, address in symbols.items() if address in fdes},
        }
    table_address = 0x3C67CF8
    table = data[table_address:table_address + 0x100]
    assert struct.unpack_from("<Q", table, 0x38)[0] == 0x2D09520
    assert struct.unpack_from("<Q", table, 0x40)[0] == 0x2D096B0
    report["fsdev_devoptab"] = {"address": hex(table_address), "bytes": table.hex(), "sha256": digest(table)}
    (args.output / "platform.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "source_nro_sha256": nro.sha256, "symbols": report["symbols"]}))


if __name__ == "__main__":
    main()
