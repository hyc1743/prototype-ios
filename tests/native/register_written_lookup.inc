// Upstream ea65b2e native_renderer.h, GPL-3.0-or-later.
  enum RegisterKind : uint8_t {
    kRegIgnored = 0,  // written every draw or only read by resolves: no cache depends on it
    kRegState,        // the render state the draws cache (and its rolling hash below 0x2400)
    kRegStateNoHash,  // state, outside the hashed context block
    kRegIndex,        // vertex index range and offset: system constants only
    kRegVsFloat,
    kRegPsFloat,
    kRegFetch,
    kRegBoolLoop,
  };
  static constexpr uint32_t kRegisterKindCount = 0x5000;
  struct RegisterKinds {
    uint8_t kind[kRegisterKindCount];
    RegisterKinds() {
      for (uint32_t i = 0; i < kRegisterKindCount; ++i) {
        uint8_t k;
      if (i >= 0x4000) {
        k = i < 0x4400 ? kRegVsFloat
             : i < 0x4800 ? kRegPsFloat
             : i < 0x48C0 ? kRegFetch
             : i < 0x4928 ? kRegBoolLoop
                          : kRegStateNoHash;
      } else if (i >= 0x2100 && i <= 0x2102) {
        k = kRegIndex;
      } else if (i == 0x2103 || (i >= 0x21F9 && i <= 0x21FC) || (i >= 0x2318 && i <= 0x231F) ||
                 i == 0x2324 || i == 0x2325 || i == 0x2293 || (i >= 0x0578 && i <= 0x057F) ||
                 i == 0x01DC || i == 0x01DD || (i >= 0x0A2F && i <= 0x0A31) || i == 0x0C44 ||
                 i == 0x0C45 || i == 0x01C5) {
        k = kRegIgnored;
      } else {
        k = (i >= 0x2000 && i < 0x2400) ? kRegState : kRegStateNoHash;
      }
        kind[i] = k;
      }
    }
  };

  void RegisterWritten(uint32_t index, uint32_t old_value, uint32_t new_value) {
    static const RegisterKinds kinds;
    const uint8_t kind = index < kRegisterKindCount ? kinds.kind[index] : uint8_t(kRegStateNoHash);
    switch (kind) {
      case kRegIgnored:
        return;
      case kRegState:
        ++state_generation_;
        ++state_change_counts_[index];
        // The render state is the context block; its hash is kept up to date one register at a
        // time (each register contributes Mix(index, value), and an XOR swaps old for new).
        state_hash_ ^= Mix(index, old_value) ^ Mix(index, new_value);
        return;
      case kRegStateNoHash:
        ++state_generation_;
        if (index < 0x4000) ++state_change_counts_[index];
        return;
      case kRegIndex:
        ++index_generation_;
        return;
      case kRegVsFloat:
        ++vs_float_generation_;
        return;
      case kRegPsFloat:
        ++ps_float_generation_;
        return;
      case kRegFetch: {
        ++fetch_generation_;
        const uint32_t slot_bit = 1u << ((index - 0x4800) / 6);
        texture_fetch_dirty_ |= slot_bit;
        vertex_fetch_dirty_ |= slot_bit;
        return;
      }
      case kRegBoolLoop:
        ++bool_loop_generation_;
        return;
    }
  }
