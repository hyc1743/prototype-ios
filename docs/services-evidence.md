# Prototype libnx services evidence

`tools/analyze_services.py` reproduces `work/analysis/services.json` from the
supplied NRO and its independently recovered `.eh_frame` boundaries. The report
contains 38 service functions with exact offsets, first 16 bytes, FDE extents,
body hashes, complete bounded disassembly and direct callers. The source digest
is `16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`.

Three string references bind the source-level identification to the actual NRO:
WindowSwitch::OpenImpl at `0x12d040`, SwitchAudioSystem::StartOutput at
`0x19b280`, and SwitchInputDriver::Setup at `0x1ac460`. Their unique diagnostics
and adjacent call order match the Switch SDK sources under
`references/masseffect-nx/sdk/src/`. Source code is used to interpret binary
observations; symbol names are inferred, since this guest has no corresponding
debug symbols.

| Function | Guest offset | FDE bytes |
|---|---:|---:|
| appletMainLoop | `0x2cfe470` | 52 |
| nwindowGetDefault | `0x2d061f0` | 12 |
| nwindowIsValid | `0x2d068c0` | 36 |
| nwindowGetDimensions | `0x2d06ab0` | 144 |
| nwindowSetDimensions | `0x2d06b40` | 132 |
| hidInitialize | `0x2d01b10` | 620 |
| padConfigureInput | `0x2d08aa0` | 156 |
| padInitializeWithMask | `0x2d08b40` | 36 |
| padUpdate | `0x2d08b70` | 668 |
| audoutInitialize | `0x2cfe9c0` | 480 |
| audoutAppendAudioOutBuffer | `0x2cfebc0` | 456 |
| audoutGetReleasedAudioOutBuffer | `0x2cfed90` | 396 |
| audoutWaitPlayFinish | `0x2cfef20` | 120 |

`appletMainLoop`, audout start and audout stop have byte-identical complete
function bodies in the supplied Biko3 reference. Nine additional service bodies
match after numeric instruction immediates are removed while preserving
mnemonics and registers. This comparison supports the source correlation and
does not establish exact historical libnx source identity.

The public switchbrew/libnx source at commit
`b156b4774fb2df7b93402169509266da7547fc4c` supplies an additional ABI cross-check
for `nx/include/switch/runtime/pad.h`, `nx/source/runtime/pad.c`,
`nx/include/switch/display/native_window.h` and
`nx/source/display/native_window.c`. The NRO instruction operands independently
confirm the field offsets below.

`PadState` is 56 bytes. Source masks and active masks occupy bytes 0 and 1;
handheld selection and active flags occupy bytes 2 and 3. Style and connection
attributes are 32-bit fields at 4 and 8. Current and previous button masks are
64-bit fields at 16 and 24. Four signed 32-bit stick axes occupy offsets 32, 36,
40 and 44. GameCube trigger values occupy 48 and 52. The padUpdate prologue
preserves the previous current mask at 24, resets the new current mask at 16,
then obtains style-specific HID state. Host input can therefore replace
padUpdate while preserving the SDK's original button mapping and edge detection.
The first pad supports No1 and Handheld; other pads retain their selected masks.

`NWindow` is 128 bytes, with validity magic `0x6e69574e` at 0, configured-slot
mask at 48, current dimensions at 68 and 72, crop at 84, and default dimensions
at 112 and 116. nwindowGetDefault returns guest address `0x401fa58`.
nwindowSetDimensions validates the window, rejects dimensions changed after
slots have been configured, stores the new dimensions and clears crop.

`AudioOutBuffer` is 40 bytes: next pointer at 0, PCM pointer at 8, allocation
bytes at 16, data bytes at 24, data offset at 32. The SDK StartOutput binary
checks 48,000 Hz, two channels and PCM format enum 2. It allocates and queues
three 4,096-byte buffers, each containing 1,024 interleaved stereo Int16 frames.
The host must use each buffer's data size and offset, rather than Biko3's
different fixed buffer length. WaitPlayFinish clears the event, drains released
buffers, waits with the passed timeout when empty, then queries released buffers
again. Its host replacement must preserve completion timing and output pointers.

Applet lifecycle and event access remain relevant even when appletMainLoop is
hosted: the SDK initializes focus handling and an applet hook and passes message
and optional resolution-change events to its UI wait loop. The report includes
their offsets so the host can supply valid lifecycle state and waitable events.

This is offline shell/Capstone evidence. The REA session reported an unconfigured
Ghidra provider; no REA decompilation Evidence was produced. No guest execution,
iOS input delivery, audio playback or gameplay verification is claimed.
