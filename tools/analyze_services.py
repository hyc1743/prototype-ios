#!/usr/bin/env python3
"""Reproduce bounded, offline Prototype libnx service identification evidence.

This records static observations and source correlations. It never executes the
guest and is not evidence that the resulting iOS port runs correctly.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

from ci_guest import PINNED_SHA256
from nro import Nro


# Names are inferred from SDK source call order, constants and libnx behavior.
# Addresses and FDE extents are independently checked against the actual NRO.
FUNCTIONS = {
    "filesystem_GetExecutablePath": (0xCF100, "Reads __system_argc and __system_argv through GOT, checks sdmc prefix and stat; source filesystem_posix.cpp"),
    "filesystem_GetExecutableFolder": (0xCF560, "Calls GetExecutablePath then std::filesystem::path::parent_path; no direct getcwd call"),
    "RexGmInit": (0xDEEE0, "CreateFileMappingHandle source-correlated diagnostic function calls it with page-rounded mapping size"),
    "RexGmShutdown": (0xDF000, "Immediately follows Init in extern C source order; unmaps chunks, releases backing and reservation"),
    "RexGmBase": (0xDF1D0, "CreateFileMappingHandle/MapFileView/AllocFixed call this; atomic load of base"),
    "RexGmSize": (0xDF1E0, "MapFileView and AllocFixed compare end address with Base+Size"),
    "RexGmWindowToOffset": (0xDF1F0, "AllocFixed passes guest host address plus size_t output before Commit"),
    "RexGmCommittedBytes": (0xDF2D0, "AllocFixed failure diagnostic obtains this before ChunkCount and MappedBytes"),
    "RexGmChunkCount": (0xDF330, "AllocFixed failure diagnostic source call sequence; state+0x68 is map size"),
    "RexGmMappedBytes": (0xDF390, "AllocFixed failure diagnostic source call sequence; state+0x108"),
    "RexGmLastResult": (0xDF3F0, "AllocFixed and MapFileView failure diagnostics use returned u32; state+0x110"),
    "RexGmAddView": (0xDF450, "MapFileView passes base, backing offset and length in source order"),
    "RexGmCommit": (0xDF640, "AllocFixed passes mapping offset, length and permission enum before Protect"),
    "RexGmFaultIn": (0xDFCA0, "One-address argument, view lookup and MapChunkIntoView helper; source and Prototype SDK patch"),
    "RexGmMapCommittedRange": (0xDFF00, "Two-argument eager range mapper added in prototype-switch SDK patch; AllocFixed uses for writable allocations"),
    "RexGmProtect": (0xE0220, "AllocFixed passes address,length,access,NULL after Commit; native renderer read-only bookkeeping patch present"),
    "RexGmQueryProtect": (0xE0F40, "Next source-correlated extern C function after Protect; writes output size and access enum"),
    "RexGmShadowFor": (0xE1410, "One-address argument, view/committed chunk lookup, returns writable shadow alias"),
    "RexGmSetReadOnlyMapped": (0xE1560, "Exact Prototype SDK patch adds bool setter protected by state mutex after ShadowFor"),
    "appletGetAppletType": (0x2CFC290, "SDK context Initialize tests the result before setting focus handling"),
    "appletHook": (0x2CFC2A0, "SDK context Initialize passes cookie, callback and context"),
    "appletUnhook": (0x2CFC2D0, "Adjacent linked-list unlink walks appletHook cookie chain and removes matching cookie"),
    "appletSetFocusHandlingMode": (0x2CFC310, "SDK Initialize passes enum value 2 before installing hook"),
    "appletGetDefaultDisplayResolution": (0x2CFC4D0, "WindowSwitch::QueryDisplayResolution passes two s32 outputs"),
    "appletGetDefaultDisplayResolutionChangeEvent": (0x2CFC550, "SDK Initialize passes impl display-resolution Event output"),
    "appletGetOperationMode": (0x2CFE150, "SDK QueryDisplayResolution compares result to docked enum 1"),
    "appletGetFocusState": (0x2CFE160, "SDK OpenImpl compares result to InFocus enum 1"),
    "appletGetMessageEvent": (0x2CFE170, "SDK UI loop loads handle at result+0 and autoclear at result+8"),
    "appletMainLoop": (0x2CFE470, "UI loop exits when result bit 0 is clear; normalized full body matches Biko3 libnx appletMainLoop"),
    "audoutExit": (0x2CFE680, "SDK StartOutput error/StopOutput cleanup and Biko3 libnx audio-exit body"),
    "audoutGetSampleRate": (0x2CFE6D0, "SDK StartOutput compares to 48000"),
    "audoutGetChannelCount": (0x2CFE6E0, "SDK StartOutput compares to 2 channels"),
    "audoutGetPcmFormat": (0x2CFE6F0, "SDK StartOutput compares to PCM Int16 enum 2"),
    "audoutInitialize": (0x2CFE9C0, "SDK StartOutput call and encoded SM service audout:u"),
    "audoutStartAudioOut": (0x2CFEBA0, "SDK StartOutput call; sets IPC command 1; byte-identical Biko3 body"),
    "audoutStopAudioOut": (0x2CFEBB0, "SDK StopOutput call; sets IPC command 2; byte-identical Biko3 body"),
    "audoutAppendAudioOutBuffer": (0x2CFEBC0, "SDK FillAndAppend passes AudioOutBuffer pointer"),
    "audoutGetReleasedAudioOutBuffer": (0x2CFED90, "SDK OutputThreadMain drains released buffer and count"),
    "audoutWaitPlayFinish": (0x2CFEF20, "SDK OutputThreadMain passes buffer**, count*, timeout; body clears event, gets released, then waits if empty"),
    "hidInitialize": (0x2D01B10, "SwitchInputDriver::Setup call and SM service encoded as hid"),
    "hidGetNpadStyleSet": (0x2D01DE0, "padUpdate reads style before style-specific state; dereferences HID shared memory"),
    "hidInitializeTouchScreen": (0x2D01E70, "Adjacent wrapper passes HID device id 11 before abort-on-error path"),
    "hidGetTouchScreenStates": (0x2D01E90, "Unique normalized full-body match to Biko3 touch query"),
    "hidGetNpadStatesFullKey": (0x2D01FB0, "padUpdate branch when style bit 0 set"),
    "hidGetNpadStatesHandheld": (0x2D02060, "padUpdate handheld branch passes id 0x20"),
    "hidGetNpadStatesJoyDual": (0x2D02110, "padUpdate branch when style bit 2 set"),
    "hidGetNpadStatesJoyLeft": (0x2D021C0, "padUpdate branch when style bit 3 set"),
    "hidGetNpadStatesJoyRight": (0x2D02270, "padUpdate branch when style bit 4 set"),
    "hidGetNpadStatesGc": (0x2D02320, "padUpdate branch when style bit 5 set; analog triggers copied"),
    "hidGetNpadStatesSystemExt": (0x2D02470, "padUpdate branch when style bit 29 set"),
    "hidPermitVibration": (0x2D02A40, "SDK Setup passes true after pad initialization"),
    "hidInitializeVibrationDevices": (0x2D02630, "Validates 1-2 handles and style, writes handle bytes, creates active vibration list using IPC203 then activates handles"),
    "hidSendVibrationValues": (0x2D02A50, "IPC206; handle array byte length=count*4 and value array length=count*16, matches libnx source"),
    "nwindowGetDefault": (0x2D061F0, "WindowSwitch::OpenImpl first call; returns static image address 0x401fa58"),
    "nwindowIsValid": (0x2D068C0, "OpenImpl checks default window; compares first u32 with 0x6e69574e"),
    "nwindowGetDimensions": (0x2D06AB0, "Matches libnx validation and current/default dimension output selection"),
    "nwindowSetDimensions": (0x2D06B40, "Matches libnx validity, slots-configured guard, width/height stores and crop clear"),
    "padConfigureInput": (0x2D08AA0, "SDK Setup passes 4 players and style mask 0x1f; normalized unique Biko3 match"),
    "padInitializeWithMask": (0x2D08B40, "SDK padInitialize inline wrapper passes 64-bit mask incl handheld bit32"),
    "padUpdate": (0x2D08B70, "SDK Poll calls it; preserves prior held at+24, zeros new held+16/sticks+32 and dispatches HID styles"),
    "argvSetup": (0x2D07700, "libnx initialization calls before constructors; initializes argc/argv globals, gets optional command line and parses into heap"),
    "envHasArgv": (0x2D07CC0, "Checks nonzero command-line pointer at image+0x401fba8"),
    "envGetArgv": (0x2D07CE0, "Loads optional command-line pointer at image+0x401fba8"),
}

REFERENCE = {
    "appletMainLoop": 0x4FF010,
    "audoutExit": 0x4FF5F0,
    "audoutGetSampleRate": 0x4FF640,
    "audoutGetChannelCount": 0x4FF650,
    "audoutGetPcmFormat": 0x4FF660,
    "audoutInitialize": 0x4FF930,
    "audoutStartAudioOut": 0x4FFB10,
    "audoutStopAudioOut": 0x4FFB20,
    "audoutAppendAudioOutBuffer": 0x4FFB30,
    "audoutGetReleasedAudioOutBuffer": 0x4FFD00,
    "hidGetTouchScreenStates": 0x5006A0,
    "padConfigureInput": 0x504710,
}

ANCHORS = {
    "WindowSwitch: no default NWindow": 0x12D040,
    "SwitchAudioSystem: audoutInitialize failed: 0x{:08X}": 0x19B280,
    "SwitchInputDriver: hidInitialize failed: 0x{:08X}": 0x1AC460,
    "Could not reserve the guest window of 0x{:X} bytes. Title takeover is required: in applet mode there is no room.": 0xE3F30,
    "MapFileView: could not register the view 0x{:016X} (0x{:X} bytes from 0x{:X}); failure 0x{:08X}": 0xE3810,
    "AllocFixed: could not commit 0x{:X} bytes at 0x{:016X}. Failure 0x{:08X}; backing {} MB in {} chunks, mapped {} MB counting mirrors.": 0xE2010,
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized(assembly):
    # Comparison removes numeric immediates, preserving opcodes and registers.
    return [ins.mnemonic + " " + re.sub(r"#-?(?:0x[0-9a-f]+|[0-9]+)", "#", ins.op_str)
            for ins in assembly]


def analyze(source, boundaries, strings, reference_nro=None, reference_boundaries=None):
    data = Path(source).read_bytes()
    nro = Nro(data)
    if nro.sha256 != PINNED_SHA256:
        raise ValueError("Wrong Prototype guest digest")
    boundary_data = Path(boundaries).read_bytes()
    boundary_report = json.loads(boundary_data)
    if boundary_report["source_nro_sha256"] != nro.sha256:
        raise ValueError("Function boundaries belong to another guest")
    functions = boundary_report["functions"]
    by_start = {f["start"]: f for f in functions}
    starts = sorted(by_start)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    string_rows = json.loads(Path(strings).read_text(encoding="utf-8"))
    anchor_addresses = {o: s for o, s in string_rows if s in ANCHORS}
    anchor_refs = {o: [] for o in anchor_addresses}
    recent_adrp = {}
    targets = {off: name for name, (off, _) in FUNCTIONS.items()}
    calls = {off: [] for off in targets}
    # Decode immediate BL directly, preserving exact source addresses.
    for off in range(0x80, nro.segments[0].end, 4):
        word = struct.unpack_from("<I", data, off)[0]
        if word & 0x9F000000 == 0x90000000:
            imm_page = ((word >> 29) & 3) | (((word >> 5) & 0x7FFFF) << 2)
            if imm_page & (1 << 20):
                imm_page -= 1 << 21
            recent_adrp[word & 31] = (off, (off & ~4095) + (imm_page << 12))
        elif word & 0xFFC00000 == 0x91000000:
            source_register = (word >> 5) & 31
            if source_register in recent_adrp:
                page_site, page = recent_adrp[source_register]
                address = page + ((word >> 10) & 0xFFF)
                if off - page_site < 32 and address in anchor_refs:
                    start = starts[bisect_right(starts, off) - 1]
                    anchor_refs[address].append({"adrp": hex(page_site), "add": hex(off), "containing_fde": hex(start)})
        if word & 0xFC000000 != 0x94000000:
            continue
        imm = word & 0x03FFFFFF
        if imm & 0x02000000:
            imm -= 0x04000000
        target = off + imm * 4
        if target in calls:
            start = starts[bisect_right(starts, off) - 1]
            calls[target].append({"site": hex(off), "containing_fde": hex(start)})
    reference_data, reference_index = None, {}
    if reference_nro and Path(reference_nro).is_file() and reference_boundaries and Path(reference_boundaries).is_file():
        reference_data = Path(reference_nro).read_bytes()
        if digest(reference_data) != "31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178":
            raise ValueError("Wrong Biko3 reference digest")
        reference_index = {f["start"]: f for f in json.loads(Path(reference_boundaries).read_text())["functions"]}
    result = []
    for name, (off, evidence) in FUNCTIONS.items():
        fde = by_start[off]
        body = data[off:off + fde["size"]]
        asm = list(md.disasm(body, off))
        if sum(i.size for i in asm) != len(body):
            raise ValueError("Incomplete disassembly at " + hex(off))
        record = {"name": name, "offset": hex(off), "expected": data[off:off + 16].hex(),
                  "fde_bytes": fde["size"], "fde_offset": hex(fde["fde_offset"]),
                  "body_sha256": digest(body), "identification": "static source correlation",
                  "evidence": evidence, "callers": calls[off],
                  "assembly": [{"address": hex(i.address), "mnemonic": i.mnemonic, "operands": i.op_str} for i in asm]}
        if reference_data and name in REFERENCE:
            ref_off = REFERENCE[name]
            ref_len = reference_index[ref_off]["size"]
            ref_body = reference_data[ref_off:ref_off + ref_len]
            record["biko3_comparison"] = {"offset": hex(ref_off), "fde_bytes": ref_len,
                "byte_identical": body == ref_body,
                "normalized_body_equal": normalized(asm) == normalized(md.disasm(ref_body, ref_off))}
        result.append(record)
    anchors = [{"string": s, "address": hex(o), "source_correlated_function": hex(ANCHORS[s]), "references": anchor_refs[o]}
               for o, s in string_rows if s in ANCHORS]
    if len(anchors) != len(ANCHORS) or any(not any(x["containing_fde"] == row["source_correlated_function"] for x in row["references"]) for row in anchors):
        raise ValueError("Source anchor did not resolve to its expected FDE")
    memory_map_offset = 0x2F3F5B0
    memory_map = [{"guest_start": hex(a), "guest_end": hex(b), "backing_offset": hex(c)}
                  for a, b, c in struct.iter_unpack("<QQQ", data[memory_map_offset:memory_map_offset + 9 * 24])]
    if memory_map[7] != {"guest_start": "0xe0000000", "guest_end": "0xffffffff", "backing_offset": "0x100001000"}:
        raise ValueError("Unexpected Xbox physical mirror skew")
    if struct.unpack_from("<Q", data, 0x3CA5B10)[0] != 0x401FB48 or struct.unpack_from("<Q", data, 0x3CA4068)[0] != 0x401FB40:
        raise ValueError("Guest argv GOT entries differ from reviewed startup")
    return {"schema": 1, "authority": "offline-capstone-and-fde-source-correlation",
        "source_nro_sha256": nro.sha256, "source_nro_bytes": len(data),
        "boundaries_sha256": digest(boundary_data),
        "libnx_source_reference": {"repository": "switchbrew/libnx", "commit": "b156b4774fb2df7b93402169509266da7547fc4c",
            "files": ["nx/source/runtime/pad.c", "nx/include/switch/runtime/pad.h", "nx/source/display/native_window.c", "nx/include/switch/display/native_window.h", "nx/source/services/hid.c", "nx/source/runtime/argv.c", "nx/source/runtime/env.c", "nx/include/switch/runtime/env.h"]},
        "prototype_sdk_patch": {"path": "references/prototype-switch/patches/rexglue-sdk-masseffect-nx.patch", "base_repository": "nebadasSwifty/masseffect-nx", "base_revision": "a85b0b4", "evidence": "Adds MapCommittedRange, SetReadOnlyMapped and native readonly_logical handling visible in this guest"},
        "source_anchors": anchors, "functions": result,
        "abi": {
            "libnx_argv": {"system_argc_offset": "0x401fb48", "system_argv_offset": "0x401fb40", "optional_command_line_offset": "0x401fba8", "argc_got_offset": "0x3ca5b10", "argv_got_offset": "0x3ca4068", "config_entry_bytes": 24, "config_entry_argv_key": 5, "config_value0": "unused", "config_value1": "pointer to NUL-terminated command-line text, not char**", "setup_heap_bytes": "0x9000", "crt_argc_load": "0xc4..0xc8", "crt_argv_load": "0xcc..0xd0", "crt_d4_d8": "main return address load, unrelated to argv"},
            "xbox_memory_map": {"table_offset": hex(memory_map_offset), "rows": memory_map, "current_allocation_granularity": 4096, "current_allocation_granularity_function": "0xe1fe0", "e0_skew_bytes": 4096, "source_granularity_adaptation": "MapViews masks backing offsets to allocation granularity; SDK implements physical 4KB skew adjustment for larger host allocation granularity; runtime verification remains required"},
            "PadState": {"bytes": 56, "fields": {"id_mask": 0, "active_id_mask": 1, "read_handheld": 2, "active_handheld": 3, "style_set": 4, "attributes": 8, "buttons_cur": 16, "buttons_old": 24, "left_x": 32, "left_y": 36, "right_x": 40, "right_y": 44, "gc_trigger_l": 48, "gc_trigger_r": 52}, "buttons_down": "current & ~previous", "buttons_up": "previous & ~current", "connected_attribute_bit": 0},
            "AudioOutBuffer": {"bytes": 40, "fields": {"next": 0, "buffer": 8, "buffer_size": 16, "data_size": 24, "data_offset": 32}},
            "audio_output": {"sample_rate": 48000, "channels": 2, "pcm_format": 2, "sample_bits": 16, "buffer_pcm_bytes": 4096, "frames_per_buffer": 1024, "queued_buffers": 3},
            "NWindow": {"bytes": 128, "default_address": "0x401fa58", "fields": {"magic": 0, "slots_configured": 48, "width": 68, "height": 72, "crop": 84, "default_width": 112, "default_height": 116}, "magic": "0x6e69574e"}},
        "unresolved": ["RexGmDecommit and RexGmProtectionMode have no established standalone FDE in this report; they may have been removed by linking, but absence is not proven.", "Later SDK RexGmConfigure/LargePagesMode/LayoutSummary/TlbBenchmark functions are not established in this older guest.", "GetExecutablePath/GetExecutableFolder use argv globals without directly calling getcwd; independent std::filesystem absolute operations may still need hosted cwd support."],
        "limitations": ["Symbol names are inferred from source call order and constants, not recovered debug symbols.", "Current upstream libnx is an ABI cross-check, not proof of the historical build's exact source commit.", "All hook offsets, bytes and extents are checked against the pinned supplied guest; no guest execution was performed.", "No evidence here proves iOS gameplay, audio playback, input delivery, or Vulkan presentation.", "REA native provider was unavailable (Ghidra unconfigured); observations are ordinary shell/Capstone analysis, not REA Evidence."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("switch/prototype-nx/prototype.nro"))
    parser.add_argument("--boundaries", type=Path, default=Path("work/analysis/boundaries.json"))
    parser.add_argument("--strings", type=Path, default=Path("work/analysis/strings.json"))
    parser.add_argument("--reference-nro", type=Path, default=Path("../weixing3/switch/biko3/biko3-runtime.nro"))
    parser.add_argument("--reference-boundaries", type=Path, default=Path("../weixing3/work/biko3-ios/nxnative/overlay/function-boundaries.json"))
    parser.add_argument("--output", type=Path, default=Path("work/analysis/services.json"))
    args = parser.parse_args()
    report = analyze(args.source, args.boundaries, args.strings, args.reference_nro, args.reference_boundaries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"functions": len(report["functions"]), "source_anchors": len(report["source_anchors"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
