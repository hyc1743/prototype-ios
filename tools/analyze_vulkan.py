#!/usr/bin/env python3
"""Recover Prototype's Vulkan export boundary from its hash-bound name lookup.

All work is static. Lookup names, return GOT entries, wrapper FDEs, direct
branches and adjacent address-taking instructions are checked independently.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

from ci_guest import PINNED_SHA256
from nro import Nro

LOOKUP_START = 0x2913A80
LOOKUP_BYTES = 0x6024
STRCMP = 0x2DAA900
RESOLVERS = {"vkGetInstanceProcAddr": 0x2919B38, "vkGetDeviceProcAddr": 0x2919AA4}
SPECIAL_BRIDGES = {"vkCreateViSurfaceNN"}


def _immediate(operands):
    match = re.search(r"#(-?0x[0-9a-f]+|-?[0-9]+)", operands)
    if not match:
        raise ValueError("Expected an immediate operand: " + operands)
    return int(match.group(1), 0)


def recover_lookup(source, *, start=LOOKUP_START, size=LOOKUP_BYTES, strcmp=STRCMP, minimum_exports=700):
    """Decode the bounded lookup; every strcmp call must have a verified result."""
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    assembly = list(md.disasm(source[start:start + size], start))
    if sum(i.size for i in assembly) != size:
        raise ValueError("Vulkan lookup disassembly is incomplete")
    indices = {i.address: n for n, i in enumerate(assembly)}
    records = []
    names = set()
    for index, instruction in enumerate(assembly):
        if instruction.mnemonic != "bl" or _immediate(instruction.op_str) != strcmp:
            continue
        before = assembly[max(0, index - 4):index]
        pairs = [(before[n], before[n + 1]) for n in range(len(before) - 1)
                 if before[n].mnemonic == "adrp" and before[n].op_str.startswith("x1,") and
                 before[n + 1].mnemonic == "add" and before[n + 1].op_str.startswith("x1, x1,")]
        if len(pairs) != 1:
            raise ValueError("Unrecognized Vulkan name literal at " + hex(instruction.address))
        adrp, add = pairs[0]
        literal = _immediate(adrp.op_str) + _immediate(add.op_str)
        terminator = source.find(b"\0", literal, literal + 160)
        if literal < 0 or terminator < literal:
            raise ValueError("Invalid Vulkan export name address")
        try:
            name = source[literal:terminator].decode("ascii")
        except UnicodeDecodeError:
            raise ValueError("Non-ASCII Vulkan export name") from None
        if not re.fullmatch(r"vk[A-Za-z0-9_]+", name) or name in names:
            raise ValueError("Invalid or repeated Vulkan export name")
        after = assembly[index + 1:index + 6]
        if after[0].mnemonic == "cbz" and after[0].op_str.startswith("w0,"):
            return_address = _immediate(after[0].op_str)
            if return_address not in indices:
                raise ValueError("Vulkan return block escapes bounded lookup")
            ret = assembly[indices[return_address]:indices[return_address] + 2]
            result_kind = "strcmp-cbz-got"
        elif (len(after) == 5 and after[0].mnemonic == "cmp" and after[0].op_str == "w0, #0" and
              after[3].mnemonic == "csel" and after[3].op_str == "x0, x0, xzr, eq"):
            # The final comparison uses CMP/conditional-select rather than CBZ.
            ret = after[1:3]
            return_address = ret[0].address
            result_kind = "strcmp-cmp-csel-got"
        else:
            raise ValueError("Unrecognized Vulkan comparison result at " + hex(instruction.address))
        if (len(ret) != 2 or ret[0].mnemonic != "adrp" or not ret[0].op_str.startswith("x0,") or
                ret[1].mnemonic != "ldr" or not re.fullmatch(r"x0, \[x0(?:, #(0x[0-9a-f]+|[0-9]+))?\]", ret[1].op_str)):
            raise ValueError("Unrecognized Vulkan GOT result block for " + name + ": " + repr([(i.mnemonic, i.op_str) for i in ret]))
        pointer = _immediate(ret[0].op_str) + (_immediate(ret[1].op_str) if "#" in ret[1].op_str else 0)
        if pointer < 0 or pointer + 8 > len(source) or pointer % 8:
            raise ValueError("Invalid Vulkan export GOT pointer")
        function = struct.unpack_from("<Q", source, pointer)[0]
        records.append({"name": name, "offset": function, "name_offset": literal,
                        "strcmp_site": instruction.address, "return_block": return_address,
                        "pointer_offset": pointer, "result_kind": result_kind})
        names.add(name)
    if len(records) < minimum_exports:
        raise ValueError("Incomplete Vulkan export inventory")
    return records


def _signed_21(word):
    value = ((word >> 29) & 3) | (((word >> 5) & 0x7FFFF) << 2)
    return value - (1 << 21) if value & (1 << 20) else value


def scan_references(source, exports, *, prefix_start, prefix_end, wrapper_ranges):
    """Decode B/BL and exact adjacent ADRP+LDR/ADD or ADR address references.

    Register tracking across unrelated instructions is deliberately avoided: a
    stale ADRP value otherwise fabricates references through overwritten regs.
    """
    by_address = {record["offset"]: record for record in exports}
    references = {}
    def record(target, site, kind, pointer=None):
        if target not in by_address:
            if any(lo <= target < hi for lo, hi in wrapper_ranges):
                raise ValueError("Unmapped Vulkan wrapper reference at " + hex(site) + " -> " + hex(target))
            return
        item = {"site": site, "kind": kind}
        if pointer is not None:
            item["pointer_offset"] = pointer
        references.setdefault(target, []).append(item)
    for pc in range(prefix_start, prefix_end, 4):
        word = struct.unpack_from("<I", source, pc)[0]
        if word & 0x7C000000 == 0x14000000:
            immediate = word & 0x03FFFFFF
            if immediate & (1 << 25):
                immediate -= 1 << 26
            record(pc + immediate * 4, pc, "bl" if word & 0x80000000 else "b")
        if word & 0x9F000000 == 0x10000000:
            record(pc + _signed_21(word), pc, "adr")
        if word & 0x9F000000 != 0x90000000 or pc + 8 > prefix_end:
            continue
        following = struct.unpack_from("<I", source, pc + 4)[0]
        if word & 31 != (following >> 5) & 31:
            continue
        page = (pc & ~4095) + (_signed_21(word) << 12)
        if following & 0xFFC00000 == 0xF9400000:
            pointer = page + (((following >> 10) & 0xFFF) * 8)
            if 0 <= pointer <= len(source) - 8:
                record(struct.unpack_from("<Q", source, pointer)[0], pc, "adrp-ldr-address", pointer)
        elif following & 0xFFC00000 == 0x91000000:
            record(page + ((following >> 10) & 0xFFF), pc, "adrp-add-address")
    return references


def analyze(source, boundary_report):
    image = Nro(source)
    if image.sha256 != PINNED_SHA256 or boundary_report.get("source_nro_sha256") != image.sha256:
        raise ValueError("Vulkan analysis input digest mismatch")
    fdes = {f["start"]: f for f in boundary_report["functions"]}
    if fdes.get(LOOKUP_START, {}).get("size") != LOOKUP_BYTES:
        raise ValueError("Unexpected Vulkan lookup FDE")
    exports = recover_lookup(source)
    for export in exports:
        function = export["offset"]
        if function not in fdes or not 0x80 <= function < image.segments[0].end:
            raise ValueError("Vulkan export is not a text FDE: " + export["name"])
        export["fde_bytes"] = fdes[function]["size"]
        export["expected_original"] = source[function:function + 16].hex()
    addresses = {x["offset"] for x in exports}
    if len(addresses) != len(exports):
        raise ValueError("Aliased Vulkan export targets require explicit review")
    for name, offset in RESOLVERS.items():
        if not any(x["name"] == name and x["offset"] == offset for x in exports):
            raise ValueError("Vulkan resolver identity mismatch")
    wrapper_start = min(addresses)
    global_end = max(x["offset"] + x["fde_bytes"] for x in exports)
    references = scan_references(source, exports, prefix_start=0x80, prefix_end=wrapper_start,
                                 wrapper_ranges=[(wrapper_start, LOOKUP_START), (LOOKUP_START + LOOKUP_BYTES, global_end)])
    chosen = set(references) | set(RESOLVERS.values())
    by_address = {x["offset"]: x for x in exports}
    ordered = [RESOLVERS["vkGetInstanceProcAddr"], RESOLVERS["vkGetDeviceProcAddr"]]
    ordered.extend(sorted(chosen - set(ordered)))
    if len(ordered) > 1024:
        raise ValueError("Vulkan hooks exceed reserved slots")
    hooks = []
    for slot, offset in enumerate(ordered):
        item = by_address[offset]
        hooks.append({"offset": offset, "slot": slot, "symbol": item["name"], "kind": "vulkan",
                      "expected_original": item["expected_original"],
                      "evidence": "tools/analyze_vulkan.py: pinned lookup literal -> GOT -> exact wrapper FDE; prefix branch/address references",
                      "export_evidence": item, "call_sites": references.get(offset, [])})
    return {"source_nro_sha256": image.sha256, "lookup_offset": LOOKUP_START, "lookup_bytes": LOOKUP_BYTES,
            "lookup_sha256": hashlib.sha256(source[LOOKUP_START:LOOKUP_START + LOOKUP_BYTES]).hexdigest(),
            "export_count": len(exports), "prefix_start": 0x80, "prefix_end": wrapper_start,
            "coverage": "All decoded B/BL and adjacent ADRP+LDR/ADD or ADR taking mapped wrapper addresses from game/SDK prefix; unmapped references into wrapper ranges fail closed. Other indirect feature paths require runtime verification.",
            "exports": exports, "hooks": hooks}


def update_profile(profile, report):
    if profile.get("source_nro_sha256") != report["source_nro_sha256"]:
        raise ValueError("Hook profile identifies another guest")
    retained = [h for h in profile["hooks"] if h["slot"] >= 1024]
    if len({h["slot"] for h in retained}) != len(retained):
        raise ValueError("Repeated retained runtime slot")
    result = dict(profile)
    result["hooks"] = report["hooks"] + retained
    result["vulkan_analysis"] = {key: value for key, value in report.items() if key != "hooks"}
    if len({h["offset"] for h in result["hooks"]}) != len(result["hooks"]):
        raise ValueError("Vulkan hook overlaps an existing runtime hook")
    return result


def exports_text(existing, hooks):
    names = set(re.findall(r"^PROTOTYPE_VK_EXPORT\((vk[A-Za-z0-9_]+)\)$", existing, re.MULTILINE))
    names.update(h["symbol"] for h in hooks)
    names -= SPECIAL_BRIDGES
    return ("/* Reviewed host Vulkan symbols; static boundary additions come from the pinned Prototype lookup. */\n" +
            "".join("PROTOTYPE_VK_EXPORT(" + name + ")\n" for name in sorted(names)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nro", type=Path, default=Path("switch/prototype-nx/prototype.nro"))
    parser.add_argument("--boundaries", type=Path, default=Path("work/analysis/boundaries.json"))
    parser.add_argument("--profile", type=Path, default=Path("ios/runtime/guest-hooks.json"))
    parser.add_argument("--exports", type=Path, default=Path("ios/runtime/PrototypeVulkanExports.inc"))
    args = parser.parse_args(argv)
    report = analyze(args.nro.read_bytes(), json.loads(args.boundaries.read_text(encoding="utf-8")))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    updated = update_profile(profile, report)
    linked = exports_text(args.exports.read_text(encoding="utf-8"), report["hooks"])
    args.profile.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    args.exports.write_text(linked, encoding="utf-8")
    print(json.dumps({"recovered_exports": report["export_count"], "vulkan_hooks": len(report["hooks"]),
                      "retained_runtime_hooks": len(updated["hooks"]) - len(report["hooks"]),
                      "static_symbols": [h["symbol"] for h in report["hooks"]], "lookup_sha256": report["lookup_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
