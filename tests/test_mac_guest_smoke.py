import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from mac_guest_smoke import COMMIT, PINNED_SHA256, PROFILE, run_smoke, runtime_evidence, smoke_makefile, windowed_frontend

TRACE = ("[prototype] original libnx init completed\n"
         "[prototype] entering original main +0x58510\n"
         "[prototype] original main returned 1\n")
MAKEFILE = ("CFLAGS=-O1 -g\n"
            "SRC=PrototypeLibnx.c PrototypeFS.c PrototypeHooks.c PrototypeAudio.c PrototypeVulkan.c main.c loader.c\n"
            "\tclang $(CFLAGS) $(SRC) -framework UniformTypeIdentifiers\n")


class MacGuestSmokeTests(unittest.TestCase):
    def test_expected_missing_resource_main_result_is_recorded(self):
        checks, result = runtime_evidence(TRACE, 0)
        self.assertTrue(all(checks.values()))
        self.assertEqual(result, 1)
        checks, result = runtime_evidence(TRACE.replace("returned 1", "returned -7"), 0)
        self.assertTrue(all(checks.values()))
        self.assertEqual(result, -7)

    def test_crashes_and_partial_or_foreign_traces_cannot_pass(self):
        for log, code in ((TRACE, -11), (TRACE, 139), (TRACE, None), (TRACE, False),
                          (TRACE + "[dm] CRASH sig=11\n", 0),
                          (TRACE + "[dm] [RED] watchdog: no progress\n", 0),
                          (TRACE + "Abort trap: 6\n", 0),
                          (TRACE.replace("original libnx init completed", "init failed"), 0),
                          (TRACE.replace("0x58510", "0x99280"), 0),
                          (TRACE.replace("original main returned 1", "loader returned 1"), 0),
                          (TRACE.replace("[prototype]", "[biko3]"), 0)):
            with self.subTest(log=log, code=code):
                checks, _ = runtime_evidence(log, code)
                self.assertFalse(all(checks.values()))

    def test_trace_must_have_the_actual_startup_order(self):
        lines = TRACE.splitlines(keepends=True)
        checks, _ = runtime_evidence(lines[2] + lines[0] + lines[1], 0)
        self.assertFalse(checks["startup_trace_ordered"])
        checks, _ = runtime_evidence("quoted " + TRACE.replace("\n", "\nquoted "), 0)
        self.assertFalse(all(checks.values()))

    def test_makefile_preserves_real_sources_and_quotes_paths(self):
        result = smoke_makefile(MAKEFILE, PurePosixPath("/tmp/MoltenVK folder"))
        self.assertIn(MAKEFILE.splitlines()[1], result)
        self.assertIn("-arch arm64 -mcpu=apple-m2", result)
        self.assertIn("-I'/tmp/MoltenVK folder/include'", result)
        self.assertIn("'/tmp/MoltenVK folder/static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a'", result)
        self.assertIn("-lc++ -framework IOSurface", result)
        for changed in (MAKEFILE.replace("PrototypeLibnx.c ", ""),
                        MAKEFILE.replace("PrototypeHooks.c ", ""),
                        MAKEFILE.replace("CFLAGS=", "UNREVIEWED="),
                        MAKEFILE.replace(" -framework UniformTypeIdentifiers", "")):
            with self.assertRaises(ValueError):
                smoke_makefile(changed, Path("/tmp/MoltenVK"))

    def test_windowed_mode_connects_layer_and_unwinds_appkit(self):
        original = ('        g_layer = [CAMetalLayer layer];\n'
                    '    ((guest_arg *)p)->fn(); LOG("guest thread finished"); exit(0);\n')
        result = windowed_frontend(original)
        self.assertIn('prototype_vk_set_metal_layer((__bridge void *)g_layer);', result)
        self.assertIn('getenv("PROTOTYPE_MAC_SMOKE_EXIT")', result)
        self.assertIn('[NSApp stop:nil];', result)
        self.assertIn('[NSApp postEvent:wake atStart:YES];', result)
        self.assertEqual(windowed_frontend(result), result)
        with self.assertRaises(ValueError):
            windowed_frontend("unreviewed frontend")

    def fixture(self, root):
        host = root / "staged/work/nxnative-staged/dm/host"
        host.mkdir(parents=True)
        (host / "Makefile").write_text(MAKEFILE)
        (host / "dmhost").write_bytes(b"fixture executable; never actually executed")
        snapshot = {"repository": "seedds/nxnative", "commit": COMMIT}
        (host.parent.parent / "snapshot.json").write_text(json.dumps(snapshot))
        header = host.parent / "games" / PROFILE / "PrototypeHookTable.h"
        header.parent.mkdir(parents=True)
        header.write_text("prototype_hook_bindings")
        bindings = root / "staged/rewritten-bindings"
        bindings.mkdir()
        guest = b"fixture patched guest; never actually executed"
        (bindings / "main.patched.bin").write_bytes(guest)
        stage = {"host_root": "work/nxnative-staged/dm/host", "patched_dir": "rewritten-bindings",
                 "source_commit": COMMIT, "guest_sha256": PINNED_SHA256, "guest_embedded": True,
                 "patched_sha256": hashlib.sha256(guest).hexdigest(), "hook_count": 9}
        (root / "staged/stage-report.json").write_text(json.dumps(stage))
        mvk = root / "MoltenVK"
        library = mvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
        library.parent.mkdir(parents=True)
        library.write_bytes(b"fixture")
        header = mvk / "include/vulkan/vulkan.h"
        header.parent.mkdir(parents=True)
        header.write_text("fixture")
        return root / "staged", mvk, root / "report"

    def test_report_fails_on_crash_even_after_complete_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            staged, mvk, prefix = self.fixture(Path(directory))

            def fake_process(command, **kwargs):
                if command[0] == "make":
                    return subprocess.CompletedProcess(command, 0)
                kwargs["stdout"].write(TRACE + "[dm] CRASH sig=11\n")
                return subprocess.CompletedProcess(command, 139)

            with patch("mac_guest_smoke.sys.platform", "darwin"), patch("mac_guest_smoke.platform.machine", return_value="arm64"), \
                    patch("mac_guest_smoke.subprocess.run", side_effect=fake_process):
                report = run_smoke(staged, mvk, prefix)
            persisted = json.loads(Path(str(prefix) + ".json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertFalse(persisted["verified"])
            self.assertFalse(persisted["checks"]["host_returned_normally"])
            self.assertEqual(report["guest_main_result"], 1)
            self.assertFalse(report["gameplay_verified"])

    def test_success_report_has_bounded_scope_and_real_process_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            staged, mvk, prefix = self.fixture(Path(directory))

            def fake_process(command, **kwargs):
                if command[0] != "make":
                    kwargs["stdout"].write(TRACE)
                    self.assertTrue((Path(kwargs["env"]["DM_ROMFS"]) / "Data").is_dir())
                    self.assertFalse((Path(kwargs["env"]["DM_ROMFS"]) / "default.xex").exists())
                return subprocess.CompletedProcess(command, 0)

            with patch("mac_guest_smoke.sys.platform", "darwin"), patch("mac_guest_smoke.platform.machine", return_value="arm64"), \
                    patch("mac_guest_smoke.subprocess.run", side_effect=fake_process):
                report = run_smoke(staged, mvk, prefix)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(report["verified"])
            self.assertFalse(report["gameplay_verified"])
            self.assertFalse(report["ios_device_execution"])
            self.assertFalse(report["rendering_verified"])
            self.assertEqual(report["run_log_sha256"],
                             hashlib.sha256(Path(str(prefix) + ".run.log").read_bytes()).hexdigest())

    def test_timeout_is_persisted_as_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            staged, mvk, prefix = self.fixture(Path(directory))
            with patch("mac_guest_smoke.sys.platform", "darwin"), patch("mac_guest_smoke.platform.machine", return_value="arm64"), \
                    patch("mac_guest_smoke.subprocess.run", side_effect=subprocess.TimeoutExpired("make", 1200)):
                report = run_smoke(staged, mvk, prefix)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["verified"])
            self.assertTrue(report["timed_out"])
            self.assertEqual(json.loads(Path(str(prefix) + ".json").read_text())["reason"], "process_timeout")

    def test_other_platform_is_skipped_and_cannot_execute_or_claim_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "report"
            with patch("mac_guest_smoke.sys.platform", "win32"), patch("mac_guest_smoke.subprocess.run") as command:
                report = run_smoke(Path("missing-stage"), Path("missing-mvk"), prefix)
            command.assert_not_called()
            self.assertEqual(report["status"], "skipped")
            self.assertFalse(report["verified"])
            self.assertEqual(report["reason"], "requires_native_arm64_macos")
            self.assertFalse(json.loads(Path(str(prefix) + ".json").read_text())["verified"])


if __name__ == "__main__":
    unittest.main()
