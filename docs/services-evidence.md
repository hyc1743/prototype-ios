# Prototype libnx services evidence

`tools/analyze_services.py` reproduces `work/analysis/services.json` from the
supplied NRO and its independently recovered `.eh_frame` boundaries. The report
contains 63 service functions with exact offsets, first 16 bytes, FDE extents,
body hashes, complete bounded disassembly and direct callers. The source digest
is `16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`.

Six string references bind the source-level identification to the actual NRO:
WindowSwitch::OpenImpl at `0x12d040`, SwitchAudioSystem::StartOutput at
`0x19b280`, SwitchInputDriver::Setup at `0x1ac460`, and the three memory adapter
functions AllocFixed at `0xe2010`, MapFileView at `0xe3810` and
CreateFileMappingHandle at `0xe3f30`. Their unique diagnostics
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

The extended report includes HID vibration initialization at `0x2d02630`,
vibration send at `0x2d02a50` (IPC command 206), and appletUnhook at `0x2cfc2d0`.
Vibration send uses four-byte device handles and 16-byte amplitude/frequency
values. These require hosting if the underlying Horizon HID session is absent,
including calls that merely send zero amplitudes.

The original SDK executable-path function at `0xcf100` reads guest
`__system_argc` at `0x401fb48` and `__system_argv` at `0x401fb40`; the folder
function at `0xcf560` takes its parent path. Neither directly calls getcwd.
Supplying arguments only to main leaves these independent globals unchanged.
libnx argvSetup at `0x2d07700` runs before C++ constructors and fills them from
the optional command-line pointer at `0x401fba8`. Homebrew configuration entry
type 5 uses Value[1] for NUL-terminated command-line text; it does not take a
char** array. Directly replacing argvSetup can instead populate the two globals
with persistent host storage before constructors.

The memory evidence establishes RexGmInit `0xdeee0`, Shutdown `0xdf000`,
Base `0xdf1d0`, Size `0xdf1e0`, WindowToOffset `0xdf1f0`, AddView `0xdf450`,
Commit `0xdf640`, FaultIn `0xdfca0`, MapCommittedRange `0xdff00`,
Protect `0xe0220`, QueryProtect `0xe0f40`, ShadowFor `0xe1410` and
SetReadOnlyMapped `0xe1560`, with diagnostic counters in the report.
MapCommittedRange and read-only logical bookkeeping are additions documented
by the supplied Prototype SDK patch. Decommit and ProtectionMode have no
established standalone address here; possible removal by linking remains an
inference.

The actual nine-row Xbox mapping table starts at `0x2f3f5b0`. Its E0 physical
view maps guest `0xe0000000..0xffffffff` to backing offset `0x100001000`, while
A0, C0 and raw physical aliases use `0x100000000`. This 4 KiB skew matters on an
iOS device with 16 KiB VM pages. The supplied SDK source masks mapping offsets
by allocation granularity and has corresponding physical-address adjustment;
its applicability to this precompiled guest requires further verification.
