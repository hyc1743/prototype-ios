import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_controls as controls

FIXTURE = ("// Nintendo button positions; A (right) B (bottom) X (top) Y (left)\n"
           "// bottom = B, right = A, left = Y, top = X (same positions as on a Switch controller)\n"
           + controls.INCLUDES + "\n" + controls.DIAMOND + "\n" + controls.PHYSICAL + "\n").encode()


class NativeControlsTests(unittest.TestCase):
    def stage_fixture(self, output, raw=FIXTURE):
        with patch.object(controls, "SOURCE_SHA256", hashlib.sha256(raw).hexdigest()):
            return controls.stage_controls(raw, output)

    def test_unreviewed_source_is_rejected_before_creating_any_output(self):
        self.assertEqual(controls.SOURCE_COMMIT, "677345b0933f5f8754aeec3125d14df32683ca97")
        self.assertEqual(controls.SOURCE_SHA256, "676766a42a2476310b86ee34dfc90aad7e0cada0f04a8a527958abe3b6aef342")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "controls"
            with self.assertRaisesRegex(ValueError, "reviewed nxnative SHA-256"):
                controls.stage_controls(FIXTURE, output)
            self.assertFalse(output.exists())

    def test_staged_touch_diamond_and_physical_mapping_agree_on_xbox_positions(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "controls"
            report = self.stage_fixture(output)
            text = (output / "DmGamepad.m").read_text(encoding="utf8")
            positions = {}
            point = type("Point", (), {"x": 0, "y": 0})()
            for button, x, y in re.findall(r"(?:case B_([ABX]):|default:) return CGPointMake\(([^,]+), ([^)]+)\)", text):
                context = {"c": point, "g": 1}
                positions[button or "Y"] = (eval(x, {"__builtins__": {}}, context), eval(y, {"__builtins__": {}}, context))
            self.assertEqual(positions, {"A": (0, 1), "B": (1, 0), "X": (-1, 0), "Y": (0, -1)})
            self.assertEqual(dict(re.findall(r"K\(g.button([ABXY]), B_([ABXY])\)", text)),
                             {"A": "A", "B": "B", "X": "X", "Y": "Y"})
            self.assertEqual(text.count('#include "PrototypeNativePad.h"'), 1)
            for private in ('"dm.h"', '"dm_input.h"', '"front.h"', '"game_profile.h"'):
                self.assertNotIn(private, text)
            self.assertFalse(report["runtime_verified"])
            for name, expected in report["files"].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), expected)
            with self.assertRaises(FileExistsError):
                self.stage_fixture(output)

    def test_changed_or_ambiguous_source_anchor_fails_even_if_hash_is_reviewed(self):
        for fragment in (controls.INCLUDES, controls.DIAMOND, controls.PHYSICAL):
            for raw in (FIXTURE.replace(fragment.encode(), b"missing"), FIXTURE + fragment.encode()):
                with self.subTest(fragment=fragment[:24]), tempfile.TemporaryDirectory() as temporary:
                    output = Path(temporary) / "controls"
                    with self.assertRaisesRegex(ValueError, "anchor changed"):
                        self.stage_fixture(output, raw)
                    self.assertFalse(output.exists())

    def test_actual_pinned_gamepad_when_reference_snapshot_is_available(self):
        source = ROOT / "references/nxnative/dm/ios/Sources/DmGamepad.m"
        if not source.is_file():
            self.skipTest("external pinned nxnative fixture has not been fetched")
        before = source.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            report = controls.stage_controls(source, Path(temporary) / "controls")
            self.assertEqual(report["source_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(source.read_bytes(), before)

    def test_native_axis_and_button_conversion_used_by_sdl_update_callback(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("native C compiler unavailable")
        harness = r'''
#include <assert.h>
#include <limits.h>
#include <string.h>
#include "PrototypeNativePad.h"
int main(void) {
    int32_t sticks[4] = {0}; int16_t axes[6]; uint8_t buttons[15];
    static const int button_index[16] = {0,1,2,3,7,8,9,10,-1,-1,6,4,13,11,14,12};
    for (unsigned bit = 0; bit < 16; ++bit) {
        PrototypePadMapState(UINT64_C(1) << bit, sticks, axes, buttons);
        for (int button = 0; button < 15; ++button) assert(buttons[button] == (button == button_index[bit]));
        assert(axes[4] == (bit == 8 ? 32767 : -32768));
        assert(axes[5] == (bit == 9 ? 32767 : -32768));
        for (int axis = 0; axis < 4; ++axis) assert(axes[axis] == 0);
    }
    int32_t extreme[4] = {INT32_MIN, INT32_MAX, INT32_MAX, INT32_MIN};
    PrototypePadMapState(DM_BTN_SHAKE, extreme, axes, buttons);
    assert(axes[0] == -32767 && axes[1] == -32767 && axes[2] == 32767 && axes[3] == 32767);
    assert(axes[4] == -32768 && axes[5] == -32768);
    for (int button = 0; button < 15; ++button) assert(buttons[button] == 0);
    PrototypePadMapState(UINT64_MAX, sticks, axes, buttons);
    for (int button = 0; button < 15; ++button) assert(buttons[button] == (button != 5));
    assert(axes[4] == 32767 && axes[5] == 32767);
    puts("NATIVE_PAD_MAPPING_ALL_PASSED");
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "pad.c"
            source.write_text(harness, encoding="utf8")
            executable = root / ("pad.exe" if sys.platform == "win32" else "pad")
            result = subprocess.run([compiler, "-std=c11", "-Werror", "-Wall", "-Wextra",
                "-I", str(ROOT / "ios/source-port"), str(source), "-o", str(executable)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("NATIVE_PAD_MAPPING_ALL_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()
