from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeFilesystemTests(unittest.TestCase):
    def test_guest_devoptab_uses_real_file_operations_and_original_stat_layout(self):
        if sys.platform == "win32":
            self.skipTest("POSIX native filesystem smoke requires macOS or Linux")
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("native C compiler unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            docs = work / "Documents"
            (docs / "game").mkdir(parents=True)
            (docs / "saves").mkdir()
            executable = work / "filesystem-test"
            subprocess.run([compiler, "-std=c11", "-O1", "-g", "-fsanitize=address,undefined",
                            "-fno-omit-frame-pointer", "-Werror=implicit-function-declaration",
                            "-I", str(ROOT / "ios/runtime"), str(ROOT / "tests/native/fs.c"),
                            "-o", str(executable)], check=True, capture_output=True, text=True, timeout=120)
            result = subprocess.run([str(executable), str(docs)], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("FS_ALL_PASSED", result.stdout)
            for check in ("original_devoptab_layout_and_offset", "original_game_and_config_paths",
                          "original_104_byte_stat_abi", "native_save_flags_seek_stat_sync_and_rename",
                          "native_directory_abi"):
                self.assertIn("FS_PASS " + check, result.stdout)
            self.assertFalse((docs / "saves/prototype").exists())


if __name__ == "__main__":
    unittest.main()
