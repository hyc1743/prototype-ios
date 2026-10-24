// Diagnostic entry only; never linked into the iOS product.
#define SDL_MAIN_HANDLED 1
#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <cstdio>
#include <csignal>
#include <dlfcn.h>
#include <execinfo.h>
#include <sys/ucontext.h>
#include <unistd.h>
#include <rex/system/thread_state.h>

int prototype_game_main(int argc, char **argv);
extern "C" uint64_t PrototypeNativeProbeMaxDraws();

static void ProbeFault(int signal, siginfo_t *info, void *raw) {
  const auto* context = static_cast<ucontext_t*>(raw);
  const uintptr_t pc = context->uc_mcontext->__ss.__pc;
  Dl_info symbol{};
  dladdr(reinterpret_cast<void*>(pc), &symbol);
  dprintf(STDERR_FILENO, "[probe-fault] signal=%d address=%p pc=%p symbol=%s image=%p symbol_address=%p\n",
          signal, info->si_addr, reinterpret_cast<void*>(pc), symbol.dli_sname ? symbol.dli_sname : "?",
          symbol.dli_fbase, symbol.dli_saddr);
  if (auto* state = rex::runtime::ThreadState::Get()) {
    const auto* ppc = state->context();
    dprintf(STDERR_FILENO, "[probe-fault] guest lr=%08llx sp=%08x r3=%08x r4=%08x r5=%08x r11=%08x last_indirect=%08x\n",
            static_cast<unsigned long long>(ppc->lr), ppc->r1.u32, ppc->r3.u32, ppc->r4.u32,
            ppc->r5.u32, ppc->r11.u32, ppc->last_indirect_target);
  }
  void* stack[48];
  const int count = backtrace(stack, 48);
  backtrace_symbols_fd(stack, count, STDERR_FILENO);
  struct sigaction fatal{};
  fatal.sa_handler = SIG_DFL;
  sigemptyset(&fatal.sa_mask);
  sigaction(signal, &fatal, nullptr);
  raise(signal);
}

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
  struct sigaction fault{};
  fault.sa_sigaction = ProbeFault;
  fault.sa_flags = SA_SIGINFO;
  sigemptyset(&fault.sa_mask);
  sigaction(SIGSEGV, &fault, nullptr);
  sigaction(SIGBUS, &fault, nullptr);
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
