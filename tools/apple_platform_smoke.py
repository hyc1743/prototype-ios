#!/usr/bin/env python3
"""Execute the exact iOS fiber source on an ARM64 macOS host, without game data."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

from build_ios import run, sha256
from build_source_ios import verify_generated

ROOT = Path(__file__).resolve().parents[1]
CHECKS = {"live_integer_and_fp_values", "stack_alignment", "thread_local_isolation", "current_fiber_and_lifetime"}


def smoke(args):
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Platform smoke output must be fresh")
    evidence = out / "evidence"
    evidence.mkdir(parents=True)
    report = {"schema": 1, "verified": False, "ios_device_execution": False,
              "game_executed": False, "scope": "Actual iOS cooperative fiber source executed on ARM64 macOS"}
    try:
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            raise ValueError("Platform smoke requires native ARM64 macOS")
        generated = args.generated.resolve()
        verify_generated(generated)
        stage = out / "staged"
        env = {k: v for k, v in os.environ.items()
               if not any(word in k.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "_KEY"))}
        run([sys.executable, ROOT / "tools/prepare_source_port.py", "--generated", generated,
             "--prototype-root", args.prototype_root.resolve(), "--out", stage],
            env=env, log=evidence / "prepare.log")
        source = stage / "sdk/src/core/fiber_apple.cpp"
        executable = out / "fiber-smoke"
        run(["xcrun", "--sdk", "macosx", "clang++", "-std=c++23", "-O2", "-arch", "arm64",
             "-DREX_PLATFORM_IOS=1", "-I", stage / "sdk/include", source,
             ROOT / "tests/native/apple_fiber.cpp", "-pthread", "-o", executable],
            env=env, log=evidence / "fiber-build.log")
        with (evidence / "fiber-run.log").open("w", encoding="utf8") as log:
            result = subprocess.run([str(executable)], env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=30)
        text = (evidence / "fiber-run.log").read_text(encoding="utf8", errors="replace")
        passed = set(re.findall(r"^APPLE_FIBER_PASS (\S+)$", text, flags=re.MULTILINE))
        report.update(architecture=platform.machine(), source_sha256=sha256(source),
                      exit_code=result.returncode, checks={name: name in passed for name in sorted(CHECKS)},
                      verified=result.returncode == 0 and CHECKS.issubset(passed) and "APPLE_FIBER_ALL_PASSED" in text)
        return report
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        raise
    finally:
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--prototype-root", type=Path, default=ROOT / "references/prototype-switch")
    parser.add_argument("--out", type=Path, default=ROOT / "out/apple-platform")
    report = smoke(parser.parse_args())
    print(json.dumps(report, indent=2))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
