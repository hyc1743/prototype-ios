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
    text = replace_once(text, 'namespace p2::native {\nnamespace shaders {',
        '''static std::atomic<uint64_t> prototype_probe_max_draws{0};
extern "C" uint64_t PrototypeNativeProbeMaxDraws() {
  return prototype_probe_max_draws.load(std::memory_order_relaxed);
}
REXCVAR_DEFINE_UINT32(proto_apple_debug_raster, 0, "Prototype",
                       "Diagnostic only: bit 0 bypasses depth/stencil, bit 1 bypasses culling");

namespace p2::native {
namespace shaders {''')
    text = replace_once(text, '  key.layout = (uint64_t(st.counts[0]) << 48)',
        '''  if (REXCVAR_GET(proto_apple_debug_raster) & 1u) {
    key.depth_test = key.depth_write = key.stencil_test = 0;
  }
  if (REXCVAR_GET(proto_apple_debug_raster) & 2u) key.cull_mode = VK_CULL_MODE_NONE;
  key.layout = (uint64_t(st.counts[0]) << 48)''')
    text = replace_once(text, '  // Water debug cycle: advance once a frame after the water appeared (cached setups do not',
        '''  if (std::getenv("PROTOTYPE_NATIVE_DUMP") && frame_draw_count >= 1000) {
    prototype_probe_max_draws.store(frame_draw_count, std::memory_order_relaxed);
    if (capture_count == 0) ArmCapture("Apple scene diagnosis");
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
  if (submission_draws >= 128) Submit();
#endif''')
    text = replace_once(text, '    s.pending = true;\n    recording = false;',
                        '    s.pending = true;\n    submission_draws = 0;\n    recording = false;')
    text = replace_once(text, '  SubmitDraw(d);\n  ProfileDraw(d.count);',
                        '  SubmitDraw(d);\n  ++submission_draws;\n  ProfileDraw(d.count);')
    text = replace_once(text, '      if (ok) translation->GetOrCreateShaderModule();',
        '''      if (ok) {
        if (const char* dump = std::getenv("PROTOTYPE_SPIRV_DUMP"))
          translation->Dump(dump, "spirv");
        translation->GetOrCreateShaderModule();
      }''')
    return alias_pass_source(text)


def audio_source(text):
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
