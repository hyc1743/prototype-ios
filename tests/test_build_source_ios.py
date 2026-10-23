import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from build_source_ios import inspect_native_app, verify_generated
from test_ios_build import executable, name


class NativeSourceBuildTests(unittest.TestCase):
    def app(self, root):
        app = root / "Prototype.app"
        app.mkdir()
        (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Prototype",
            "CFBundleIdentifier": "io.github.hyc1743.prototype", "MinimumOSVersion": "26.0"}))
        data = executable()
        data[104:120] = name("__text")
        (app / "Prototype").write_bytes(data)
        (app / "dm").mkdir()
        for path in (ROOT / "ios/resources/PrototypeData.manifest.json", ROOT / "ios/config/prototype.toml"):
            (app / "dm" / path.name).write_bytes(path.read_bytes())
        return app

    def test_compiled_app_requires_every_translated_guest_function_and_static_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            app = self.app(Path(directory))
            dependencies = "Prototype:\n\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
            names = {"sub_82230000", "sub_822300A0"}
            result = inspect_native_app(app, names, "__imp__sub_82230000\n_sub_822300A0\n", dependencies)
            self.assertEqual(result["translated_functions_verified"], 2)
            with self.assertRaisesRegex(ValueError, "missing 1 translated"):
                inspect_native_app(app, names, "_sub_82230000\n", dependencies)
            with self.assertRaisesRegex(ValueError, "unbundled"):
                inspect_native_app(app, names, "_sub_82230000\n_sub_822300A0", dependencies + "\t@rpath/librexruntime.dylib (compatibility version 1.0.0)\n")
            data = bytearray((app / "Prototype").read_bytes())
            struct.pack_into("<I", data, 12, 6)
            (app / "Prototype").write_bytes(data)
            with self.assertRaises(ValueError):
                inspect_native_app(app, names, "", dependencies)

    def test_authenticated_extraction_is_rechecked_before_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generated = root / "generated"
            generated.mkdir()
            data = b"int translated_guest() { return 7; }\n"
            (generated / "guest.cpp").write_bytes(data)
            receipt = {"schema": 1, "project": "prototype", "original_xex_sha256": "0" * 64,
                "official_tool": {}, "source_count": 1, "uncompressed_bytes": len(data),
                "files": [{"path": "guest.cpp", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}]}
            (generated / "codegen-manifest.json").write_text(json.dumps(receipt))
            (root / "ci-inputs").mkdir()
            (root / "ci-inputs/codegen.json").write_text(json.dumps(receipt))
            with patch("build_source_ios.ROOT", root):
                self.assertEqual(verify_generated(generated), receipt)
                (generated / "guest.cpp").write_bytes(data.replace(b"7", b"8"))
                with self.assertRaisesRegex(ValueError, "file changed"):
                    verify_generated(generated)
                (generated / "guest.cpp").write_bytes(data)
                (generated / "unreviewed.cpp").write_bytes(data)
                with self.assertRaisesRegex(ValueError, "unreviewed files"):
                    verify_generated(generated)


if __name__ == "__main__":
    unittest.main()
