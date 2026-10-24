// Diagnostic entry only; never linked into the iOS product.
#define SDL_MAIN_HANDLED 1
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <cstdio>

int prototype_game_main(int argc, char **argv);
extern "C" uint64_t PrototypeNativeProbeMaxDraws();

struct ProbePad {
  SDL_Joystick *joystick = nullptr;
  Uint64 start = 0;
  int previous = -1;
  Uint64 pause_start = 0;
};

static void SDLCALL UpdatePad(void *userdata) {
  auto &pad = *static_cast<ProbePad *>(userdata);
  if (!pad.joystick) return;
  const Uint64 elapsed = SDL_GetTicks() - pad.start;
  const int phase = int(elapsed / 8000);
  // Alternate Start and confirm. These are ordinary SDL gamepad
  // events; the translated game and its input provider receive no patches.
  if (!pad.pause_start && elapsed >= 240000 && PrototypeNativeProbeMaxDraws() >= 1200)
    pad.pause_start = elapsed;
  const int pressed = pad.pause_start ? (elapsed-pad.pause_start < 250 ? SDL_GAMEPAD_BUTTON_START : -1)
      : phase > 0 && PrototypeNativeProbeMaxDraws() < 1000 && elapsed % 8000 < 250
      ? (phase % 2 ? SDL_GAMEPAD_BUTTON_START : SDL_GAMEPAD_BUTTON_SOUTH) : -1;
  if (pressed == pad.previous) return;
  for (int button = 0; button < 15; ++button)
    SDL_SetJoystickVirtualButton(pad.joystick, button, button == pressed);
  if (pressed >= 0)
    std::fprintf(stderr, "[prototype-mac-probe] input button=%d ms=%llu\n",
                 pressed, static_cast<unsigned long long>(elapsed));
  pad.previous = pressed;
}

int main(int argc, char **argv) {
  std::setvbuf(stdout, nullptr, _IOLBF, 0);
  std::setvbuf(stderr, nullptr, _IOLBF, 0);
  SDL_SetMainReady();
  if (!SDL_Init(SDL_INIT_GAMEPAD)) {
    std::fprintf(stderr, "[prototype-mac-probe] SDL input failed: %s\n", SDL_GetError());
    return 1;
  }
  ProbePad pad;
  SDL_VirtualJoystickDesc desc;
  SDL_INIT_INTERFACE(&desc);
  desc.type = SDL_JOYSTICK_TYPE_GAMEPAD;
  desc.vendor_id = 0x1209;
  desc.product_id = 0x5052;
  desc.naxes = 6;
  desc.nbuttons = 15;
  desc.axis_mask = (1u << 6) - 1;
  desc.button_mask = (1u << 15) - 1;
  desc.name = "Prototype macOS diagnostic input";
  desc.userdata = &pad;
  desc.Update = UpdatePad;
  const SDL_JoystickID id = SDL_AttachVirtualJoystick(&desc);
  pad.joystick = id ? SDL_OpenJoystick(id) : nullptr;
  if (!pad.joystick || !SDL_SetJoystickPlayerIndex(pad.joystick, 0)) {
    std::fprintf(stderr, "[prototype-mac-probe] virtual pad failed: %s\n", SDL_GetError());
    SDL_Quit();
    return 1;
  }
  for (int axis = 0; axis < 6; ++axis)
    SDL_SetJoystickVirtualAxis(pad.joystick, axis, axis >= 4 ? -32768 : 0);
  pad.start = SDL_GetTicks();
  std::fprintf(stderr, "[prototype-mac-probe] entering original source main\n");
  const int result = prototype_game_main(argc, argv);
  std::fprintf(stderr, "[prototype-mac-probe] original source main returned %d\n", result);
  SDL_CloseJoystick(pad.joystick);
  pad.joystick = nullptr;
  SDL_DetachVirtualJoystick(id);
  SDL_Quit();
  return result;
}
