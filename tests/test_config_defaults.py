from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfigDefaultsTests(unittest.TestCase):
    def test_upgrade_changes_old_factory_values_and_preserves_comments_and_custom_settings(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        source = r'''
#include "PrototypeConfigDefaults.h"
#include <cassert>
int main() {
  const auto defaults = {PrototypeFactoryValue{"fast", "true", "false"},
                         PrototypeFactoryValue{"renderer", "\"native\"", "\"xenos\""}};
  auto migrated = PrototypeMigrateFactoryValues(
    "# fast = true\r\n fast\t= true  # keep\r\nrenderer = \"native\"\r\npath = \"untouched\"", defaults);
  assert(migrated == "# fast = true\r\n fast\t= false  # keep\r\nrenderer = \"xenos\"\r\npath = \"untouched\"");
  assert(PrototypeMigrateFactoryValues(migrated, defaults) == migrated);
  assert(PrototypeMigrateFactoryValues("fast_extra = true\nfast = false\nrenderer = \"custom\"", defaults) ==
    "fast_extra = true\nfast = false\nrenderer = \"custom\"");
  assert(PrototypeMigrateFactoryValues("# no active values", defaults) ==
    "# no active values\nfast = false\nrenderer = \"xenos\"\n");
  assert(PrototypeMigrateFactoryValues("fast=true\nrenderer=\"native\" # note", defaults) ==
    "fast=false\nrenderer=\"xenos\" # note");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'migration.cpp'
            cpp.write_text(source)
            binary = directory / ('migration.exe' if sys.platform == 'win32' else 'migration')
            subprocess.run([compiler, '-std=c++17', '-I', str(ROOT / 'ios/source-port'),
                            str(cpp), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()
