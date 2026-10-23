// SPDX-License-Identifier: GPL-3.0-or-later
// Expand guest rectangles and quads on devices without geometry shaders.
#pragma once
#include <string>

namespace p2::native {
struct PrimitiveTessellationSource { std::string control, evaluation; };

inline PrimitiveTessellationSource PrimitiveTessellationGlsl(
    bool quad, unsigned interpolators, unsigned clip, unsigned cull,
    bool point_coordinates) {
  PrimitiveTessellationSource source;
  const unsigned points = quad ? 4 : 3;
  auto block = [&](const std::string& direction, const std::string& instance) {
    std::string text = direction + " gl_PerVertex { vec4 gl_Position;";
    if (clip) text += "float gl_ClipDistance[" + std::to_string(clip) + "];";
    if (cull) text += "float gl_CullDistance[" + std::to_string(cull) + "];";
    return text + "} " + instance + ";\n";
  };
  source.control = "#version 450\nlayout(vertices=" + std::to_string(points) + ") out;\n";
  source.control += block("in", "gl_in[]") + block("out", "gl_out[]");
  source.evaluation = "#version 450\nlayout(quads,equal_spacing,ccw) in;\n";
  source.evaluation += block("in", "gl_in[]") + block("out", "");
  for (unsigned i = 0; i < interpolators; ++i) {
    const std::string n = std::to_string(i);
    source.control += "layout(location=" + n + ") in vec4 a" + n + "[];\n";
    source.control += "layout(location=" + n + ") out vec4 b" + n + "[];\n";
    source.evaluation += "layout(location=" + n + ") in vec4 b" + n + "[];\n";
    source.evaluation += "layout(location=" + n + ") out vec4 v" + n + ";\n";
  }
  if (point_coordinates)
    source.evaluation += "layout(location=" + std::to_string(interpolators) + ") out vec2 point_uv;\n";
  source.control += "void main(){\n gl_out[gl_InvocationID].gl_Position=gl_in[gl_InvocationID].gl_Position;\n";
  for (unsigned i = 0; i < interpolators; ++i) {
    const std::string n = std::to_string(i);
    source.control += "b" + n + "[gl_InvocationID]=a" + n + "[gl_InvocationID];\n";
  }
  for (const auto& member : {std::string("gl_ClipDistance"), std::string("gl_CullDistance")}) {
    unsigned count = member == "gl_ClipDistance" ? clip : cull;
    for (unsigned i = 0; i < count; ++i) {
      std::string n = "[" + std::to_string(i) + "]";
      source.control += "gl_out[gl_InvocationID]." + member + n + "=gl_in[gl_InvocationID]." + member + n + ";\n";
    }
  }
  source.control += "if(gl_InvocationID==0){gl_TessLevelOuter[0]=1;gl_TessLevelOuter[1]=1;"
                    "gl_TessLevelOuter[2]=1;gl_TessLevelOuter[3]=1;"
                    "gl_TessLevelInner[0]=1;gl_TessLevelInner[1]=1;}\n}\n";
  source.evaluation += "void main(){\n";
  if (quad) {
    // Match the original geometry shader's strip 0,1,3,2. Piecewise affine
    // weights preserve its diagonal and non-planar interpolators exactly.
    source.evaluation += "vec2 uv=gl_TessCoord.xy;vec4 w;"
                         "if(uv.x+uv.y<=1) w=vec4(1-uv.x-uv.y,uv.x,0,uv.y);"
                         "else w=vec4(0,1-uv.y,uv.x+uv.y-1,1-uv.x);\n";
  } else {
    // The longest edge is the diagonal; reconstruct the missing fourth corner
    // as v1 + v2 - v0, as in native_geometry_shaders.cpp.
    source.evaluation += "vec2 e0=gl_in[1].gl_Position.xy-gl_in[2].gl_Position.xy;"
                         "vec2 e1=gl_in[2].gl_Position.xy-gl_in[0].gl_Position.xy;"
                         "vec2 e2=gl_in[0].gl_Position.xy-gl_in[1].gl_Position.xy;"
                         "vec3 d=vec3(dot(e0,e0),dot(e1,e1),dot(e2,e2));"
                         "int a=(d.x>d.y && d.x>d.z)?0:((d.y>d.z)?1:2);"
                         "int b=(a+1)%3,c=(a+2)%3;vec3 w=vec3(0);"
                         "w[a]=1-gl_TessCoord.x-gl_TessCoord.y;"
                         "w[b]=gl_TessCoord.x;w[c]=gl_TessCoord.y;\n";
  }
  auto weighted = [&](const std::string& prefix, const std::string& suffix) {
    std::string result;
    for (unsigned i = 0; i < points; ++i) {
      if (i) result += "+";
      const std::string n = std::to_string(i);
      result += prefix + "[" + n + "]" + suffix + "*w[" + n + "]";
    }
    return result;
  };
  source.evaluation += "gl_Position=" + weighted("gl_in", ".gl_Position") + ";\n";
  // Any NaN vertex kills the original primitive, not just its own corner.
  for (unsigned i = 0; i < points; ++i)
    source.evaluation += "if(any(isnan(gl_in[" + std::to_string(i) + "].gl_Position))) gl_Position=vec4(uintBitsToFloat(0x7fc00000u));\n";
  for (unsigned i = 0; i < interpolators; ++i) {
    const std::string n = std::to_string(i);
    source.evaluation += "v" + n + "=" + weighted("b" + n, "") + ";\n";
  }
  for (unsigned i = 0; i < clip; ++i) {
    const std::string n = "[" + std::to_string(i) + "]";
    source.evaluation += "gl_ClipDistance" + n + "=" + weighted("gl_in", ".gl_ClipDistance" + n) + ";\n";
  }
  // Cull only when every input is outside the same plane (vertex-kill AND
  // uses the final cull-distance slot too).
  for (unsigned i = 0; i < cull; ++i) {
    const std::string n = "[" + std::to_string(i) + "]";
    std::string outside;
    for (unsigned j = 0; j < points; ++j) {
      if (j) outside += " && ";
      outside += "gl_in[" + std::to_string(j) + "].gl_CullDistance" + n + "<0";
    }
    source.evaluation += "gl_CullDistance" + n + "=(" + outside + ")?-1:1;\n";
  }
  if (point_coordinates) source.evaluation += "point_uv=vec2(0);\n";
  source.evaluation += "}\n";
  return source;
}
}  // namespace p2::native
