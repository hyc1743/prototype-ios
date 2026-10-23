import io
import ast
from pathlib import Path
import re
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from prepare_source_port import apple_fiber_source, extract_archive, generated_sources, replace_once, source_sha


class SourcePreparationTests(unittest.TestCase):
    def test_fiber_macho_adaptation_keeps_assembly_statement_complete(self):
        source = ('#if REX_PLATFORM_SWITCH\n__asm__(\n'
                  '    ".global rex_fiber_swap\\n"\n'
                  '    ".type rex_fiber_swap, %function\\n"\n'
                  '    "rex_fiber_swap:\\n"\n'
                  '    "    ret\\n"\n'
                  '    ".global rex_fiber_entry\\n"\n'
                  '    "rex_fiber_entry:\\n"\n'
                  '    "    b   rex_fiber_trampoline\\n"\n'
                  '    ".size rex_fiber_entry, .-rex_fiber_entry\\n");\n#endif\n')
        result = apple_fiber_source(source)
        statement = re.search(r"__asm__\((.*?)\);", result, re.DOTALL)
        self.assertIsNotNone(statement)
        literals = re.findall(r'"(?:[^"\\]|\\.)*"', statement.group(1))
        assembly = "".join(ast.literal_eval(value) for value in literals)
        self.assertIn(".global _rex_fiber_swap\n_rex_fiber_swap:", assembly)
        self.assertIn("b   _rex_fiber_trampoline", assembly)
        self.assertNotIn(".type", assembly)
        self.assertNotIn(".size", assembly)

    def test_archive_cannot_write_outside_the_prepared_tree(self):
        for name in ("../escaped.h", "/absolute.h", "sdk/../../escaped.h"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w") as archive:
                    member = tarfile.TarInfo(name)
                    member.size = 4
                    archive.addfile(member, io.BytesIO(b"test"))
                with self.assertRaises(ValueError):
                    extract_archive(payload.getvalue(), Path(directory) / "prepared")
                self.assertFalse((Path(directory) / "escaped.h").exists())

    def test_dependency_population_preserves_fork_overlays(self):
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as archive:
            for name in ("config.h", "new.h"):
                member = tarfile.TarInfo(name)
                member.size = 6
                archive.addfile(member, io.BytesIO(b"vendor"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.h").write_bytes(b"reviewed fork overlay")
            extract_archive(payload.getvalue(), root, only_missing=True)
            self.assertEqual((root / "config.h").read_bytes(), b"reviewed fork overlay")
            self.assertEqual((root / "new.h").read_bytes(), b"vendor")

    def generated_fixture(self, root):
        root.mkdir()
        for name in ("prototype_init.cpp", "prototype_init.h", "prototype_register.cpp", "prototype_recomp.0.cpp"):
            (root / name).write_text("// source fixture\n")
        (root / "sources.cmake").write_text("set(GENERATED_SOURCES ${CMAKE_CURRENT_LIST_DIR}/prototype_recomp.0.cpp)\n")
        (root / "prototype_pch.h").write_text(
            "#if REX_PLATFORM_MAC && REX_ARCH_ARM64\n#define REX_PHYS_HOST_OFFSET(a) 0x1000u\n#endif\n")
        return root

    def test_missing_game_code_and_source_list_escape_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.generated_fixture(Path(directory) / "generated")
            self.assertEqual(generated_sources(root), ["prototype_recomp.0.cpp"])
            (root / "prototype_recomp.0.cpp").unlink()
            with self.assertRaisesRegex(ValueError, "missing unit"):
                generated_sources(root)
            outside = root.parent / "prototype_recomp.outside.cpp"
            outside.write_text("// outside\n")
            (root / "prototype_recomp.0.cpp").write_text("// fixture\n")
            (root / "sources.cmake").write_text("${CMAKE_CURRENT_LIST_DIR}/prototype_recomp.0.cpp\n"
                                               "${CMAKE_CURRENT_LIST_DIR}/../prototype_recomp.outside.cpp\n")
            with self.assertRaisesRegex(ValueError, "escapes"):
                generated_sources(root)

    def test_internal_vendor_links_are_materialized_but_external_links_are_rejected(self):
        for linked in ("original.h", "../outside.h"):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as directory:
                payload = io.BytesIO()
                with tarfile.open(fileobj=payload, mode="w") as archive:
                    original = tarfile.TarInfo("original.h")
                    original.size = 4
                    archive.addfile(original, io.BytesIO(b"real"))
                    member = tarfile.TarInfo("alias.h")
                    member.type = tarfile.SYMTYPE
                    member.linkname = linked
                    archive.addfile(member)
                root = Path(directory)
                if linked.startswith(".."):
                    with self.assertRaises(ValueError):
                        extract_archive(payload.getvalue(), root)
                else:
                    extract_archive(payload.getvalue(), root)
                    self.assertEqual((root / "alias.h").read_bytes(), b"real")
                    self.assertFalse((root / "alias.h").is_symlink())

    def test_unadjusted_switch_translation_and_original_binary_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.generated_fixture(Path(directory) / "generated")
            (root / "default.xex").write_bytes(b"original resource fixture")
            with self.assertRaisesRegex(ValueError, "non-source"):
                generated_sources(root)
            (root / "default.xex").unlink()
            (root / "prototype_pch.h").write_text("#define REX_PHYS_HOST_OFFSET(a) 0u\n")
            with self.assertRaisesRegex(ValueError, "Apple E0"):
                generated_sources(root)

    def test_reviewed_patch_identity_is_independent_of_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lf").write_bytes(b"first\nsecond\n")
            (root / "crlf").write_bytes(b"first\r\nsecond\r\n")
            self.assertEqual(source_sha(root / "lf"), source_sha(root / "crlf"))
            (root / "crlf").write_bytes(b"first\r\nchanged\r\n")
            self.assertNotEqual(source_sha(root / "lf"), source_sha(root / "crlf"))
        with self.assertRaises(ValueError):
            replace_once("two anchors two anchors", "anchors", "replacement")
        with self.assertRaises(ValueError):
            replace_once("missing", "anchors", "replacement")


if __name__ == "__main__":
    unittest.main()
