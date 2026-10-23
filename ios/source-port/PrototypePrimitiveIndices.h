// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>

namespace p2::native {
// Input count includes incomplete trailing primitives; those must be ignored.
inline uint32_t ExpandedTriangleIndexCount(uint32_t guest_count, uint32_t vertices_per_primitive) {
  return guest_count / vertices_per_primitive * 6u;
}

// The shader's four corner IDs match the SDK point/rectangle strip. Quads
// use the original guest indices in 0,1,3,2 strip order.
inline uint32_t ExpandedTriangleVertex(uint32_t output_index, bool quad) {
  constexpr uint32_t sprite[6] = {0, 1, 2, 2, 1, 3};
  constexpr uint32_t quadrilateral[6] = {0, 1, 3, 3, 1, 2};
  return output_index / 6u * 4u + (quad ? quadrilateral : sprite)[output_index % 6u];
}
}  // namespace p2::native
