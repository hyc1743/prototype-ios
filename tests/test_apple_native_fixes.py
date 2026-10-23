from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from apple_native_fixes import audio_source, renderer_source


class AppleNativeFixTests(unittest.TestCase):
    def test_index_expansion_keeps_winding_primitive_boundaries_and_large_indices(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        source = r'''
#include "PrototypePrimitiveIndices.h"
#include <cassert>
using namespace p2::native;
int main() {
  // A rectangle's fourth corner has no original vertex; the SDK synthesizes
  // it. Two triangles cover both halves with consistent winding.
  const unsigned expected[12]={0,1,2,2,1,3,4,5,6,6,5,7};
  const unsigned quads[12]={0,1,3,3,1,2,4,5,7,7,5,6};
  for(unsigned i=0;i<12;++i) {
    assert(ExpandedTriangleVertex(i,false)==expected[i]);
    assert(ExpandedTriangleVertex(i,true)==quads[i]);
  }
  assert(ExpandedTriangleIndexCount(0,1)==0);
  assert(ExpandedTriangleIndexCount(1,1)==6);
  assert(ExpandedTriangleIndexCount(2,3)==0);
  assert(ExpandedTriangleIndexCount(5,3)==6);
  assert(ExpandedTriangleIndexCount(6,3)==12);
  assert(ExpandedTriangleIndexCount(7,4)==6);
  assert(ExpandedTriangleIndexCount(8,4)==12);
  // Never truncate synthetic IDs to uint16, nor connect neighboring sprites.
  for(unsigned primitive:{0u,1u,16383u,16384u,65536u}) {
    for(unsigned corner=0;corner<6;++corner) {
      auto value=ExpandedTriangleVertex(primitive*6+corner,false);
      assert(value/4==primitive);
      assert(value%4==expected[corner]);
    }
  }
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'test.cpp'
            cpp.write_text(source.replace('#include <cassert>', '#include <cassert>\n#include <initializer_list>'))
            executable = directory / ('test.exe' if sys.platform == 'win32' else 'test')
            subprocess.run([compiler, '-std=c++17', '-I', str(ROOT / 'ios/source-port'),
                            str(cpp), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)

    def test_unknown_pinned_sources_are_rejected(self):
        for transform in (audio_source, renderer_source):
            with self.assertRaises(ValueError):
                transform('unreviewed input')


if __name__ == '__main__':
    unittest.main()
