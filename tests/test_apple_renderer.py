import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from apple_renderer import primitive_renderer_source


class ApplePrimitiveTests(unittest.TestCase):
    def test_overlay_rejects_changed_renderer(self):
        with self.assertRaises(ValueError):
            primitive_renderer_source('unrelated renderer')

    def test_primitive_stages_compile_without_geometry_capability(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        validator = shutil.which('glslangValidator') or shutil.which('glslang')
        if not compiler or not validator:
            self.skipTest('native C++ and glslang needed for real stage compilation')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            driver = root / 'stages.cpp'
            driver.write_text('''#include "PrototypePrimitiveTessellation.h"
#include <fstream>
int main() {
  unsigned variant=0;
  for(bool quad:{false,true}) for(unsigned n:{0u,1u,16u})
    for(unsigned clip:{0u,2u}) for(unsigned cull:{0u,1u}) {
      auto s=p2::native::PrimitiveTessellationGlsl(quad,n,clip,cull,true);
      auto name=std::to_string(variant++);
      std::ofstream(name+".tesc")<<s.control;
      std::ofstream(name+".tese")<<s.evaluation;
    }
}
''')
            binary = root / ('stages.exe' if os.name == 'nt' else 'stages')
            subprocess.run([compiler, '-std=c++17', '-I', str(ROOT / 'ios/source-port'),
                            str(driver), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], cwd=root, check=True, capture_output=True)
            for shader in sorted(root.glob('*.tes?')):
                result = subprocess.run([validator, '-V', str(shader), '-o', str(root / 'out.spv')],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, shader.name + '\n' + result.stdout + result.stderr)
            # Also link each control/evaluation interface together.
            for control in sorted(root.glob('*.tesc')):
                result = subprocess.run([validator, '-V', '-l', str(control),
                                        str(control.with_suffix('.tese')), '-o', str(root / 'linked.spv')],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
