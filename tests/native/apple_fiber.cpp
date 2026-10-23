// Exercise the exact iOS cooperative fiber backend on an ARM64 macOS host.
// This checks native ABI behavior; it is not an iPhone execution test.
#include <rex/thread/fiber.h>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <thread>

using rex::thread::Fiber;
static thread_local uint64_t marker;

struct State {
  Fiber *root = nullptr, *child = nullptr;
  uint64_t base = 0;
  unsigned completed = 0;
  bool finished = false;
};

static void Roundtrip(Fiber *target, uint64_t seed) {
  uint64_t a = seed + 1, b = seed + 2, c = seed + 3, d = seed + 4;
  uint64_t e = seed + 5, f = seed + 6, g = seed + 7, h = seed + 8;
  uint64_t i = seed + 9, j = seed + 10;
  double u = double(seed & 65535) + 0.25, v = u + 1, w = u + 2, x = u + 3;
  double y = u + 4, z = u + 5, p = u + 6, q = u + 7;
  // Keep enough independent live integer/FP values across the real call to
  // exercise register preservation, without writing Apple's reserved registers.
  asm volatile("" : "+r"(a), "+r"(b), "+r"(c), "+r"(d), "+r"(e), "+r"(f),
                    "+r"(g), "+r"(h), "+r"(i), "+r"(j), "+w"(u), "+w"(v),
                    "+w"(w), "+w"(x), "+w"(y), "+w"(z), "+w"(p), "+w"(q));
  Fiber::SwitchTo(target);
  asm volatile("" : "+r"(a), "+r"(b), "+r"(c), "+r"(d), "+r"(e), "+r"(f),
                    "+r"(g), "+r"(h), "+r"(i), "+r"(j), "+w"(u), "+w"(v),
                    "+w"(w), "+w"(x), "+w"(y), "+w"(z), "+w"(p), "+w"(q));
  assert(a == seed + 1 && b == seed + 2 && c == seed + 3 && d == seed + 4);
  assert(e == seed + 5 && f == seed + 6 && g == seed + 7 && h == seed + 8);
  assert(i == seed + 9 && j == seed + 10);
  const double expected = double(seed & 65535) + 0.25;
  assert(u == expected && v == expected + 1 && w == expected + 2 && x == expected + 3);
  assert(y == expected + 4 && z == expected + 5 && p == expected + 6 && q == expected + 7);
  uintptr_t sp;
  asm volatile("mov %0, sp" : "=r"(sp));
  assert((sp & 15) == 0);
}

static void Child(void *opaque) {
  auto &state = *static_cast<State *>(opaque);
  for (unsigned n = 0; n < 1000; ++n) {
    assert(Fiber::Current() == state.child);
    assert(marker == state.base + n * 8 + 3);
    marker += 5;
    Roundtrip(state.root, state.base + n + 0x10000000);
    assert(Fiber::Current() == state.child);
    ++state.completed;
  }
  state.finished = true;
  for (;;) Fiber::SwitchTo(state.root); // Never return through the trampoline.
}

static void Worker(unsigned number) {
  State state;
  state.base = uint64_t(number + 1) * 100000;
  marker = state.base;
  assert(Fiber::Current() == nullptr);
  state.root = Fiber::ConvertCurrentThread();
  state.child = Fiber::Create(256 * 1024, Child, &state);
  assert(state.root && state.child && Fiber::Current() == state.root);
  for (unsigned n = 0; n < 1000; ++n) {
    marker += 3;
    Roundtrip(state.child, state.base + n);
    assert(Fiber::Current() == state.root);
    assert(marker == state.base + (n + 1) * 8);
  }
  Roundtrip(state.child, state.base + 1000);
  assert(state.finished && state.completed == 1000);
  state.child->Destroy();
  assert(Fiber::Current() == state.root);
  state.root->Destroy();
  assert(Fiber::Current() == nullptr);
}

int main() {
  std::thread first(Worker, 0), second(Worker, 1);
  first.join(); second.join();
  std::puts("APPLE_FIBER_PASS live_integer_and_fp_values");
  std::puts("APPLE_FIBER_PASS stack_alignment");
  std::puts("APPLE_FIBER_PASS thread_local_isolation");
  std::puts("APPLE_FIBER_PASS current_fiber_and_lifetime");
  std::puts("APPLE_FIBER_ALL_PASSED");
}
