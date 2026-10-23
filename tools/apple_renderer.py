"""Reviewed source overlay for primitive expansion on Apple GPUs."""
from prepare_source_port import replace_once


def primitive_renderer_source(text):
    text = replace_once(text, '#include "native_geometry_shaders.h"',
                        '#include "native_geometry_shaders.h"\n#include "PrototypePrimitiveTessellation.h"')
    text = replace_once(text, '  bool tessellation_supported = false;',
                        '  std::unordered_map<uint32_t, std::array<VkShaderModule, 2>> primitive_tessellation;\n'
                        '  bool tessellation_supported = false;')
    text = replace_once(text, '    VkShaderModule tess_control = VK_NULL_HANDLE;',
                        '    VkShaderModule tess_control = VK_NULL_HANDLE;\n'
                        '    VkShaderModule primitive_evaluation = VK_NULL_HANDLE;')
    text = replace_once(text, '  geometry_shaders.clear();', '''  geometry_shaders.clear();
  for (const auto& entry : primitive_tessellation)
    for (VkShaderModule module : entry.second)
      if (module) dfn().vkDestroyShaderModule(vk, module, nullptr);
  primitive_tessellation.clear();''')
    text = replace_once(text,
        '  if (geometry != PipelineGeometryShader::kNone && !properties.geometryShader) return fail(5);',
        '''  const bool primitive_tessellated =
      geometry != PipelineGeometryShader::kNone && !properties.geometryShader &&
      tessellation_supported && (geometry == PipelineGeometryShader::kRectangleList ||
                                 geometry == PipelineGeometryShader::kQuadList);
  if (geometry != PipelineGeometryShader::kNone && !properties.geometryShader &&
      !primitive_tessellated) {
    static uint32_t unsupported_primitives = 0;
    if (unsupported_primitives++ < 8)
      REXLOG_WARN("[native] Unsupported primitive {} (geometry={}, tessellation={})",
                  uint32_t(guest_type), properties.geometryShader, tessellation_supported);
    return fail(5);
  }
  if (primitive_tessellated) topology = VK_PRIMITIVE_TOPOLOGY_PATCH_LIST;''')
    text = replace_once(text, '    key.geometry_shader = gs_key.key;\n    auto it = geometry_shaders.find(gs_key.key);',
        '''    key.geometry_shader = gs_key.key;
    if (primitive_tessellated) {
      key.tessellation_reserved = 2;
    } else {
    auto it = geometry_shaders.find(gs_key.key);''')
    text = replace_once(text, '    if (!geometry_module) return fail(12);',
                        '    if (!geometry_module) return fail(12);\n    }')
    text = replace_once(text, '  if (key.tessellation) {\n    const auto type', '''  if (key.tessellation_reserved == 2) {
    GeometryShaderKey primitive;
    primitive.key = key.geometry_shader;
    auto [it, added] = primitive_tessellation.try_emplace(primitive.key,
                                                       std::array<VkShaderModule, 2>{});
    if (added) {
      auto source = PrimitiveTessellationGlsl(
          primitive.type == PipelineGeometryShader::kQuadList,
          primitive.interpolator_count,
          primitive.user_clip_plane_cull ? 0 : primitive.user_clip_plane_count,
          (primitive.user_clip_plane_cull ? primitive.user_clip_plane_count : 0) +
              primitive.has_vertex_kill_and, primitive.has_point_coordinates);
      const std::string sources[] = {source.control, source.evaluation};
      for (unsigned stage = 0; stage < 2; ++stage) {
        std::vector<uint32_t> spirv;
        if (!tessellation::CompilePrimitiveShader(stage == 1, sources[stage], spirv)) break;
        VkShaderModuleCreateInfo info = {};
        info.sType = VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO;
        info.codeSize = spirv.size() * sizeof(uint32_t);
        info.pCode = spirv.data();
        if (dfn().vkCreateShaderModule(vk, &info, nullptr, &it->second[stage]) != VK_SUCCESS) break;
      }
      REXLOG_WARN("[native] Apple primitive expansion: type {}, interpolators {}, control={}, evaluation={}",
                  uint32_t(primitive.type), uint32_t(primitive.interpolator_count),
                  it->second[0] != VK_NULL_HANDLE, it->second[1] != VK_NULL_HANDLE);
    }
    job.tess_control = it->second[0];
    job.primitive_evaluation = it->second[1];
    job.patch_points = primitive.type == PipelineGeometryShader::kQuadList ? 4 : 3;
    if (!job.tess_control || !job.primitive_evaluation) {
      pipelines.emplace(hash, VK_NULL_HANDLE);
      return VK_NULL_HANDLE;
    }
  }
  if (key.tessellation) {
    const auto type''')
    text = replace_once(text, '  if (geometry) {\n    stages[stage_count++]', '''  if (job.primitive_evaluation) {
    stages[stage_count++] = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0,
                             VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT, job.tess_control, "main", nullptr};
    stages[stage_count++] = {VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO, nullptr, 0,
                             VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT, job.primitive_evaluation, "main", nullptr};
  }
  if (geometry) {
    stages[stage_count++]''')
    return replace_once(text, '  info.pTessellationState = job.tess_vertex ? &tessellation_state : nullptr;',
                        '  info.pTessellationState = job.tess_control ? &tessellation_state : nullptr;')
