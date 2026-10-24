"""Connect the pinned SDK's vertex primitive fallback to the native renderer."""
from prepare_source_port import replace_once


def alias_pass_source(text):
    alias = '''  if (edram_depth_alias_clear && setup.targets.depth && !setup.targets.color[0] &&
      setup.targets.color[1] == nullptr) {
    auto draw_initiator = r.Get<reg::VGT_DRAW_INITIATOR>();
    if (draw_initiator.prim_type == xenos::PrimitiveType::kRectangleList) {
      AliasDepthClearToColor(r.Get<reg::RB_SURFACE_INFO>().msaa_samples);
    }
  }
'''
    text = replace_once(text, alias, '')
    # AliasDepthClearToColor closes the active rendering scope and uses its
    # own standalone scope. It must run before recording the actual draw.
    return replace_once(text, '  // Record.\n  if (!BeginPass(targets)) return skip(14);',
                        alias + '\n  // Record.\n  if (!BeginPass(targets)) return skip(14);')


def capture_pass_source(text):
    # Readbacks end rendering and invalidate cached bindings. Run them before
    # BeginPass and DrawCommand construction so both are rebuilt afterwards.
    start = text.index('  if (water_dump_enabled && setup.active_vs &&',
                       text.index('  // Record.\n  if (!BeginPass(targets))'))
    end = text.index('  if (profile_draws && profile_frame) {', start)
    capture = text[start:end]
    text = text[:start] + text[end:]
    capture = capture.replace('TraceSimDraw(d.count)', 'TraceSimDraw(draw_count)')
    capture = capture.replace('WaterSequenceDump(d.count)', 'WaterSequenceDump(draw_count)')
    return replace_once(text, '  // Record.\n  if (!BeginPass(targets))',
                        capture + '\n  // Record.\n  if (!BeginPass(targets))')


def attachment_pass_source(text):
    # Pipelines are keyed by the draw's exact attachments. The device does
    # not enable dynamicRenderingUnusedAttachments, so a superset render
    # scope violates vkCmdDraw 06179 / 08910 / 08914.
    text = replace_once(text,
        '      if (targets.color[i] && targets.color[i] != pass.color[i]) compatible = false;',
        '      if (targets.color[i] != pass.color[i]) compatible = false;')
    text = replace_once(text,
        '    if (targets.depth && targets.depth != pass.depth) compatible = false;',
        '    if (targets.depth != pass.depth) compatible = false;')
    return text.replace(
        '// Keep the scope if every attachment the draw needs is already bound at\n'
        '    // the same slot.',
        '// Reuse only an exact attachment set: the pipeline formats must\n'
        '    // match every attachment in this dynamic rendering scope.')


def constant_descriptor_source(text):
    # Consecutive descriptor updates may cross bindings only when the stage
    # flags agree. These five UBO bindings use different vertex/pixel masks.
    before = '''  VkWriteDescriptorSet writes[2] = {};
  writes[0].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
  writes[0].dstSet = set_shared;
  writes[0].descriptorCount = shared_binding_count;
  writes[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
  writes[0].pBufferInfo = shared_infos.data();
  writes[1].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
  writes[1].dstSet = set_constants;
  writes[1].descriptorCount = SpirvShaderTranslator::kConstantBufferCount;
  writes[1].descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
  writes[1].pBufferInfo = constant_infos;
  dfn().vkUpdateDescriptorSets(vk, 2, writes, 0, nullptr);'''
    after = '''  VkWriteDescriptorSet writes[1 + SpirvShaderTranslator::kConstantBufferCount] = {};
  writes[0].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
  writes[0].dstSet = set_shared;
  writes[0].descriptorCount = shared_binding_count;
  writes[0].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
  writes[0].pBufferInfo = shared_infos.data();
  for (uint32_t i = 0; i < SpirvShaderTranslator::kConstantBufferCount; ++i) {
    auto& write = writes[1 + i];
    write.sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    write.dstSet = set_constants;
    write.dstBinding = i;
    write.descriptorCount = 1;
    write.descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
    write.pBufferInfo = &constant_infos[i];
  }
  dfn().vkUpdateDescriptorSets(vk, 1 + SpirvShaderTranslator::kConstantBufferCount,
                              writes, 0, nullptr);'''
    return replace_once(text, before, after)


def static_constants_source(text):
    text = text.replace('VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC', 'constant_descriptor_type')
    text = replace_once(text, '  VkDescriptorSet set_constants = VK_NULL_HANDLE;',
        '''  VkDescriptorSet set_constants = VK_NULL_HANDLE;
  bool use_static_constants = false;
  VkDescriptorType constant_descriptor_type = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;''')
    text = replace_once(text, '  // Set 1: the five constant buffers, dynamic so one set serves every draw.',
        '''  use_static_constants = REXCVAR_GET(proto_apple_static_constants);
  constant_descriptor_type = use_static_constants ? VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER
                                                  : VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER_DYNAMIC;
  REXLOG_INFO("[native] per-draw static constant descriptors={}", use_static_constants);
  // Set 1: the five constant buffers, dynamic so one set serves every draw.''')
    text = replace_once(text,
        '''            {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 256},
        };''',
        '''            {VK_DESCRIPTOR_TYPE_STORAGE_IMAGE, 256},
            {VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER, 16384},
        };''')
    text = replace_once(text, '        pool_info.poolSizeCount = 3;',
                        '        pool_info.poolSizeCount = use_static_constants ? 4 : 3;')
    text = replace_once(text,
        '''                                &d.set_constants, SpirvShaderTranslator::kConstantBufferCount,
                                d.offsets);''',
        '''                                &d.set_constants,
                                use_static_constants ? 0u : SpirvShaderTranslator::kConstantBufferCount,
                                use_static_constants ? nullptr : d.offsets);''')
    text = replace_once(text,
        '  if (!bound_offsets_valid || std::memcmp(bound_offsets, d.offsets, sizeof(d.offsets))) {',
        '  if (use_static_constants || !bound_offsets_valid || std::memcmp(bound_offsets, d.offsets, sizeof(d.offsets))) {')
    text = replace_once(text, '    d.set_constants = set_constants;',
                        '    d.set_constants = draw_constants_set;')
    return replace_once(text, '  // Record.\n  if (!BeginPass(targets)) return skip(14);',
        '''  VkDescriptorSet draw_constants_set = set_constants;
  if (use_static_constants) {
    draw_constants_set = AllocateSet(layout_constants);
    if (!draw_constants_set) return skip(9);
    const VkDeviceSize offsets[] = {system_offset, vs_float_offset, ps_float_offset,
                                    bool_loop_offset, fetch_offset};
    const VkDeviceSize ranges[] = {sizeof(SpirvShaderTranslator::SystemConstants),
        kFloatConstantsBytes, kFloatConstantsBytes, kBoolLoopConstantsBytes, kFetchConstantsBytes};
    VkDescriptorBufferInfo infos[SpirvShaderTranslator::kConstantBufferCount] = {};
    VkWriteDescriptorSet writes[SpirvShaderTranslator::kConstantBufferCount] = {};
    for (uint32_t i = 0; i < SpirvShaderTranslator::kConstantBufferCount; ++i) {
      infos[i] = {stream_buffer, offsets[i], ranges[i]};
      writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
      writes[i].dstSet = draw_constants_set;
      writes[i].dstBinding = i;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER;
      writes[i].pBufferInfo = &infos[i];
    }
    dfn().vkUpdateDescriptorSets(vk, SpirvShaderTranslator::kConstantBufferCount,
                                writes, 0, nullptr);
  }
  // Record.
  if (!BeginPass(targets)) return skip(14);''')


def exception_source(text):
    text = replace_once(text, '#include <signal.h>', '#include <signal.h>\n#include <unistd.h>')
    return replace_once(text, '''  }
}

void ExceptionHandler::Install(Handler fn, void* data) {''', '''  }
  // Returning from an unhandled host fault retries the faulting instruction
  // forever. In MoltenVK this looked like a silent black-screen hang.
  const char message[] = "[prototype-source] unhandled host fault; forwarding to OS handler\\n";
  (void)write(STDERR_FILENO, message, sizeof(message) - 1);
  const struct sigaction* previous = signal_number == SIGILL ? &original_sigill_handler_
                                                           : &original_sigsegv_handler_;
#if REX_PLATFORM_MAC
  if (signal_number == SIGBUS) previous = &original_sigbus_handler_;
#endif
  if (previous->sa_handler != SIG_DFL && previous->sa_handler != SIG_IGN) {
    if (previous->sa_flags & SA_SIGINFO) previous->sa_sigaction(signal_number, signal_info, signal_context);
    else previous->sa_handler(signal_number);
    return;
  }
  struct sigaction fatal = {};
  fatal.sa_handler = SIG_DFL;
  sigemptyset(&fatal.sa_mask);
  (void)sigaction(signal_number, &fatal, nullptr);
  (void)raise(signal_number);
}

void ExceptionHandler::Install(Handler fn, void* data) {''')


def translator_source(text):
    # Rectangle iteration adds another predecessor to the continue block.
    # The original builds its PC Phi before adding that predecessor, leaving
    # malformed SSA for shaders with guest control flow.
    start = text.index('    builder_->setBuildPoint(main_loop_continue_);\n    if (has_main_switch) {')
    end = text.index('    builder_->createBranch(main_loop_header_);', start)
    end += len('    builder_->createBranch(main_loop_header_);')
    continuation = text[start:end]
    text = text[:start] + text[end:]
    anchor = '''      builder_->setBuildPoint(main_loop_exit_);
    }
  }

  // Write data for the last memexport.'''
    replacement = '''      builder_->setBuildPoint(main_loop_exit_);
    }
    // All incoming PC values, including rectangle iteration, are now known.
    spv::Block* after_main_loop = builder_->getBuildPoint();
''' + continuation + '''
    builder_->setBuildPoint(after_main_loop);
  }

  // Write data for the last memexport.'''
    return replace_once(text, anchor, replacement)


def renderer_source(text):
    text = replace_once(text, '#include "native_geometry_shaders.h"',
                        '#include "native_geometry_shaders.h"\n#include "PrototypePrimitiveIndices.h"')
    text = replace_once(text, '  water_dump_enabled = REXCVAR_GET(p2_native_water_dump);',
        '  water_dump_enabled = REXCVAR_GET(p2_native_water_dump) || std::getenv("PROTOTYPE_NATIVE_DUMP");')
    text = replace_once(text,
        '  if (ps_hash == UINT64_C(0x3E0AB1A6A7CD8CFB) && !post_dumped_edge) {',
        '''  if (capture_all && (ps_hash == UINT64_C(0x7FFB2EDAC403E49B) ||
                      ps_hash == UINT64_C(0x45763A31BB3B49B4) ||
                      ps_hash == UINT64_C(0xFEECC8EB051119C6))) {
    REXLOG_INFO("[native] APPLEPOST PS {:016X} VS{} PS{} |{}{}{}{}", ps_hash,
                ConstantsText(0, 8), ConstantsText(256, 16),
                FetchText(0), FetchText(1), FetchText(2), FetchText(3));
    for (uint32_t index = 0; index < 4; ++index)
      dump_fetch(index, fmt::format("post_{:016X}", ps_hash).c_str());
  }
  if (ps_hash == UINT64_C(0x3E0AB1A6A7CD8CFB) && !post_dumped_edge) {''')
    text = replace_once(text, 'namespace p2::native {\nnamespace shaders {',
        '''static std::atomic<uint64_t> prototype_probe_max_draws{0};
static uint32_t prototype_post_probe_mode = 0;
extern "C" uint64_t PrototypeNativeProbeMaxDraws() {
  return prototype_probe_max_draws.load(std::memory_order_relaxed);
}
REXCVAR_DEFINE_UINT32(proto_apple_debug_raster, 0, "Prototype",
                       "Diagnostic: bit 0 no depth/stencil, bit 1 no cull, bit 2 solid post, bit 3 fixed post VS");
REXCVAR_DEFINE_BOOL(proto_apple_normal_depth, false, "Prototype",
                       "Diagnostic: preserve guest depth through a non-reversed host viewport");
REXCVAR_DEFINE_BOOL(proto_apple_static_constants, false, "Prototype",
                       "Diagnostic: use immutable per-draw constant descriptors without dynamic offsets");
REXCVAR_DEFINE_BOOL(proto_apple_relax_post_vertex, true, "Prototype",
                       "Use native arithmetic for Prototype's full-screen vertex shader on Apple GPUs");
REXCVAR_DEFINE_UINT32(proto_apple_draw_batch, 128, "Prototype",
                       "Apple bounded GPU draw batch, clamped to 64..256");

namespace p2::native {
namespace shaders {''')
    text = replace_once(text,
        '                                 properties.maxViewportDimensions[1], true, depth_control, false,',
        '                                 properties.maxViewportDimensions[1],\n'
        '                                 !REXCVAR_GET(proto_apple_normal_depth), depth_control, false,')
    text = replace_once(text, '  key.layout = (uint64_t(st.counts[0]) << 48)',
        '''  if (REXCVAR_GET(proto_apple_debug_raster) & 1u) {
    key.depth_test = key.depth_write = key.stencil_test = 0;
  }
  if (REXCVAR_GET(proto_apple_debug_raster) & 2u) key.cull_mode = VK_CULL_MODE_NONE;
  if (REXCVAR_GET(proto_apple_relax_post_vertex) && vs &&
      vs->ucode_data_hash() == UINT64_C(0x8CF6223D9D62D0AD))
    key.tessellation_reserved = 6;
  if ((prototype_post_probe_mode || (REXCVAR_GET(proto_apple_debug_raster) & 4u)) && ps &&
      ps->ucode_data_hash() == UINT64_C(0xFEECC8EB051119C6)) {
    key.tessellation_reserved = prototype_post_probe_mode ? prototype_post_probe_mode :
        ((REXCVAR_GET(proto_apple_debug_raster) & 8u) ? 2u : 1u);
    key.depth_test = key.depth_write = key.stencil_test = 0;
    key.cull_mode = VK_CULL_MODE_NONE;
    key.blend[0] = 0x00010001;
    key.write_masks |= 0xFu;
  }
  key.layout = (uint64_t(st.counts[0]) << 48)''')
    text = replace_once(text, '  VkShaderModule water_debug_ps = VK_NULL_HANDLE;',
        '  VkShaderModule water_debug_ps = VK_NULL_HANDLE;\n'
        '  VkShaderModule prototype_debug_fullscreen_vs = VK_NULL_HANDLE;\n'
        '  VkShaderModule prototype_debug_scene_ps = VK_NULL_HANDLE;\n'
        '  VkShaderModule prototype_debug_vertex_data_ps = VK_NULL_HANDLE;\n'
        '  std::unordered_map<VulkanShader::VulkanTranslation*, VkShaderModule> prototype_relaxed_vs;')
    text = replace_once(text, '  if (key.tessellation_reserved == 1) {',
                        '  if (key.tessellation_reserved == 1 || key.tessellation_reserved == 2) {')
    text = replace_once(text, '    if (water_debug_ps) job.pixel = water_debug_ps;',
        '''    if (!water_debug_ps) {
      REXLOG_ERROR("[native] Pipeline creation failed: diagnostic solid fragment shader");
      return VK_NULL_HANDLE;
    }
    job.pixel = water_debug_ps;''')
    text = replace_once(text, '  job.geometry = geometry;\n  job.layout = layout;',
        '''  if (key.tessellation_reserved == 6) {
    auto found = prototype_relaxed_vs.find(vs);
    if (found == prototype_relaxed_vs.end()) {
      const auto& bytes = vs->translated_binary();
      if (bytes.size() < 20 || bytes.size() % sizeof(uint32_t)) return VK_NULL_HANDLE;
      std::vector<uint32_t> input(bytes.size()/sizeof(uint32_t)), output;
      std::memcpy(input.data(), bytes.data(), bytes.size());
      output.insert(output.end(), input.begin(), input.begin()+5);
      uint32_t removed = 0;
      for (size_t i = 5; i < input.size();) {
        const uint32_t count = input[i] >> 16;
        if (!count || count > input.size()-i) return VK_NULL_HANDLE;
        if ((input[i]&0xFFFFu) == uint32_t(spv::OpDecorate) && count == 3 &&
            input[i+2] == uint32_t(spv::DecorationNoContraction)) {
          ++removed;
        } else output.insert(output.end(), input.begin()+i, input.begin()+i+count);
        i += count;
      }
      REXLOG_WARN("[native] post vertex native arithmetic: {} precision decorations removed", removed);
      if (const char* dump = std::getenv("PROTOTYPE_SPIRV_DUMP")) {
        auto path = std::filesystem::path(dump) / fmt::format("shader_{:016X}_relaxed.spirv.bin.vert",
            vs->shader().ucode_data_hash());
        if (FILE* file = std::fopen(path.string().c_str(), "wb")) {
          std::fwrite(output.data(), sizeof(uint32_t), output.size(), file);
          std::fclose(file);
        }
      }
      auto module = rex::ui::vulkan::util::CreateShaderModule(
          device, output.data(), output.size()*sizeof(uint32_t));
      found = prototype_relaxed_vs.emplace(vs, module).first;
    }
    if (!found->second) {
      REXLOG_ERROR("[native] Pipeline creation failed: diagnostic native vertex arithmetic");
      return VK_NULL_HANDLE;
    }
    job.vertex = found->second;
  }
  if (key.tessellation_reserved == 3) {
    if (!prototype_debug_scene_ps) {
      std::vector<uint32_t> spirv;
      if (tessellation::CompileFragmentShader(
          "#version 450\\nlayout(set=3,binding=4) uniform texture2DArray scene;\\n"
          "layout(set=3,binding=10) uniform sampler smp;\\n"
          "layout(location=0) in vec4 uv;layout(location=0) out vec4 c;\\n"
          "void main(){c=vec4(texture(sampler2DArray(scene,smp),vec3(uv.xy,0)).rgb,1); }\\n", spirv))
        prototype_debug_scene_ps = rex::ui::vulkan::util::CreateShaderModule(
            device, spirv.data(), spirv.size()*sizeof(uint32_t));
    }
    if (!prototype_debug_scene_ps) {
      REXLOG_ERROR("[native] Pipeline creation failed: diagnostic scene fragment shader");
      return VK_NULL_HANDLE;
    }
    job.pixel = prototype_debug_scene_ps;
  }
  if (key.tessellation_reserved == 4) {
    if (!prototype_debug_vertex_data_ps) {
      std::vector<uint32_t> spirv;
      if (tessellation::CompileFragmentShader(
          "#version 450\\nlayout(set=0,binding=0,std430) readonly buffer S{uint words[];};\\n"
          "layout(set=1,binding=4,std140) uniform F{uvec4 fetch[48];};\\n"
          "layout(set=1,binding=0,std140) uniform C{uint flags;"
          "layout(offset=16) int indexBase;layout(offset=124) float nx;float ny;float nz;"
          "layout(offset=140) float ox;float oy;float oz;"
          "layout(offset=288) float bias;layout(offset=496) uint imin;uint imax;};\\n"
          "layout(set=1,binding=2,std140) uniform P{vec4 ps[9];};\\n"
          "layout(location=0) out vec4 c;\\n"
          "void main(){uint i=uint(gl_FragCoord.x)/8u;uint v=0u;"
          "uint base=(fetch[47].z>>2u)&0x7ffffffu;"
          "if(i==0u)v=fetch[47].z;else if(i==1u)v=fetch[47].w;"
          "else if(i<26u){uint p=base+i-2u;v=p<uint(words.length())?words[p]:0xDEADBAD0u;}"
          "else if(i==26u)v=flags;else if(i==27u)v=uint(indexBase);"
          "else if(i==28u)v=imin;else if(i==29u)v=imax;"
          "else if(i==30u)v=floatBitsToUint(nx);else if(i==31u)v=floatBitsToUint(ny);"
          "else if(i==32u)v=floatBitsToUint(nz);else if(i==33u)v=floatBitsToUint(ox);"
          "else if(i==34u)v=floatBitsToUint(oy);else if(i==35u)v=floatBitsToUint(oz);"
          "else if(i==36u)v=floatBitsToUint(bias);"
          "else if(i<49u){uint p=i-37u;v=floatBitsToUint(ps[p/4u][p%4u]);}"
          "c=vec4(v&255u,(v>>8u)&255u,(v>>16u)&255u,(v>>24u)&255u)/255.0;}\\n", spirv))
        prototype_debug_vertex_data_ps = rex::ui::vulkan::util::CreateShaderModule(
            device, spirv.data(), spirv.size()*sizeof(uint32_t));
    }
    if (!prototype_debug_vertex_data_ps) {
      REXLOG_ERROR("[native] Pipeline creation failed: diagnostic vertex data shader");
      return VK_NULL_HANDLE;
    }
    job.pixel = prototype_debug_vertex_data_ps;
  }
  if (key.tessellation_reserved == 2 || key.tessellation_reserved == 4) {
    if (!prototype_debug_fullscreen_vs)
      prototype_debug_fullscreen_vs = rex::ui::vulkan::util::CreateShaderModule(
          device, shaders::kFullscreenVs, sizeof(shaders::kFullscreenVs));
    if (!prototype_debug_fullscreen_vs) {
      REXLOG_ERROR("[native] Pipeline creation failed: diagnostic fixed vertex shader");
      return VK_NULL_HANDLE;
    }
    job.vertex = prototype_debug_fullscreen_vs;
  }
  if (key.tessellation_reserved)
    REXLOG_INFO("[native] APPLEPOST_STAGE pipeline_mode={}", key.tessellation_reserved);
  job.geometry = geometry;
  job.layout = layout;''')
    text = replace_once(text, '  vkDeviceWaitIdle(vk);\n  textures.Shutdown();',
        '''  vkDeviceWaitIdle(vk);
  if (prototype_debug_fullscreen_vs)
    dfn().vkDestroyShaderModule(vk, prototype_debug_fullscreen_vs, nullptr);
  if (prototype_debug_scene_ps)
    dfn().vkDestroyShaderModule(vk, prototype_debug_scene_ps, nullptr);
  if (prototype_debug_vertex_data_ps)
    dfn().vkDestroyShaderModule(vk, prototype_debug_vertex_data_ps, nullptr);
  for (const auto& entry : prototype_relaxed_vs)
    if (entry.second) dfn().vkDestroyShaderModule(vk, entry.second, nullptr);
  textures.Shutdown();''')
    text = replace_once(text, '  // Water debug cycle: advance once a frame after the water appeared (cached setups do not',
        '''  if (std::getenv("PROTOTYPE_NATIVE_DUMP") && frame_draw_count >= 1000) {
    prototype_probe_max_draws.store(frame_draw_count, std::memory_order_relaxed);
    if (capture_count == 0) ArmCapture("Apple scene diagnosis");
  }
  if (std::getenv("PROTOTYPE_POST_CYCLE") &&
      prototype_probe_max_draws.load(std::memory_order_relaxed) >= 1000) {
    static const auto scene_started = std::chrono::steady_clock::now();
    const auto seconds = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::steady_clock::now() - scene_started).count();
    const uint32_t modes[] = {0, 1, 3, 2, 4};
    const uint32_t mode = modes[std::min<uint64_t>(uint64_t(seconds / 6), 4)];
    if (mode != prototype_post_probe_mode && !capture_all) {
      prototype_post_probe_mode = mode;
      ++rt_generation;
      REXLOG_INFO("[native] APPLEPOST_CYCLE mode={} scene_seconds={}", mode, seconds);
      ArmCapture(fmt::format("Apple post stage {}", mode).c_str());
    }
  }
  // Water debug cycle: advance once a frame after the water appeared (cached setups do not''')
    text = replace_once(text,
        '''      const std::string path =
          fmt::format("sdmc:/switch/Prototype2/logs/{}_{}x{}_vk{}.bin", it->name, it->width,
                      it->height, uint32_t(it->format));''',
        '''      const char* apple_dump = std::getenv("PROTOTYPE_NATIVE_DUMP");
      const std::string path = apple_dump
          ? (std::filesystem::path(apple_dump) / fmt::format("{}_{}x{}_vk{}.bin", it->name,
              it->width, it->height, uint32_t(it->format))).string()
          : fmt::format("sdmc:/switch/Prototype2/logs/{}_{}x{}_vk{}.bin", it->name, it->width,
                        it->height, uint32_t(it->format));''')
    text = replace_once(text,
        '    bool convert_fan = false, convert_loop = false, strip = false, reset_enabled = false;',
        '    bool convert_fan = false, convert_loop = false, strip = false, reset_enabled = false;\n'
        '    bool expand_vertex = false, convert_quad = false;')
    text = replace_once(text,
        '  if (geometry != PipelineGeometryShader::kNone && !properties.geometryShader) return fail(5);',
        '''  if (!properties.geometryShader) {
    if (geometry == PipelineGeometryShader::kPointList ||
        geometry == PipelineGeometryShader::kRectangleList) {
      host_vs_type = geometry == PipelineGeometryShader::kPointList
          ? Shader::HostVertexShaderType::kPointListAsTriangleStrip
          : Shader::HostVertexShaderType::kRectangleListAsTriangleStrip;
      st.expand_vertex = true;
      topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
      geometry = PipelineGeometryShader::kNone;
    } else if (geometry == PipelineGeometryShader::kQuadList) {
      st.convert_quad = true;
      topology = VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST;
      geometry = PipelineGeometryShader::kNone;
    }
  }
  if (geometry != PipelineGeometryShader::kNone && !properties.geometryShader) return fail(5);''')
    # On the point fallback this flag outputs sprite coordinates rather than
    # the point diameter. The translator already reads guest point sizes.
    text = replace_once(text, '  SpirvShaderTranslator::Modification ps_mod(0);',
        '''  if (host_vs_type == Shader::HostVertexShaderType::kPointListAsTriangleStrip)
    vs_mod.vertex.output_point_parameters = ps && ps_param_gen_pos < xenos::kMaxInterpolators;
  SpirvShaderTranslator::Modification ps_mod(0);''')
    text = replace_once(text, '  bool host_indexed = indexed || convert_fan || convert_loop;',
        '''  const bool expand_vertex = st.expand_vertex;
  const bool convert_quad = st.convert_quad;
  VkDeviceSize primitive_index_offset = 0;
  if (expand_vertex && indexed) {
    // Vertex fallback loads original indices from the same shared stream as
    // vertex fetches. Normalize endian/width once, keeping guest ordering.
    uint8_t* original = Allocate(VkDeviceSize(guest_count) * sizeof(uint32_t), 4,
                                 primitive_index_offset);
    if (!original) return skip(9);
    for (uint32_t i = 0; i < guest_count; ++i) {
      uint32_t value = read_index(i);
      std::memcpy(original + size_t(i) * sizeof(uint32_t), &value, sizeof(value));
    }
  }
  bool host_indexed = indexed || convert_fan || convert_loop || expand_vertex || convert_quad;''')
    text = replace_once(text, '    if (convert_fan) {\n      if (guest_count < 3) return;',
        '''    if (expand_vertex || convert_quad) {
      uint32_t guest_per_primitive = convert_quad ? 4u :
          (initiator.prim_type == xenos::PrimitiveType::kRectangleList ? 3u : 1u);
      draw_count = ExpandedTriangleIndexCount(guest_count, guest_per_primitive);
      if (!draw_count) return;
    } else if (convert_fan) {
      if (guest_count < 3) return;''')
    text = replace_once(text, '    if (!indexed) {\n      index_type = VK_INDEX_TYPE_UINT32;',
                        '    if (!indexed || expand_vertex || convert_quad) {\n      index_type = VK_INDEX_TYPE_UINT32;')
    text = replace_once(text, '    if (dma_base && !convert_fan && !convert_loop) {',
                        '    if (dma_base && !convert_fan && !convert_loop && !expand_vertex && !convert_quad) {')
    text = replace_once(text, '      if (convert_fan) {\n        uint32_t first = read_index(0);',
        '''      if (expand_vertex || convert_quad) {
        // Two explicit triangles with the same winding as the SDK's 0,1,2,3
        // strip. Synthetic vertex IDs encode primitive*4 + corner.
        for (uint32_t i = 0; i < draw_count; ++i) {
          uint32_t vertex = ExpandedTriangleVertex(i, convert_quad);
          put(i, convert_quad ? read_index(vertex) : vertex);
        }
      } else if (convert_fan) {
        uint32_t first = read_index(0);''')
    text = replace_once(text, '  bool system_same = last_system.submission == submission_number &&',
        '  bool system_same = !expand_vertex && last_system.submission == submission_number &&')
    text = replace_once(text,
        '    UpdateSystemConstants(st.primitive_polygonal, st.viewport, tb.used_mask, 0, vs, ps);',
        '''    UpdateSystemConstants(st.primitive_polygonal, st.viewport, tb.used_mask, 0, vs, ps);
    if (expand_vertex && indexed) {
      system_constants.flags |= SpirvShaderTranslator::kSysFlag_ComputeOrPrimitiveVertexIndexLoad |
                                SpirvShaderTranslator::kSysFlag_ComputeOrPrimitiveVertexIndexLoad32Bit;
      system_constants.vertex_index_load_address = uint32_t(primitive_index_offset);
    }''')
    # Sparse, passive diagnostics survive the warn-only production setting.
    text = replace_once(text, '  stats().frame_draws = frame_draw_count;',
        '''  if (frame % 300 == 0) {
    REXLOG_WARN("[native] frame={} draws={} skipped={} no_geometry={} translation={} pipeline={} frontbuffer={:08X} view={} age={}",
                frame, frame_draw_count, stats().draws_skipped, stats().skipped_by_reason[5],
                stats().skipped_by_reason[6], stats().skipped_by_reason[13], address,
                source_view != VK_NULL_HANDLE,
                source_dest && frame >= source_dest->last_written_frame
                    ? frame - source_dest->last_written_frame : UINT64_MAX);
  }
  stats().frame_draws = frame_draw_count;''')
    text = replace_once(text, '           frame_texture_ns = 0, frame_draw_count = 0, frame_stream_wait_ns = 0;',
        '           frame_texture_ns = 0, frame_draw_count = 0, frame_stream_wait_ns = 0;\n'
        '  uint32_t submission_draws = 0;')
    text = replace_once(text, 'void Renderer::Impl::Draw(const uint32_t* immediate_indices, uint32_t immediate_index_words) {',
        '''void Renderer::Impl::Draw(const uint32_t* immediate_indices, uint32_t immediate_index_words) {
#if defined(__APPLE__)
  // A full scene may contain thousands of draws. Keep each Metal command
  // buffer bounded so the first gameplay frame cannot trip the watchdog.
  if (submission_draws >= std::clamp(REXCVAR_GET(proto_apple_draw_batch), 64u, 256u)) Submit();
#endif''')
    text = replace_once(text, '    s.pending = true;\n    recording = false;',
                        '    s.pending = true;\n    submission_draws = 0;\n    recording = false;')
    text = replace_once(text, '  SubmitDraw(d);\n  ProfileDraw(d.count);',
        '''  SubmitDraw(d);
  ++submission_draws;
  if (capture_all && prototype_post_probe_mode == 4 && targets.color[0] && ps &&
      ps->ucode_data_hash() == UINT64_C(0xFEECC8EB051119C6)) {
    auto* target = targets.color[0];
    RecordImageDump(target->image, target->format, 512, 1,
                    fmt::format("cap{}_vertex_gpu_words", capture_count));
  }
  ProfileDraw(d.count);''')
    text = replace_once(text, '      if (ok) translation->GetOrCreateShaderModule();',
        '''      if (ok) {
        if (const char* dump = std::getenv("PROTOTYPE_SPIRV_DUMP"))
          translation->Dump(dump, "spirv");
        translation->GetOrCreateShaderModule();
      }''')
    text = constant_descriptor_source(capture_pass_source(alias_pass_source(attachment_pass_source(text))))
    text = static_constants_source(text)
    text = performance_source(text)
    return replace_once(text, '  // Record.\n  if (!BeginPass(targets)) return skip(14);',
        '''  if (capture_all && setup.active_ps &&
      setup.active_ps->ucode_data_hash() == UINT64_C(0xFEECC8EB051119C6)) {
    const auto* host_system = reinterpret_cast<const SpirvShaderTranslator::SystemConstants*>(
        stream_mapping + system_offset);
    const auto* host_vs = reinterpret_cast<const float*>(stream_mapping + vs_float_offset);
    const auto* host_fetch = reinterpret_cast<const uint32_t*>(stream_mapping + fetch_offset);
    const auto* host_ps = reinterpret_cast<const float*>(stream_mapping + ps_float_offset);
    const uint32_t vertex_base = host_fetch[190] & 0x1FFFFFFCu;
    if (uint64_t(vertex_base) + 72 <= kStreamSize) {
      const auto* raw_vertices = reinterpret_cast<const uint32_t*>(stream_mapping + vertex_base);
      std::string vertices;
      for (uint32_t i = 0; i < 18; ++i)
        vertices += fmt::format(" {:08X}", __builtin_bswap32(raw_vertices[i]));
      REXLOG_INFO("[native] APPLEPOST_VERTEX_HOST{}", vertices);
    }
    std::string packed_ps;
    uint32_t packed_index = 0;
    for (uint32_t word = 0; word < 4; ++word) {
      uint64_t bits = ps->constant_register_map().float_bitmap[word];
      uint32_t bit;
      while (rex::bit_scan_forward(bits, &bit)) {
        bits &= ~(uint64_t(1) << bit);
        packed_ps += fmt::format(" c{}=({},{},{},{})", word*64+bit,
            host_ps[packed_index], host_ps[packed_index+1],
            host_ps[packed_index+2], host_ps[packed_index+3]);
        packed_index += 4;
      }
    }
    REXLOG_INFO("[native] APPLEPOST_PS_HOST blend={:08X}{}",
        r[reg::RB_BLENDCONTROL::rt_register_indices[0]], packed_ps);
    REXLOG_INFO("[native] APPLEPOST_HOST min={} max={} base={} flags={:08X} bias={} "
                "ndc {},{},{} + {},{},{} vf95={:08X},{:08X} "
                "VS0 {},{},{},{} VS1 {},{},{},{} VS2 {},{},{},{} "
                "VS3 {},{},{},{} VS4 {},{},{},{} VS5 {},{},{},{}",
        host_system->vertex_index_min, host_system->vertex_index_max,
        host_system->vertex_base_index, host_system->flags, host_system->color_exp_bias[0],
        host_system->ndc_scale[0], host_system->ndc_scale[1], host_system->ndc_scale[2],
        host_system->ndc_offset[0], host_system->ndc_offset[1], host_system->ndc_offset[2],
        host_fetch[190], host_fetch[191], host_vs[0], host_vs[1], host_vs[2], host_vs[3],
        host_vs[4], host_vs[5], host_vs[6], host_vs[7], host_vs[8], host_vs[9], host_vs[10], host_vs[11],
        host_vs[12], host_vs[13], host_vs[14], host_vs[15], host_vs[16], host_vs[17], host_vs[18], host_vs[19],
        host_vs[20], host_vs[21], host_vs[22], host_vs[23]);
  }
  // Record.
  if (!BeginPass(targets)) return skip(14);''')


def performance_source(text):
    # Aggregate only real scene frames. All timings already exist in the
    # renderer; avoid per-draw clocks or flushing a log line every frame.
    text = replace_once(text, '  uint32_t submission_draws = 0;', '''  uint32_t submission_draws = 0;
  uint32_t apple_perf_count = 0;
  double apple_perf_ms = 0;
  std::array<double, 300> apple_perf_times{};
  uint64_t apple_perf_draws = 0, apple_perf_submits = 0;
  uint64_t apple_perf_fence = 0, apple_perf_upload = 0, apple_perf_drain = 0;
  uint64_t apple_perf_texture = 0, apple_perf_pipeline = 0;
  uint64_t apple_perf_last_pipeline_ns = 0, apple_perf_last_submits = 0;''')
    text = replace_once(text, '  last_swap_time = now;\n  at_last_swap = stats();', '''  if (last_swap_time.time_since_epoch().count() && frame_draw_count >= 1200) {
    const double frame_ms = std::chrono::duration<double, std::milli>(now-last_swap_time).count();
    apple_perf_times[apple_perf_count] = frame_ms;
    apple_perf_ms += frame_ms;
    apple_perf_draws += frame_draw_count;
    apple_perf_submits += stats().submissions - apple_perf_last_submits;
    apple_perf_fence += frame_fence_wait_ns;
    apple_perf_upload += frame_upload_wait_ns;
    apple_perf_drain += frame_drain_wait_ns;
    apple_perf_texture += frame_texture_ns;
    apple_perf_pipeline += stats().pipeline_ns - apple_perf_last_pipeline_ns;
    if (++apple_perf_count == 300) {
      constexpr double ns_per_frame_ms = 300000000.0;
      std::sort(apple_perf_times.begin(), apple_perf_times.end());
      REXLOG_WARN("[apple-perf] 300 scene frames fps={:.2f} median_ms={:.2f} p95_ms={:.2f} p99_ms={:.2f} draws/frame={:.1f} submits/frame={:.1f} fence_ms={:.2f} upload_ms={:.2f} recorder_ms={:.2f} texture_ms={:.2f} pipeline_ms={:.2f} batch={}",
          300000.0/apple_perf_ms, apple_perf_times[149], apple_perf_times[284], apple_perf_times[296],
          double(apple_perf_draws)/300, double(apple_perf_submits)/300,
          apple_perf_fence/ns_per_frame_ms, apple_perf_upload/ns_per_frame_ms,
          apple_perf_drain/ns_per_frame_ms, apple_perf_texture/ns_per_frame_ms,
          apple_perf_pipeline/ns_per_frame_ms, REXCVAR_GET(proto_apple_draw_batch));
      apple_perf_count = 0; apple_perf_ms = 0;
      apple_perf_draws = apple_perf_submits = apple_perf_fence = apple_perf_upload = 0;
      apple_perf_drain = apple_perf_texture = apple_perf_pipeline = 0;
    }
  } else {
    // Menu/loading intervals must not contaminate the scene window.
    apple_perf_count = 0; apple_perf_ms = 0;
    apple_perf_draws = apple_perf_submits = apple_perf_fence = apple_perf_upload = 0;
    apple_perf_drain = apple_perf_texture = apple_perf_pipeline = 0;
  }
  apple_perf_last_submits = stats().submissions;
  apple_perf_last_pipeline_ns = stats().pipeline_ns;
  last_swap_time = now;
  at_last_swap = stats();''')
    # Percentile diagnostics must survive the shipped warn-only log level.
    return text.replace('REXLOG_INFO("[p2-frame] copy_phase=',
                        'REXLOG_WARN("[p2-frame] copy_phase=')


def audio_source(text):
    text = replace_once(text, '  SDL_AudioSpec obtained_spec = {};',
                        '  SDL_AudioSpec obtained_spec = {};\n  int obtained_sample_frames = 0;')
    text = replace_once(text, 'SDL_GetAudioDeviceFormat(sdl_device, &obtained_spec, NULL)',
                        'SDL_GetAudioDeviceFormat(sdl_device, &obtained_spec, &obtained_sample_frames)')
    text = replace_once(text, '  if (!SDL_ResumeAudioDevice(sdl_device)) {',
        '''  REXAPU_WARN("[audio] device buffer={} sample frames ({:.3f} ms at {} Hz)",
              obtained_sample_frames, 1000.0 * obtained_sample_frames /
                  std::max(obtained_spec.freq, 1), obtained_spec.freq);
  if (!SDL_ResumeAudioDevice(sdl_device)) {''')
    text = replace_once(text, 'void SDLAudioDriver::Pump() {\n  using Clock',
        '''void SDLAudioDriver::Pump() {
#if defined(__APPLE__)
  if (!SDL_SetCurrentThreadPriority(SDL_THREAD_PRIORITY_HIGH))
    REXAPU_WARN("[audio] Cannot raise pacing thread priority: {}", SDL_GetError());
#endif
  using Clock''')
    text = replace_once(text, '  desired_spec.channels = frame_channels_;\n  sdl_device_channels_ = frame_channels_;',
        '''#if defined(__APPLE__)
  // A six-channel logical endpoint may be negotiated even for iPhone speakers.
  // Submit the SDK's explicit stereo fold and let SDL negotiate the route.
  desired_spec.channels = 2;
#else
  desired_spec.channels = frame_channels_;
#endif
  sdl_device_channels_ = uint8_t(desired_spec.channels);''')
    text = text.replace('                                          SDLCallback, this);',
        '''#if defined(__APPLE__)
                                          REXCVAR_GET(audio_sdl_pump) ? nullptr : SDLCallback, this);
#else
                                          SDLCallback, this);
#endif''')
    text = replace_once(text, '    size_t in_queue;\n    {',
        '''    size_t in_queue;
#if defined(__APPLE__)
    const int queued_bytes = SDL_GetAudioStreamQueued(sdl_stream_);
    if (queued_bytes < 0) break;
    in_queue = size_t(queued_bytes) / (channel_samples_ * sdl_device_channels_ * sizeof(float));
#else
    {''')
    text = replace_once(text, '      in_queue = frames_queued_.size();\n    }',
                        '      in_queue = frames_queued_.size();\n    }\n#endif')
    text = replace_once(text, '  float* output_frame;\n  {',
        '''#if defined(__APPLE__)
  if (active_pump_.load(std::memory_order_relaxed)) {
    // Produce directly into SDL's thread-safe stream. Waiting until the
    // realtime device callback to pop a guest queue inserts silent blocks
    // even when the guest finishes immediately after that callback.
    std::array<float, channel_samples_ * 2> stereo;
    if (REXCVAR_GET(audio_mute)) stereo.fill(0.0f);
    else conversion::sequential_6_BE_to_interleaved_2_LE(
        stereo.data(), input_frame, channel_samples_, GetStereoFold(), GetOutputGain());
    if (!SDL_PutAudioStreamData(sdl_stream_, stereo.data(), int(sizeof(stereo))))
      REXAPU_ERROR("[audio] Apple stream submission failed: {}", SDL_GetError());
    RecordOutput(stereo.data(), int(sizeof(stereo)), 2);
    CountFrame(false);
    return;
  }
#endif
  float* output_frame;
  {''')
    text = replace_once(text,
        "      std::this_thread::yield();  // no sleep: on Windows it can overshoot by several ms",
        '''#if defined(__APPLE__)
      std::this_thread::sleep_until(deadline);
#else
      std::this_thread::yield();  // Desktop diagnostic behavior.
#endif''')
    text = replace_once(text, 'REXAPU_INFO("[audio] SDL in 10 s:', 'REXAPU_WARN("[audio] SDL in 10 s:')
    text = replace_once(text, 'REXAPU_INFO("audio endpoint', 'REXAPU_WARN("audio endpoint')
    text = replace_once(text, '#include <chrono>', '#include <chrono>\n#include <cmath>')
    text = replace_once(text, 'uint64_t frames_with_data = 0;',
        '''float output_peak = 0.0f;
uint64_t output_nonfinite = 0;
uint64_t output_nonzero = 0;
uint64_t frames_with_data = 0;''')
    text = replace_once(text, '  const int32_t seconds = REXCVAR_GET(audio_dump_output_s);',
        '''  for (int i = 0; i < bytes / int(sizeof(float)); ++i) {
    if (!std::isfinite(data[i])) { ++output_nonfinite; continue; }
    output_peak = std::max(output_peak, std::fabs(data[i]));
    output_nonzero += std::fabs(data[i]) > 0.00001f;
  }
  const int32_t seconds = REXCVAR_GET(audio_dump_output_s);''')
    text = replace_once(text, '  frames_with_data = 0;\n  frames_of_silence = 0;',
        '''  REXAPU_WARN("[audio] output peak={} active_samples={} nonfinite={}",
              output_peak, output_nonzero, output_nonfinite);
  output_peak = 0;
  output_nonzero = output_nonfinite = 0;
  frames_with_data = 0;
  frames_of_silence = 0;''')
    return text
