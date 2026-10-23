#!/usr/bin/env python3
"""Execute the actual staged Prototype guest on native arm64 macOS.

The default run uses fresh empty assets and proves only guest startup and normal
return. It does not verify iOS execution, rendering, or game playability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import time

from ci_guest import PINNED_SHA256
from rewrite_nro import COMMIT
from stage_ios import PROFILE


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def smoke_makefile(text, moltenvk):
    """Append Mac build flags to the staged runtime's actual source list."""
    runtime = {"PrototypeLibnx.c", "PrototypeFS.c", "PrototypeHooks.c",
               "PrototypeAudio.c", "PrototypeVulkan.c"}
    source = re.findall(r"^SRC=(.*)$", text, re.MULTILINE)
    if len(source) != 1 or not runtime.issubset(source[0].split()):
        raise ValueError("staged host Makefile does not compile Prototype runtime sources")
    library = moltenvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
    text, count = re.subn(r"^CFLAGS=(.*)$", lambda match: match.group(0) +
                         " -arch arm64 -mcpu=apple-m2 -I" + shlex.quote(str(moltenvk / "include")),
                         text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("staged Makefile CFLAGS anchor changed")
    anchor = " -framework UniformTypeIdentifiers"
    if text.count(anchor) != 1:
        raise ValueError("staged Makefile macOS link anchor changed")
    return text.replace(anchor, anchor + " " + shlex.quote(str(library)) +
                        " -lc++ -framework IOSurface -framework AppKit -framework CoreGraphics -framework IOKit", 1)


def windowed_frontend(text):
    """Connect the Mac layer and stop AppKit after the real guest returns."""
    marker = 'getenv("PROTOTYPE_MAC_SMOKE_EXIT")'
    if marker in text:
        return text
    anchor = '    ((guest_arg *)p)->fn(); LOG("guest thread finished"); exit(0);'
    layer = '        g_layer = [CAMetalLayer layer];'
    if text.count(anchor) != 1 or text.count(layer) != 1:
        raise ValueError("staged front_mac guest completion or Metal layer anchor changed")
    text = '#include "PrototypeVulkan.h"\n' + text
    text = text.replace(layer, layer + '\n        prototype_vk_set_metal_layer((__bridge void *)g_layer);', 1)
    return text.replace(anchor, '''    ((guest_arg *)p)->fn(); LOG("guest thread finished");
    if (getenv("PROTOTYPE_MAC_SMOKE_EXIT")) {
        dispatch_async(dispatch_get_main_queue(), ^{
            [NSApp stop:nil];
            NSEvent *wake = [NSEvent otherEventWithType:NSEventTypeApplicationDefined
                location:NSZeroPoint modifierFlags:0 timestamp:0 windowNumber:0
                context:nil subtype:0 data1:0 data2:0];
            [NSApp postEvent:wake atStart:YES];
        });
        return NULL;
    }
    exit(0);''', 1)


def runtime_evidence(log, exit_code):
    initialized = re.search(r"^\[prototype\] original libnx init completed\r?$", log, re.MULTILINE)
    entered = re.search(r"^\[prototype\] entering original main \+0x58510\r?$", log, re.MULTILINE)
    returned = re.search(r"^\[prototype\] original main (?:returned|exited) (-?\d+)(?: via native platform)?\r?$", log, re.MULTILINE)
    crash = re.search(r"\[dm\] CRASH sig=|\[RED\] watchdog:|EXC_BAD_ACCESS|Segmentation fault|Abort trap", log)
    checks = {
        "libnx_initialized": initialized is not None,
        "original_main_entered": entered is not None,
        "original_main_returned": returned is not None,
        "startup_trace_ordered": bool(initialized and entered and returned and
                                      initialized.start() < entered.start() < returned.start()),
        "host_returned_normally": type(exit_code) is int and exit_code == 0,
        "no_crash_reported": crash is None,
    }
    return checks, int(returned.group(1)) if returned else None


def run_smoke(staged, moltenvk, prefix, timeout=120, windowed=False, game_root=None):
    prefix = Path(prefix).resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(str(prefix) + ".json")
    build_log, run_log = Path(str(prefix) + ".build.log"), Path(str(prefix) + ".run.log")
    report = {
        "schema": 1, "status": "failed", "verified": False,
        "platform": platform.system(), "architecture": platform.machine(),
        "ios_device_execution": False, "gameplay_verified": False, "rendering_verified": False,
        "mode": "windowed" if windowed else "headless", "empty_assets": game_root is None,
        "scope": "real arm64 macOS guest libnx startup and main return; no iOS or gameplay verification",
        "build_log": build_log.name, "run_log": run_log.name,
    }
    started = time.monotonic()
    try:
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            report.update(status="skipped", reason="requires_native_arm64_macos")
            return report
        if timeout <= 0:
            raise ValueError("execution timeout must be positive")
        if game_root is not None and not windowed:
            raise ValueError("an actual game root requires --windowed")
        staged, moltenvk = Path(staged).resolve(), Path(moltenvk).resolve()
        host = staged / "work/nxnative-staged/dm/host"
        bindings = staged / "rewritten-bindings"
        stage = json.loads((staged / "stage-report.json").read_text(encoding="utf8"))
        if (stage.get("host_root") != "work/nxnative-staged/dm/host" or
                stage.get("patched_dir") != "rewritten-bindings" or stage.get("guest_sha256") != PINNED_SHA256 or
                stage.get("source_commit") != COMMIT or stage.get("guest_embedded") is not True):
            raise ValueError("unexpected staged guest identity or layout")
        patched = bindings / "main.patched.bin"
        if digest(patched) != stage.get("patched_sha256"):
            raise ValueError("staged guest differs from verified hook image")
        snapshot = json.loads((host.parent.parent / "snapshot.json").read_text(encoding="utf8"))
        if snapshot.get("commit") != COMMIT or snapshot.get("repository") != "seedds/nxnative":
            raise ValueError("unexpected staged reference identity")
        library = moltenvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
        if not library.is_file() or not (moltenvk / "include/vulkan/vulkan.h").is_file():
            raise ValueError("MoltenVK macOS universal library and Vulkan headers are required")
        header = host.parent / "games" / PROFILE / "PrototypeHookTable.h"
        if "prototype_hook_bindings" not in header.read_text(encoding="utf8"):
            raise ValueError("generated guest hook bindings are missing")
        if windowed:
            front = host / "front_mac.m"
            front.write_text(windowed_frontend(front.read_text(encoding="utf8")), encoding="utf8")
        makefile = host / "Makefile.prototype-smoke"
        makefile.write_text(smoke_makefile((host / "Makefile").read_text(encoding="utf8"), moltenvk), encoding="utf8")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("DM_", "PROTOTYPE_"))}
        env["GAME"] = PROFILE
        if windowed:
            env.update(DM_WINDOW="1", PROTOTYPE_MAC_SMOKE_EXIT="1")
        build = ["make", "-f", makefile.name, "-j", "3", "GAME=" + PROFILE, "dmhost"]
        with build_log.open("w", encoding="utf8") as output:
            compiled = subprocess.run(build, cwd=host, env=env, stdout=output,
                                      stderr=subprocess.STDOUT, timeout=1200)
        report["build_exit_code"] = compiled.returncode
        if compiled.returncode:
            raise ValueError("macOS host compilation failed")
        # Fresh writable state prevents saved files or accidental inputs from
        # changing what the default missing-resource run actually exercises.
        state = prefix.parent / (prefix.name + ".state")
        state.mkdir(exist_ok=False)
        if game_root is None:
            game = state / "game"
            (game / "Data").mkdir(parents=True)
        else:
            game = Path(game_root).resolve()
            if not (game / "default.xex").is_file():
                raise ValueError("actual game root must contain default.xex")
        saves, temporary = state / "saves", state / "temp"
        saves.mkdir(); temporary.mkdir()
        env.update(PROTOTYPE_GAME_ROOT=str(game), PROTOTYPE_SAVE_ROOT=str(saves),
                   PROTOTYPE_DOCUMENTS_ROOT=str(state), PROTOTYPE_FS_TRACE="1",
                   DM_ROMFS=str(game), DM_SAVE=str(saves), DM_TEMP=str(temporary), DM_AUDIO="1", DM_SVC="1",
                   DM_WATCH="45", DM_WATCH_STALL="45")
        report.update(guest_sha256=stage["guest_sha256"], patched_sha256=digest(patched),
                      hook_count=stage["hook_count"], host_sha256=digest(host / "dmhost"), compiled=True)
        with run_log.open("w", encoding="utf8") as output:
            executed = subprocess.run([str(host / "dmhost"), str(bindings)], cwd=state, env=env,
                                      stdout=output, stderr=subprocess.STDOUT, timeout=timeout)
        report["exit_code"] = executed.returncode
        log = run_log.read_text(encoding="utf8", errors="replace")
        checks, guest_result = runtime_evidence(log, executed.returncode)
        report.update(checks=checks, guest_main_result=guest_result, verified=all(checks.values()),
                      run_log_sha256=digest(run_log))
        report["status"] = "passed" if report["verified"] else "failed"
        if not report["verified"]:
            report["reason"] = "guest_startup_evidence_incomplete_or_abnormal_return"
        return report
    except subprocess.TimeoutExpired as error:
        report.update(reason="process_timeout", timed_out=True, timeout_seconds=error.timeout)
        return report
    except (OSError, ValueError, KeyError) as error:
        report["reason"] = str(error)
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--moltenvk-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="output filename prefix")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--windowed", action="store_true", help="open the real AppKit/CAMetalLayer frontend")
    parser.add_argument("--game-root", type=Path, help="optional actual resources for windowed diagnostic runs")
    args = parser.parse_args(argv)
    report = run_smoke(args.staged, args.moltenvk_root, args.out, args.timeout, args.windowed, args.game_root)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
