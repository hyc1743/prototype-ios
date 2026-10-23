# Prototype Xbox memory on Apple arm64

The recommended production route is to regenerate the original Prototype PPC
translation and compile it, the original native renderer, and the ReXGlue runtime
for Apple. Keep the original NRO host as an independent startup diagnostic. A
binary-preserving bridge is possible in principle, but a working allocator and
successful startup do not solve its 4 KiB mirror phase, fault handling, GPU
coherence, or atomic operations.

`python tools/memory_analysis.py` reproduces the evidence in
`work/analysis/memory/memory.json` and the bounded disassembly files. The input
must have SHA-256
`16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`.
The complete nine-row `map_info` table in the checked-in SDK's
`src/system/xmemory.cpp` matches the original NRO at `0x2f3f5b0`. This proves
the table correspondence; it does not establish complete source/binary identity.
No Apple mapping or game execution was performed by this analysis tool.

The table below uses offsets relative to the arena base. The last column is a
canonical backing offset, not an absolute host virtual address.

| Arena view | Inclusive end | Canonical backing start |
|---:|---:|---:|
| `0x00000000` | `0x3fffffff` | `0x000000000` |
| `0x40000000` | `0x7effffff` | `0x040000000` |
| `0x7f000000` | `0x7fffffff` | `0x100000000` |
| `0x80000000` | `0x8fffffff` | `0x080000000` |
| `0x90000000` | `0x9fffffff` | `0x080000000` |
| `0xa0000000` | `0xbfffffff` | `0x100000000` |
| `0xc0000000` | `0xdfffffff` | `0x100000000` |
| `0xe0000000` | `0xffffffff` | `0x100001000` |
| `0x100000000` | `0x11fffffff` | `0x100000000` |

For example, `A0001000`, `C0001000`, `E0000000`, `100001000` and `7F001000`
must observe the same bytes immediately after a write through any of those
views. Separate zero-filled allocations or copies refreshed only at API calls
do not satisfy this invariant. Reserve sparse address space for the arena and
its alignment padding; do not allocate 4.5 GiB of resident RAM just to reserve
the addresses. Commitment, logical permissions, and backing lifetime are
separate state. A failed mapping must leave the previous mappings usable and
must report failure.

For a page-aligned arena and 16 KiB host pages, every view except E0 has the
same page phase as its backing. E0 requires a 4 KiB phase difference. Rounding
its mapping offset from `0x100001000` to `0x100000000` makes `E0000000` alias
`A0000000` instead of `A0001000`: a concrete corruption, not an alignment fix.
One E0 host page would need bytes from two distinct 16 KiB physical pages.
Changing the global arena alignment cannot simultaneously satisfy that phase
and the A0/C0/raw views.

Apple's XNU defines `VM_FLAGS_RETURN_4K_DATA_ADDR`, but this flag does not
provide arbitrary 4 KiB hardware aliasing. In the examined implementation it
retains an offset inside the mapped host page and returns a correspondingly
shifted data pointer. That still requires callers to use the shifted pointer.
The pinned primary source snapshot is retained under
`work/analysis/memory/apple-xnu/`, with URLs and hashes in `sources.json`, from
`apple-oss-distributions/xnu` commit
`f6217f891ac0bb64f3d375211650a4c1ff8ca1ea`. Relevant locations are
`osfmk/mach/vm_statistics.h:293` and `osfmk/vm/vm_map.c:4705` / `19903`.
This is source evidence, not proof of availability or behavior on the target
iOS device; a native page-size and alias test remains required.

The SDK already addresses this in a source build. `PhysicalHostOffset` in
`include/rex/system/xmemory.h` adds `0x1000` for E0 guest addresses on Apple
arm64. `Memory::MapViewsMac` maps the aligned backing, and
`PhysicalHeap::Initialize` sets the matching `host_address_offset`. Generated
loads/stores, `GuestPtr`, runtime `TranslateVirtual`, heap operations, and reverse
host-to-guest translation must all agree. Updating only the runtime changes
neither the Switch NRO's compiled pointer arithmetic nor its generated accesses.
The valid E0 heap is also smaller than the full E0 table view
(`0x1fd00000` bytes in this SDK); test the guard/end behavior rather than treating
every address in the table as an allocated guest address.

The required permission unit is the logical 4 KiB guest page, even when the
host protects 16 KiB at once. If one quarter of a host page is watched or
inaccessible, the other quarters retain their guest permissions. A conservative
host protection may cause extra faults, but those faults must be decoded and
handled according to the exact touched guest pages. Unprotecting all four
quarters on any write silently disables neighboring GPU watches. Cross-page
scalar, pair, and vector accesses must check every touched page and backing span.
Read-only writes must trigger the original guest callback semantics before
modifying backing; ordinary reads must not masquerade as writes. Reuse and verify
the source SDK's `SyncHostPageAccess`, MMIO, and write-watch reconciliation rather
than replacing them with rounded `mprotect` calls.

There are two possible binary bridge boundaries. Hosting the Horizon mapping
SVCs preserves the guest's C++ RexGm state but needs genuine shared aliases for
`MapProcessCodeMemory` / `MapProcessMemory`, borrowed-source lifetime, unmapping,
permissions, and E0 fault emulation. Replacing all RexGm C entry points with a
host arena manager avoids that mapping dance, but the replacement must implement
the complete shared state contract, including query, shadow lookup, commitment,
protection, accounting, and shutdown. A mixture of independent host state and
guest state is not sound. The current unknown-SVC success return establishes
none of these behaviors and cannot support a claim of playability.

The exact NRO contains useful exception bridge anchors:

| Inferred entry | NRO offset | Evidence |
|---|---:|---|
| `HandleSwitchException` | `0xdd250` | FDE, 1020 bytes; original SDK callback dispatch |
| `__libnx_exception_handler` | `0xddb40` | FDE, 12 bytes; calls `0xdd250` |
| `EmulateViaShadow` | `0xdc5e0` | FDE; original scalar/pair/SIMD decoder |
| `RexResumeFromException` | `0xdbb98` | Verified 160-byte assembly window, no FDE |
| resume trap | `0xdbc34` | Unique original `UDF #0x5245` |
| `RexGmFaultIn` | `0xdfca0` | FDE, 608 bytes; fault handler call sites |
| `RexGmQueryProtect` | `0xe0f40` | FDE, 1220 bytes; permission-query call sites |
| `RexGmShadowFor` | `0xe1410` | FDE, 336 bytes; shadow decoder call sites |

In particular `0xdff00` is a two-argument window-range operation, not the
one-argument FaultIn function. Its exact semantic label remains unclassified.
Do not use a guessed name to select a hook ABI.

A diagnostic binary bridge could translate a Darwin fault context into the
original 0x340-byte ThreadExceptionDump, call the original handler, and intercept
its resume entry to capture the corrected dump. Return through Darwin's context
restoration, preserving all integer/SIMD registers, flags, FPCR/FPSR, and the
reserved Apple register; do not execute Horizon's resume trap/SVC on Apple. E0
would remain inaccessible and resolve through canonical backing. When an SDK
callback requests a retry at unchanged PC, a software-only E0 page must emulate
the access before resuming, or it faults forever. The source decoder explicitly
omits exclusives/atomics and vector register-list transfers, and assumes contiguous
shadow spans. It is not a complete all-E0 access engine.

Eleven FDE-backed GCC atomic helpers offer a smaller boundary for some missing
operations: CAS at `0x2a89340/370/3a0/3d0/400`, exchange at
`0x2a89430/460/490`, and fetch-add at `0x2a894c0/4f0/520`. Their exact bytes,
widths, and acquire/release behavior are recorded in the analysis JSON. A host
implementation could canonicalize an arena pointer, resolve guest watches, and
perform the matching hardware atomic on the shared backing. That preserves the
existing NRO helper's observed compare/exchange behavior. It does not prove that
every atomic access goes through those helpers. Emulating one `LDXR` in a signal
handler and later replaying `STXR` in guest code is not a valid general solution.

CPU fault emulation also does not repair pointers consumed outside guest CPU
instructions. Kernel I/O using an inaccessible E0 pointer can fail rather than
raise a recoverable user fault. GPU/no-copy APIs can retain pointers or inspect
pages without executing the instrumented guest instructions. Every such boundary
must use canonical backing with correct offset, range, alignment and lifetime,
or an explicit staging copy with synchronization and writeback. A transient
contiguous copy cannot be passed as if it were a persistent coherent alias.

The source-native path keeps the original game behavior and avoids exhaustive
binary pointer rewriting, but still needs concrete Apple platform work:

1. Regenerate `generated/default` from the supplied original `default.xex` and
   the reviewed manifest/overrides. Compile that generated code together with
   Prototype's original app and native renderer. Pin the SDK revision and its
   Prototype compatibility patch. Preserve hashes of inputs, source revisions,
   generated outputs and applied patches. The checkout originally omitted the
   generated sources; acquiring codegen output is a prerequisite, not proof of a
   successful native build.
2. Add an iOS platform selection before the broad Apple/macOS selection. The
   current SDK CMake treats all `APPLE` targets as macOS. Use actual host page
   size and retain the Apple E0 adjustment in every translation path. Establish
   the mirror/permission tests on native arm64 macOS, then the same tests in an
   iPhoneOS build and on a device.
3. Replace the POSIX fiber backend for iOS. It calls `getcontext`, `makecontext`
   and `swapcontext`; enabling `_XOPEN_SOURCE` is only a macOS header workaround
   and does not make unavailable iPhoneOS APIs available. A cooperative arm64
   backend can adapt the existing Switch fiber context layout (callee-saved
   x19-x30, SP, d8-d15) to Mach-O assembly. Keep x18 and system TLS registers
   untouched, retain per-thread `tls_current_`, align stacks, and supply proper
   unwind metadata. Verify target-SDK availability and linking explicitly. Fiber
   switching and full asynchronous exception restoration require different saved
   contexts; do not reuse the small cooperative context for a signal.
4. Preserve original MMIO/write-watch logic with an Apple fault backend. The
   existing POSIX context adapter is a starting point, not demonstrated iOS
   support. Verify signal/context APIs against the chosen iPhoneOS SDK,
   neighboring 4 KiB permissions, SIMD state, simultaneous faults, and progress
   after callback retries. Any unsupported fault must remain a visible failure.
5. Integrate SDL3's UIKit entry/lifecycle and Metal view with one owner of the
   application main loop. The SDK's current `windowed_app_main_sdl.cpp` defines
   a desktop main; it does not implement the resource importer or iOS controller
   UI. Keep resource import before game launch, resolve Documents paths, and
   connect pause/resume, audio and display lifecycle. The existing SDL Metal
   surface adapter is useful, but a macOS window is not device validation.
6. Use a pinned iOS arm64 MoltenVK library and real portability enumeration/
   subset behavior. The SDK contains configurable geometry-shader/non-solid-fill
   requirements and an existing MoltenVK path; select the native renderer's
   actual feature set and implement required fallbacks. Never advertise an
   unsupported Vulkan feature to get past initialization. Verify shader creation,
   depth/stencil, blend, synchronization, textures and frame presentation with
   actual game resources.
7. Link the runtime, generated translation, renderer, required GPU registration,
   SDL3 and dependencies into the actual application. Adapt desktop Vulkan/SPIRV
   runtime discovery: `vulkan_moltenvk.cpp` searches desktop dylib/SDK locations,
   which is not a static iOS runtime strategy. Bind the linked Vulkan entry
   points or bundle permitted signed dependencies explicitly. Confirm dead-strip
   behavior does not remove game/GPU registration. Do not carry Switch startup,
   libnx/NSO/NRO link flags, or Horizon service calls into the native target.

Completion evidence must distinguish compilation, missing-resource main return,
memory alias/permission correctness, actual renderer/game startup, and iOS device
operation. A green IPA build or a process that returns after reporting missing
resources proves only its own scope. Actual resource import, game initialization,
visible rendering, input, audio, save/load and resume remain required before the
port can be called playable.
