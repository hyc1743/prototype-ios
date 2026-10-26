"""Compare the pinned upstream register optimization against its previous behavior."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RegisterLookupTests(unittest.TestCase):
    def test_all_registers_preserve_hash_generations_and_dirty_masks(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        before = (ROOT / 'tests/native/register_written_old.inc').read_text()
        after = (ROOT / 'tests/native/register_written_lookup.inc').read_text()
        fixture = r'''
#include <array>
#include <cassert>
#include <cstdint>
struct State {
  uint64_t state_generation_=0, state_hash_=0, index_generation_=0;
  uint64_t vs_float_generation_=0, ps_float_generation_=0, fetch_generation_=0;
  uint64_t bool_loop_generation_=0;
  uint32_t texture_fetch_dirty_=0, vertex_fetch_dirty_=0;
  std::array<uint32_t,0x4000> state_change_counts_{};
  static uint64_t Mix(uint32_t i,uint32_t v) {return (uint64_t(i)<<32)^uint64_t(v)*0x9e3779b9;}
  bool operator==(const State&) const = default;
};
struct Before : State { BEFORE };
struct After : State { AFTER };
int main() {
  Before before; After after;
  // Includes every valid register, all range boundaries, and the fallback above 0x5000.
  for (uint32_t pass=0;pass<3;++pass) {
    for (uint32_t i=0;i<0x5100;++i) {
      uint32_t old_value=i*19+pass, new_value=~old_value;
      before.RegisterWritten(i,old_value,new_value);
      after.RegisterWritten(i,old_value,new_value);
      assert(static_cast<State&>(before)==static_cast<State&>(after));
    }
  }
}
'''.replace('BEFORE', before).replace('AFTER', after)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            cpp = directory / 'registers.cpp'
            cpp.write_text(fixture)
            binary = directory / 'registers.exe'
            subprocess.run([compiler, '-std=c++20', '-O2', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_fixture_matches_pinned_source_when_available(self):
        source = ROOT / '../../references/prototype-switch/prototype/src/native/native_renderer.h'
        if not source.is_file():
            self.skipTest('upstream source not present in test-only checkout')
        fixture = (ROOT / 'tests/native/register_written_lookup.inc').read_text()
        upstream = source.read_text(encoding='utf8')
        prefix, method = fixture[fixture.index('  enum RegisterKind'):].split('  void RegisterWritten', 1)
        self.assertIn(prefix, upstream)
        self.assertIn('  void RegisterWritten' + method, upstream)


if __name__ == '__main__':
    unittest.main()
