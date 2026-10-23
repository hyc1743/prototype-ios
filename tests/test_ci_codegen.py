import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import ci_codegen


def fixture_entries():
    names = [f"synthetic_{i:03}.cpp" for i in range(157)]
    files = {name: f"int synthetic_{i}() {{ return {i}; }}\n".encode() for i, name in enumerate(names)}
    files["fixture.h"] = b"#pragma once\n"
    files["sources.cmake"] = ("set(GENERATED_SOURCES\n" + "".join("    ${CMAKE_CURRENT_LIST_DIR}/" + n + "\n" for n in names) + ")\n").encode()
    manifest = {"schema": 1, "project": "prototype", "original_xex_sha256": ci_codegen.XEX_SHA256,
                "official_tool": {"repository": "rexglue/rexglue-sdk", "tag": "v0.10.0", "archive_sha256": ci_codegen.TOOL_ZIP_SHA256},
                "source_count": 157, "uncompressed_bytes": sum(map(len, files.values())),
                "files": [{"path": n, "bytes": len(d), "sha256": ci_codegen.sha256(d)} for n, d in sorted(files.items())]}
    return {**files, ci_codegen.MANIFEST_NAME: json.dumps(manifest).encode()}


def unchecked_zip(entries, *, symlink=None):
    """Construct hostile fixtures without using production safe-path admission."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in entries.items():
            item = zipfile.ZipInfo(name)
            item.filename = name
            item.create_system = 3
            item.external_attr = ((stat.S_IFLNK if name == symlink else stat.S_IFREG) | 0o644) << 16
            archive.writestr(item, contents)
    return output.getvalue()


class CodegenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = fixture_entries()
        cls.archive = ci_codegen.deterministic_zip(cls.entries)
        cls.key = bytes(range(32))

    def test_deterministic_zip_and_inventory(self):
        self.assertEqual(self.archive, ci_codegen.deterministic_zip(dict(reversed(list(self.entries.items())))))
        manifest, files = ci_codegen.validate_archive(self.archive)
        self.assertEqual(manifest["source_count"], 157)
        self.assertEqual(len(files), 159)

    def test_authenticated_roundtrip_and_nonce(self):
        with patch.object(ci_codegen, "PINNED_ZIP_SHA256", ci_codegen.sha256(self.archive)):
            first = ci_codegen.seal(self.archive, self.key)
            second = ci_codegen.seal(self.archive, self.key)
            self.assertNotEqual(first, second)
            self.assertEqual(ci_codegen.open_container(first, self.key), self.archive)
            self.assertNotIn(self.key.hex(), json.dumps(ci_codegen.inspect_container(first)))

    def test_wrong_key_and_mutation_fail_authentication(self):
        with patch.object(ci_codegen, "PINNED_ZIP_SHA256", ci_codegen.sha256(self.archive)):
            packed = ci_codegen.seal(self.archive, self.key)
            for candidate, key in [(packed, bytes(32)), (packed[:-1] + bytes([packed[-1] ^ 1]), self.key)]:
                with self.subTest(wrong_key=key != self.key), self.assertRaisesRegex(ValueError, "authentication failed"):
                    ci_codegen.open_container(candidate, key)

    def test_unpinned_input_and_changed_header_fail(self):
        with self.assertRaisesRegex(ValueError, "pinned"):
            ci_codegen.seal(self.archive, self.key)
        with patch.object(ci_codegen, "PINNED_ZIP_SHA256", ci_codegen.sha256(self.archive)):
            packed = ci_codegen.seal(self.archive, self.key)
            for candidate in [b"bad", packed[:-1], b"X" + packed[1:], packed[:17] + bytes(32) + packed[49:]]:
                with self.subTest(size=len(candidate)), self.assertRaises(ValueError):
                    ci_codegen.inspect_container(candidate)

    def test_path_traversal_absolute_and_windows_names_fail_before_writing(self):
        for name in ["../escape.cpp", "/absolute.cpp", "C:/escape.cpp", "sub\\escape.cpp", "con.cpp", "file.cpp."]:
            packed = unchecked_zip({**self.entries, name: b"unsafe"})
            with tempfile.TemporaryDirectory() as tmp:
                destination = Path(tmp) / "generated"
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unsafe"):
                    ci_codegen.extract_archive(packed, destination)
                self.assertFalse(destination.exists())
                self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_symlink_case_collision_and_inventory_tamper_fail(self):
        with self.assertRaisesRegex(ValueError, "regular"):
            ci_codegen.validate_archive(unchecked_zip(self.entries, symlink="fixture.h"))
        with self.assertRaisesRegex(ValueError, "Repeated"):
            ci_codegen.validate_archive(unchecked_zip({**self.entries, "FIXTURE.H": b"collision"}))
        with self.assertRaisesRegex(ValueError, "inventory"):
            ci_codegen.validate_archive(unchecked_zip({**self.entries, "fixture.h": b"modified contents"}))

    def test_semantically_unsafe_source_manifest_fails_with_valid_file_hash(self):
        entries = dict(self.entries)
        changed = entries["sources.cmake"].replace(b"/synthetic_000.cpp", b"/../escape.cpp")
        manifest = json.loads(entries[ci_codegen.MANIFEST_NAME])
        for item in manifest["files"]:
            if item["path"] == "sources.cmake":
                item.update(bytes=len(changed), sha256=ci_codegen.sha256(changed))
        manifest["uncompressed_bytes"] += len(changed) - len(entries["sources.cmake"])
        entries["sources.cmake"] = changed
        entries[ci_codegen.MANIFEST_NAME] = json.dumps(manifest).encode()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            ci_codegen.validate_archive(unchecked_zip(entries))

    def test_new_directory_extract_and_existing_target_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "generated"
            ci_codegen.extract_archive(self.archive, target)
            self.assertEqual((target / "synthetic_042.cpp").read_bytes(), self.entries["synthetic_042.cpp"])
            self.assertTrue((target / ci_codegen.MANIFEST_NAME).is_file())
            with self.assertRaisesRegex(ValueError, "already exists"):
                ci_codegen.extract_archive(self.archive, target)
            self.assertFalse(any("unlock-" in p.name for p in Path(tmp).iterdir()))

    def test_cli_key_environment_is_exact_and_never_printed(self):
        with patch.dict("os.environ", {"PROTOTYPE_CODEGEN_KEY": self.key.hex()}):
            self.assertEqual(ci_codegen.read_key(), self.key)
        with patch.dict("os.environ", {"PROTOTYPE_CODEGEN_KEY": "invalid-secret"}):
            with self.assertRaises(ValueError) as error:
                ci_codegen.read_key()
            self.assertNotIn("invalid-secret", str(error.exception))


if __name__ == "__main__":
    unittest.main()
