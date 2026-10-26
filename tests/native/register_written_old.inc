// Upstream 292de4b native_renderer.h, GPL-3.0-or-later.
  void RegisterWritten(uint32_t index, uint32_t old_value, uint32_t new_value) {
    if (index >= 0x4000) {
      if (index < 0x4400) {
        ++vs_float_generation_;
      } else if (index < 0x4800) {
        ++ps_float_generation_;
      } else if (index < 0x48C0) {
        ++fetch_generation_;
        uint32_t slot_bit = 1u << ((index - 0x4800) / 6);
        texture_fetch_dirty_ |= slot_bit;
        vertex_fetch_dirty_ |= slot_bit;
      } else if (index < 0x4928) {
        ++bool_loop_generation_;
      } else {
        ++state_generation_;
      }
      return;
    }
    // Vertex index range and offset: system constants only.
    if (index >= 0x2100 && index <= 0x2102) {
      ++index_generation_;
      return;
    }
    // Registers written for every draw, or only read by resolves and the
    // ring, do not invalidate the render state the draws cache.
    if (index == 0x2103 || (index >= 0x21F9 && index <= 0x21FC) ||
        (index >= 0x2318 && index <= 0x231F) || index == 0x2324 || index == 0x2325 ||
        index == 0x2293 || (index >= 0x0578 && index <= 0x057F) || index == 0x01DC ||
        index == 0x01DD || (index >= 0x0A2F && index <= 0x0A31) || index == 0x0C44 ||
        index == 0x0C45 || index == 0x01C5) {
      return;
    }
    ++state_generation_;
    ++state_change_counts_[index];
    // The render state is the context block; its hash is kept up to date one
    // register at a time (each register contributes Mix(index, value), and
    // an XOR swaps the old contribution for the new one).
    if (index >= 0x2000 && index < 0x2400) {
      state_hash_ ^= Mix(index, old_value) ^ Mix(index, new_value);
    }
  }
