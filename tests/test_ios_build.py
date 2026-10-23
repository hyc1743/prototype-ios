import json
import hashlib
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_ios import NX_COMMIT, PINNED_SHA256, bundle_notices, guest_startup_result, inspect_app, macho_sections, package


def name(value):
    return value.encode().ljust(16, b"\0")


def executable():
    data = bytearray(0x500)
    struct.pack_into("<8I", data, 0, 0xFEEDFACF, 0x0100000C, 0, 2, 2, 232 + 152, 0, 0)
    struct.pack_into("<II16s4Q4I", data, 32, 0x19, 232, name("__TEXT"), 0, 0x4C0, 0, 0x4C0, 5, 5, 2, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 72, name("__dmmainx0"), name("__TEXT"), 0, 0x80, 0x400, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 152, name("__dmmainx1"), name("__TEXT"), 0, 0x40, 0x480, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<II16s4Q4I", data, 32 + 232, 0x19, 152, name("__DMmainW"), 0, 0x40, 0x4C0, 0x40, 3, 3, 1, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 232 + 72, name("__img"), name("__DMmainW"), 0, 0x40, 0x4C0, 0, 0, 0, 0, 0, 0, 0)
    data[0x410:0x414] = b"NRO0"
    return data


class IosBuildTests(unittest.TestCase):
    def test_guest_startup_requires_every_trace_and_normal_guest_return(self):
        report = {"status": "passed", "verified": True, "exit_code": 0, "guest_main_result": 1,
                  "guest_sha256": PINNED_SHA256,
                  "checks": {key: True for key in ("libnx_initialized", "original_main_entered",
                      "original_main_returned", "host_returned_normally", "startup_trace_ordered", "no_crash_reported")}}
        self.assertTrue(guest_startup_result(report))
        for changed in (dict(report, guest_main_result=None), dict(report, guest_main_result=True), dict(report, exit_code=-11),
                        dict(report, checks={}), dict(report, verified=False), dict(report, status="failed")):
            with self.assertRaises(ValueError):
                guest_startup_result(changed)
        self.assertFalse(guest_startup_result({"status": "skipped", "verified": False,
                                              "reason": "requires_native_arm64_macos"}))
    def test_dependency_notices_are_copied_into_the_actual_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mvk = root / "MoltenVK/MoltenVK"
            mvk.mkdir(parents=True)
            (mvk.parent / "LICENSE").write_text("Apache license fixture")
            source = root / "source"
            license = source / "dm/third_party/libvpx/LICENSE"
            license.parent.mkdir(parents=True)
            license.write_text("libvpx license fixture")
            app = root / "Prototype.app"
            report = bundle_notices(app, source, mvk)
            self.assertEqual(report["nxnative_third_party_notice_files"], 1)
            self.assertEqual((app / "dm/notices/MoltenVK-Apache-2.0.txt").read_text(), "Apache license fixture")
            self.assertEqual((app / "dm/notices/nxnative-third-party/libvpx/LICENSE").read_text(), "libvpx license fixture")

    def test_guest_embedded_sections_and_preserved_header_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Prototype.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.github.hyc1743.prototype", "MinimumOSVersion": "26.0", "CFBundleExecutable": "Prototype", "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "1"}))
            (app / "dm").mkdir()
            original_manifest = Path(__file__).resolve().parents[1] / "ios/resources/PrototypeData.manifest.json"
            (app / "dm/PrototypeData.manifest.json").write_bytes(original_manifest.read_bytes())
            original_config = Path(__file__).resolve().parents[1] / "ios/config/prototype.toml"
            (app / "dm/prototype.toml").write_bytes(original_config.read_bytes())
            binary = executable()
            (app / "Prototype").write_bytes(binary)
            sections, _ = macho_sections(binary)
            section_records = {",".join(key): {"size": value["size"], "sha256": hashlib.sha256(binary[value["offset"]:value["offset"]+value["size"]]).hexdigest()} for key, value in sections.items()}
            stage = {"guest_sha256": PINNED_SHA256, "source_commit": NX_COMMIT, "guest_embedded": True, "embedded_sections": section_records}
            report = inspect_app(app, stage)
            self.assertTrue(report["arm64"])
            self.assertTrue(report["embedded_guest_sections_verified"])
            self.assertEqual(len(report["guest_sections"]), 3)
            mutated = bytearray(binary)
            mutated[0x4c2] ^= 0x80
            (app / "Prototype").write_bytes(mutated)
            with self.assertRaisesRegex(ValueError, "differs from staged"):
                inspect_app(app, stage)
            (app / "Prototype").write_bytes(binary)
            with self.assertRaisesRegex(ValueError, "differs from staged"):
                inspect_app(app, dict(stage, embedded_sections={}))
            for changed in (dict(stage, guest_embedded=False), dict(stage, guest_sha256="0" * 64)):
                with self.assertRaises(ValueError):
                    inspect_app(app, changed)
            (app / "dm/PrototypeData.manifest.json").write_bytes(b"wrong manifest")
            with self.assertRaisesRegex(ValueError, "resource manifest"):
                inspect_app(app, stage)
            (app / "dm/PrototypeData.manifest.json").write_bytes(original_manifest.read_bytes())
            binary[0x410:0x414] = b"FAKE"
            (app / "Prototype").write_bytes(binary)
            with self.assertRaisesRegex(ValueError, "preserved NRO header"):
                inspect_app(app, stage)

    def test_malformed_or_wrong_architecture_macho_is_rejected(self):
        for data in (b"shell", executable()[:300]):
            with self.assertRaises(ValueError):
                macho_sections(data)
        data = executable()
        struct.pack_into("<I", data, 4, 7)
        with self.assertRaisesRegex(ValueError, "arm64"):
            macho_sections(data)

    def test_ipa_has_compiled_app_and_excludes_signing_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "Prototype.app"
            app.mkdir()
            (app / "Prototype").write_bytes(executable())
            (app / "embedded.mobileprovision").write_bytes(b"profile")
            target = root / "build.ipa"
            package(app, target)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), ["Payload/Prototype.app/Prototype"])
            (app / "game.nro").write_bytes(b"input")
            with self.assertRaises(ValueError):
                package(app, target)


if __name__ == "__main__":
    unittest.main()
