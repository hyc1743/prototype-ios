// Prototype 1 collision traversal fix for Apple builds.
//
// sub_827A28A8 prepares an 8 KiB visited bitmap on the guest stack. The
// translated PPC dcbz sequence clears only 32 bytes every 128 bytes, which
// matches neither the Xenon cache-line operation nor the intended zeroed
// bitmap. Stale bits accumulate after movement and can make floor triangles
// disappear from later collision queries.
#include "generated/default/prototype_pch.h"

#include <atomic>
#include <cstring>

#include <rex/cvar.h>
#include <rex/logging.h>

REXCVAR_DEFINE_BOOL(proto_collision_bitmap_fix, true, "Prototype",
                    "Clear the complete 8 KiB collision traversal visited bitmap");

REX_EXTERN(__imp__sub_827A28A8);

extern "C" REX_FUNC(sub_827A28A8) {
  const uint32_t start = ctx.r3.u32 & ~uint32_t(127);
  __imp__sub_827A28A8(ctx, base);
  if (!REXCVAR_GET(proto_collision_bitmap_fix)) return;

  if (uint64_t(start) + 8192 <= 0x100000000ull) {
    std::memset(static_cast<void*>(REX_RAW_ADDR(start)), 0, 8192);
  } else {
    for (unsigned i = 0; i < 64; ++i) {
      const uint32_t address = start + i * 128u;
      std::memset(static_cast<void*>(REX_RAW_ADDR(address)), 0, 128);
    }
  }

  static std::atomic<bool> reported{false};
  if (!reported.exchange(true, std::memory_order_relaxed))
    REXLOG_INFO("[proto-collision-bitmap] complete 8 KiB clear enabled at {:08X}", start);
}
