from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from apple_native_fixes import (audio_source, renderer_source, translator_source,
                                alias_pass_source, capture_pass_source, attachment_pass_source)


class AppleNativeFixTests(unittest.TestCase):
    def test_attachment_removal_cannot_reuse_an_incompatible_dynamic_scope(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        fixture = r'''
#include <iostream>
struct Targets {int color[4];int depth;};
struct Renderer {
  bool in_pass=false;Targets pass{};int invalid=0;
  void BeginPass(const Targets& targets) {
    if(in_pass) {
      bool compatible = true;
      for (unsigned i=0;i<4;++i) {
      if (targets.color[i] && targets.color[i] != pass.color[i]) compatible = false;
      }
    if (targets.depth && targets.depth != pass.depth) compatible = false;
      if(compatible) return;
    }
    pass=targets;in_pass=true;
  }
  void Draw(const Targets& pipeline) {
    BeginPass(pipeline);
    // Without dynamicRenderingUnusedAttachments, pipeline and scope formats
    // must agree even when the shader does not use a leftover attachment.
    bool valid=pass.depth==pipeline.depth;
    for(unsigned i=0;i<4;++i) valid &= pass.color[i]==pipeline.color[i];
    invalid += !valid;
  }
};
int main() {
  Renderer r;
  r.Draw({{1,2,0,0},3});
  r.Draw({{1,2,0,0},3}); // exact reuse stays valid
  r.Draw({{1,0,0,0},3}); // remove MRT1
  r.Draw({{1,0,0,0},0}); // remove depth for a post pass
  r.Draw({{0,0,0,0},3}); // depth-only pass must not retain colors
  std::cout<<r.invalid;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for label, source, expected in [('before', fixture, '3'),
                                             ('after', attachment_pass_source(fixture), '0')]:
                cpp = directory / (label + '.cpp')
                cpp.write_text(source)
                executable = directory / (label + ('.exe' if sys.platform == 'win32' else ''))
                subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(executable)],
                               check=True, capture_output=True)
                result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout, expected)

    def test_capture_readbacks_preserve_render_pass_and_bound_state(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        fixture = r'''
#include <iostream>
struct Shader {unsigned ucode_data_hash() {return 0;}};
struct Setup {Shader* active_vs=nullptr;Shader* active_ps=nullptr;} setup;
bool in_pass=false,bound=false,water_dump_enabled=false;
bool capture_all=true,post_trace_seconds=false,water_seq_frame=false;
bool water_raw_pending=false,profile_draws=false,profile_frame=false;
int post_trace_count=0,frame=1,water_seq_count=0,targets=0;
void RequestWaterDump() {}
bool WaterSimTracing() {return true;}
void PostDump(unsigned) {in_pass=false;bound=false;}
void TraceSimDraw(unsigned) {PostDump(0);}
void WaterSequenceDump(unsigned) {PostDump(0);}
void DumpWaterSimInputs() {PostDump(0);}
void skip(int) {}
bool BeginPass(int) {in_pass=true;return true;}
void Draw() {
  unsigned draw_count=6;
  // Record.
  if (!BeginPass(targets)) return skip(14);
  struct {unsigned count;} d{draw_count};
  bound=true;
  if (water_dump_enabled && setup.active_vs &&
      setup.active_vs->ucode_data_hash() == 0) RequestWaterDump();
  if (WaterSimTracing()) {
    if (((post_trace_seconds && post_trace_count == 2) || capture_all) && setup.active_ps)
      PostDump(setup.active_ps->ucode_data_hash());
    TraceSimDraw(d.count);
  }
  if (water_seq_frame && frame == water_seq_frame && water_seq_count < 24)
    WaterSequenceDump(d.count);
  if (water_raw_pending) DumpWaterSimInputs();
  if (profile_draws && profile_frame) {}
  std::cout<<in_pass<<bound;
}
int main() {Draw();}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for label, source, expected in [('before', fixture, '00'),
                                             ('after', capture_pass_source(fixture), '11')]:
                cpp = directory / (label + '.cpp')
                cpp.write_text(source)
                executable = directory / (label + ('.exe' if sys.platform == 'win32' else ''))
                subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(executable)],
                               check=True, capture_output=True)
                result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
                self.assertEqual(result.stdout, expected)

    def test_alias_clear_cannot_leave_the_draw_outside_a_render_pass(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        fixture = r'''
#include <iostream>
namespace xenos { enum class PrimitiveType {kRectangleList}; }
namespace reg {
struct VGT_DRAW_INITIATOR {xenos::PrimitiveType prim_type=xenos::PrimitiveType::kRectangleList;};
struct RB_SURFACE_INFO {int msaa_samples=0;};
}
struct Registers {template<class T> T Get() {return {};}};
struct Setup {struct {int* depth;int* color[2];} targets;};
struct Renderer {
  bool in_pass=false,edram_depth_alias_clear=true;
  int target=1,targets=1;
  Registers r;
  Setup setup{{&target,{nullptr,nullptr}}};
  void AliasDepthClearToColor(int) {in_pass=false;}
  bool BeginPass(int) {in_pass=true;return true;}
  void skip(int) {}
  void Draw() {
  // Record.
  if (!BeginPass(targets)) return skip(14);
  if (edram_depth_alias_clear && setup.targets.depth && !setup.targets.color[0] &&
      setup.targets.color[1] == nullptr) {
    auto draw_initiator = r.Get<reg::VGT_DRAW_INITIATOR>();
    if (draw_initiator.prim_type == xenos::PrimitiveType::kRectangleList) {
      AliasDepthClearToColor(r.Get<reg::RB_SURFACE_INFO>().msaa_samples);
    }
  }
    std::cout<<in_pass;
  }
};
int main(){Renderer renderer;renderer.Draw();}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for label, source, expected in [('before', fixture, '0'),
                                             ('after', alias_pass_source(fixture), '1')]:
                cpp = directory / (label + '.cpp')
                cpp.write_text(source)
                executable = directory / (label + ('.exe' if sys.platform == 'win32' else ''))
                subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(executable)],
                               check=True, capture_output=True)
                output = subprocess.run([str(executable)], check=True, capture_output=True, text=True).stdout
                self.assertEqual(output, expected)

    def test_rectangle_pc_phi_includes_the_late_added_predecessor(self):
        compiler = shutil.which('g++') or shutil.which('clang++')
        if not compiler:
            self.skipTest('C++ compiler unavailable')
        fixture = r'''
#include <vector>
#include <iostream>
namespace spv { struct Block {}; }
struct Builder {
  spv::Block* point=nullptr;
  void setBuildPoint(spv::Block* block) { point=block; }
  spv::Block* getBuildPoint() { return point; }
  void createBranch(spv::Block*) {}
};
struct Function { void addBlock(spv::Block*) {} };
int main() {
  Builder storage; Builder* builder_=&storage;
  Function function; Function* function_main_=&function;
  spv::Block header,continuation,merge,exit;
  auto* main_loop_header_=&header;
  auto* main_loop_continue_=&continuation;
  auto* main_loop_merge_=&merge;
  auto* main_loop_exit_=&exit;
  bool has_main_switch=true;
  std::vector<unsigned> incoming_pc{7,100}, phi;
  {
    builder_->setBuildPoint(main_loop_continue_);
    if (has_main_switch) {
      phi=incoming_pc;
    }
    builder_->createBranch(main_loop_header_);
    // Rectangle iteration adds the program counter 0 and its predecessor.
    incoming_pc.push_back(0); incoming_pc.push_back(200);
    if (main_loop_exit_ != main_loop_merge_) {
      function_main_->addBlock(main_loop_exit_);
      builder_->setBuildPoint(main_loop_exit_);
    }
  }

  // Write data for the last memexport.
  std::cout<<phi.size();
}
'''
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for label, source, expected in [('before', fixture, '2'),
                                             ('after', translator_source(fixture), '4')]:
                cpp = directory / (label + '.cpp')
                cpp.write_text(source)
                executable = directory / (label + ('.exe' if sys.platform == 'win32' else ''))
                subprocess.run([compiler, '-std=c++17', str(cpp), '-o', str(executable)],
                               check=True, capture_output=True)
                output = subprocess.run([str(executable)], check=True, capture_output=True, text=True).stdout
                self.assertEqual(output, expected)

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
