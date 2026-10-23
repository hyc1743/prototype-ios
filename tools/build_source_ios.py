#!/usr/bin/env python3
"""Compile the original Prototype translation, renderer and runtime for iOS."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import struct
import sys
import urllib.request

from build_ios import macho_sections, moltenvk, package, run, sha256
from ci_codegen import PINNED_ZIP_SHA256, XEX_SHA256, safe_path
from prepare_source_port import SDK_COMMIT, PROTOTYPE_COMMIT
from native_controls import stage_controls, SOURCE_COMMIT as CONTROLS_COMMIT
from app_icon import install_icon

ROOT = Path(__file__).resolve().parents[1]


def verify_generated(generated):
    generated = Path(generated).resolve()
    receipt = json.loads((generated / "codegen-manifest.json").read_text(encoding="utf8"))
    reviewed = json.loads((ROOT / "ci-inputs/codegen.json").read_text(encoding="utf8"))
    keys = {"schema", "project", "original_xex_sha256", "official_tool", "source_count", "uncompressed_bytes", "files"}
    if set(receipt) != keys or any(receipt[k] != reviewed.get(k) for k in keys):
        raise ValueError("Extracted codegen receipt differs from the authenticated build input")
    expected = {"codegen-manifest.json"}
    for record in receipt["files"]:
        relative = safe_path(record["path"])
        path = generated.joinpath(*relative.parts)
        if (not path.is_file() or any(p.is_symlink() for p in [path, *path.parents]) or
                path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]):
            raise ValueError("Extracted codegen file changed: " + record["path"])
        expected.add(record["path"])
    if {p.relative_to(generated).as_posix() for p in generated.rglob("*") if p.is_file()} != expected:
        raise ValueError("Extracted codegen tree contains unreviewed files")
    return receipt


def expected_functions(generated):
    text = (Path(generated) / "prototype_init.cpp").read_text(encoding="utf8")
    names = set(re.findall(r"\{\s*0x[0-9a-fA-F]{8},\s*(sub_[0-9a-fA-F]{8})\s*\}", text))
    if len(names) != 53211:
        raise ValueError("Generated Prototype guest function inventory differs from the pinned input")
    return names


def ios_device_build_version(image):
    """Check the executable's target platform, independently of its plist."""
    macho_sections(image)
    count = struct.unpack_from("<I", image, 16)[0]
    offset, versions = 32, []
    for _ in range(count):
        command, size = struct.unpack_from("<II", image, offset)
        if command == 0x32:  # LC_BUILD_VERSION, Apple's mach-o/loader.h
            if size < 24:
                raise ValueError("Truncated Mach-O build version")
            platform, minimum, sdk, tools = struct.unpack_from("<4I", image, offset + 8)
            if size != 24 + tools * 8:
                raise ValueError("Invalid Mach-O build tool inventory")
            versions.append((platform, minimum, sdk))
        offset += size
    if len(versions) != 1 or versions[0][0] != 2:  # PLATFORM_IOS; simulator is 7.
        raise ValueError("Executable must target iOS devices, not macOS or the simulator")
    _, minimum, sdk = versions[0]
    if minimum != 26 << 16 or sdk < minimum:
        raise ValueError("Executable deployment target differs from the declared iOS 26.0 minimum")
    return {"macho_platform": "iOS", "macho_platform_id": 2,
            "sdk_version": f"{sdk >> 16}.{(sdk >> 8) & 255}.{sdk & 255}"}


def inspect_native_app(app, expected, nm_output, load_commands):
    app = Path(app)
    info = plistlib.loads((app / "Info.plist").read_bytes())
    if (info.get("CFBundleExecutable") != "Prototype" or
            info.get("CFBundleIdentifier") != "io.github.hyc1743.prototype" or
            info.get("MinimumOSVersion") != "26.0"):
        raise ValueError("Native iOS bundle identity or deployment target differs")
    image = (app / "Prototype").read_bytes()
    sections, executable = macho_sections(image)
    platform = ios_device_build_version(image)
    if struct.unpack_from("<I", image, 12)[0] != 2:
        raise ValueError("Native iOS bundle binary is not an MH_EXECUTE image")
    if executable != ["__TEXT"] or sections.get(("__TEXT", "__text"), {}).get("size", 0) == 0:
        raise ValueError("Native iOS binary lacks a real ARM64 executable text section")
    actual = set(re.findall(r"sub_[0-9a-fA-F]{8}", nm_output))
    missing = expected - actual
    if missing:
        raise ValueError(f"Native executable is missing {len(missing)} translated guest functions")
    dependencies = [line.strip().split(" (", 1)[0] for line in load_commands.splitlines()[1:] if line.strip()]
    if any(not name.startswith(("/System/Library/", "/usr/lib/")) for name in dependencies):
        raise ValueError("Native executable references an unbundled non-system dynamic library")
    for name in ("PrototypeData.manifest.json", "prototype.toml"):
        source = ROOT / ("ios/resources/" + name if name.endswith("json") else "ios/config/" + name)
        if (app / "dm" / name).read_bytes() != source.read_bytes():
            raise ValueError("Bundled resource manifest/config differs from the reviewed source")
    return {"arm64": True, "minimum_ios": "26.0", **platform, "translated_functions_verified": len(expected),
            "system_dynamic_dependencies": dependencies, "executable_sha256": sha256(app / "Prototype")}


def build(args):
    if sys.platform != "darwin":
        raise ValueError("Native iOS compilation requires macOS with Xcode 26")
    generated = args.generated.resolve()
    receipt = verify_generated(generated)
    # Revalidate every extracted file against the authenticated inventory before
    # the reviewed preparation transform operates on a separate build copy.
    if receipt.get("original_xex_sha256") != XEX_SHA256:
        raise ValueError("Codegen receipt is bound to a different original XEX")
    expected = expected_functions(generated)
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Source build output must be a fresh directory")
    output.mkdir(parents=True)
    logs, stage, build_dir = output / "logs", output / "staged", output / "build"
    sdk = run(["xcrun", "--sdk", "iphoneos", "--show-sdk-version"])
    if int(sdk.split(".")[0]) < 26:
        raise ValueError("Xcode must include the iOS 26 SDK")
    mvk = moltenvk(ROOT / ".tools/moltenvk-1.4.2")
    mvk_license = mvk.parent / "LICENSE"
    if not mvk_license.is_file():
        raise ValueError("MoltenVK release is missing its license")
    env = {k: v for k, v in os.environ.items() if k not in {"PROTOTYPE_CODEGEN_KEY", "PROTOTYPE_GUEST_KEY"}}
    env["MOLTENVK_ROOT"] = str(mvk)
    run([sys.executable, ROOT / "tools/prepare_source_port.py", "--generated", generated,
         "--prototype-root", args.prototype_root.resolve(), "--out", stage, "--fetch-dependencies"],
        env=env, log=logs / "prepare.log")
    run([sys.executable, ROOT / "tools/resource_import_smoke.py", "--out", logs / "resource-import"],
        env=env, log=logs / "resource-import-driver.log")
    import_report = json.loads((logs / "resource-import.json").read_text(encoding="utf8"))
    if import_report.get("verified") is not True:
        raise ValueError("Native resource importer did not pass")
    controls_source = "https://raw.githubusercontent.com/seedds/nxnative/" + CONTROLS_COMMIT + "/dm/ios/Sources/DmGamepad.m"
    with urllib.request.urlopen(controls_source, timeout=60) as source:
        controls_raw = source.read(1024 * 1024)
    controls = output / "controls"
    controls_report = stage_controls(controls_raw, controls)
    license_url = "https://raw.githubusercontent.com/seedds/nxnative/" + CONTROLS_COMMIT + "/LICENSE"
    with urllib.request.urlopen(license_url, timeout=60) as source:
        controls_license = source.read(64 * 1024)
    controls_license_sha = hashlib.sha256(controls_license).hexdigest()
    if controls_license_sha != "5811600bb8fc4522d3310e8e0e637a1bb14b7c64412f8cfc6234872eb53129b9":
        raise ValueError("Pinned nxnative controller license differs")
    controls_report["license_sha256"] = controls_license_sha
    (logs / "controls.json").write_text(json.dumps(controls_report, indent=2) + "\n", encoding="utf8")
    extra = ";".join([str(ROOT / p) for p in ["ios/import/PrototypeImportViewController.m", "ios/import/PrototypeResourceInstaller.m"]]
                     + [str(controls / p) for p in ["DmGamepad.m", "PrototypeNativePad.m"]])
    command = ["cmake", "-S", stage, "-B", build_dir, "-G", "Ninja",
               "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_SYSTEM_NAME=iOS", "-DCMAKE_OSX_SYSROOT=iphoneos",
               "-DCMAKE_OSX_ARCHITECTURES=arm64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=26.0",
               "-DCMAKE_TRY_COMPILE_TARGET_TYPE=STATIC_LIBRARY", "-DCMAKE_C_COMPILER=clang",
               "-DCMAKE_CXX_COMPILER=clang++", "-DMOLTENVK_ROOT=" + str(mvk),
               "-DPROTOTYPE_APPLE_ENTRY_SOURCE=" + str(ROOT / "ios/source-port/PrototypeSDLMain.mm"),
               "-DPROTOTYPE_EXTRA_SOURCES=" + extra, "-DPROTOTYPE_EXTRA_INCLUDE_DIRS=" + str(ROOT / "ios/import") + ";" + str(controls),
               "-DPROTOTYPE_INFO_PLIST=" + str(ROOT / "ios/source-port/Info.plist.in"),
               "-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO"]
    if shutil.which("ccache"):
        for language in ("C", "CXX", "OBJC", "OBJCXX"):
            command.append("-DCMAKE_" + language + "_COMPILER_LAUNCHER=ccache")
    run(command, env=env, log=logs / "configure.log")
    run(["cmake", "--build", build_dir, "--target", "prototype", "--parallel", "3"], env=env, log=logs / "compile.log")
    applications = [p for p in stage.rglob("Prototype.app") if p.is_dir()]
    applications += [p for p in build_dir.rglob("Prototype.app") if p.is_dir() and p not in applications]
    if len(applications) != 1:
        raise ValueError("Expected exactly one compiled Prototype.app")
    app = applications[0]
    icon_report = install_icon(app, output / "app-icon")
    dm = app / "dm"
    dm.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "ios/resources/PrototypeData.manifest.json", dm / "PrototypeData.manifest.json")
    shutil.copyfile(ROOT / "ios/config/prototype.toml", dm / "prototype.toml")
    notices = dm / "notices"
    notices.mkdir()
    shutil.copyfile(stage / "Prototype-LICENSE", notices / "Prototype-GPL-3.0.txt")
    shutil.copyfile(mvk_license, notices / "MoltenVK-Apache-2.0.txt")
    (notices / "nxnative-controller-MIT.txt").write_bytes(controls_license)
    for source in [ROOT / "THIRD_PARTY.md", ROOT / "LICENSE"]:
        shutil.copyfile(source, notices / source.name)
    # Keep all dependency license/notice texts in the distributed app.
    for p in (stage / "sdk").rglob("*"):
        if p.is_file() and p.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
            destination = notices / "sdk" / p.relative_to(stage / "sdk")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, destination)
    nm = run(["xcrun", "nm", "-U", "-j", app / "Prototype"])
    dependencies = run(["xcrun", "otool", "-L", app / "Prototype"])
    native = inspect_native_app(app, expected, nm, dependencies)
    ipa = output / "Prototype-native-development.ipa"
    package(app, ipa)
    report = {"kind": "development-native-ipa", "signed": False,
              "contains_guest_executable": True, "contains_game_data": False,
              "runtime_verified": False, "playable_verified": False,
              "source_xex_sha256": XEX_SHA256, "codegen_zip_sha256": PINNED_ZIP_SHA256,
              "sdk_commit": SDK_COMMIT, "prototype_commit": PROTOTYPE_COMMIT,
              "moltenvk_license_included": True,
              "resource_import_smoke": import_report, "native_controls": controls_report, "requires_jit": False,
              "app_icon": icon_report,
              "ipa_sha256": sha256(ipa), "ipa_bytes": ipa.stat().st_size, **native}
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--prototype-root", type=Path, default=ROOT / "references/prototype-switch")
    parser.add_argument("--output", type=Path, default=ROOT / "out/source-ios")
    build(parser.parse_args())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
