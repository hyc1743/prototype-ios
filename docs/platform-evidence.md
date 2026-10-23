# Prototype platform address evidence

This report concerns only `switch/prototype-nx/prototype.nro`, SHA-256
`16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`.
Run `python tools/analyze_platform.py` from the repository root to reproduce the
byte checks, bounded Capstone disassembly and `work/analysis/platform/platform.json`.
The script refuses any other NRO hash. These are offline observations and inferred
symbol labels; no execution or REA MCP evidence is claimed.

| Inferred symbol | NRO offset | Evidence |
|---|---:|---|
| `__libnx_init` | `0x2d07fb0` | Entry `BL` at `0xc0`; seven setup calls and constructor runner tail call |
| `main` | `0x58510` | Entry `B` at `0xdc`, after loading argc/argv |
| `__stack_top` | `0x4022418` | Entry save at `0xac..0xb0`, restore at `0xe0..0xe8` |
| `appInit` | `0x2d07e70` | Last setup call at `0x2d07fd0`; service initialization body |
| `initHeap` | `0x2d07da0` | OverrideHeap path; otherwise SetHeapSize SVC wrapper |
| `__libc_init_array` | `0x2da44c0` | `__libnx_init` tail call at `0x2d07fd8` |
| `findDevice` | `0x2dbb6e0` | Colon lookup, scan of 35 device pointers, prefix comparison |
| `devoptab_list` | `0x3d9dfb0` | `findDevice` GOT load at `0x2dbb70c..0x2dbb710`, GOT `0x3ca1270` |
| default device integer | `0x3d9e0c8` | Colon-free return path at `0x2dbb784` |
| libnx reent getter | `0x2d08020` | ThreadVars magic check and TLS reent pointer load |

`appInit` starts with `fd7bafa9fd030091faefff97c0050035`; `findDevice`
starts with `fd7bbca941078052fd030091f76303a9`. Both entry addresses are
FDE-backed in `work/analysis/boundaries.json`: appInit size `0xec`, findDevice
size `0xb8`; `__libnx_init` has size `0x2c`. The analysis script validates the
boundary document's NRO hash and `.eh_frame` hash before retaining FDE membership.

Biko3's entry/setup sequence supplies a comparison for the labels, but offsets
and bytes differ. In particular Prototype's findDevice has a shorter body and
does not contain Biko3's extra drive-prefix handling. Reuse the host ABI design,
not Biko3's hook addresses.

The device interface's `deviceData` field is at `0xb0`; `close_reent.asm` shows
it copied to `_reent + 0x238` before the callback. `fsdev_devoptab` is at
`0x3c67cf8`; its function pointers at offsets `0x38` and `0x40` target fstat
`0x2d09520` and stat `0x2d096b0`. The fstat body clears exactly 104 bytes using
three 32-byte stores and an eight-byte store at offset 96, then stores file size
at offset 16, mode at 4, nlink at 8, and timestamp seconds at 24, 40 and 56.
Newlib's fseek code independently passes `sp + 0x58` and reads size from
`sp + 0x68`. This supports the existing Biko3 GuestStat structure. The semantic
identity of `st_blksize` at 72 is inherited from that ABI; no named access to it
was established here. Libnx's original fstat implementation leaves it zero.

ThreadVars initialization at `0x2d08910` uses `TPIDRRO_EL0 + 0x1e0`, magic
`0x21545624`, reent pointer at ThreadVars offset `0x10` (TLS `0x1f0`), and
static TLS pointer at offset `0x18` (TLS `0x1f8`). The guest TLS base must be
provided by rewritten register reads on Apple; the guest cannot use Apple's TLS
layout directly. GOT pointers identify `_impure_ptr` at `0x3d9a5d0` and static
TLS start at `0x40257c0`. No full newlib revision or struct `_reent` equivalence
with Biko3 is asserted.

The SDK's Memory::Initialize candidate at `0x227dc0` matches the checked-in
ReXGlue source's arena setup: the bounded `sdk_guest_memory_setup.asm` shows a
mapping size calculation based on `0x120000000` plus allocation granularity,
CreateFileMappingHandle candidate `0xe3f30`, GetFileMappingBase candidate
`0xe1ff0`, and MapViews candidate `0x21e4f0`. The physical base is set to arena
base plus `0x100000000`. These are guest arena offsets, not evidence that an
absolute host mapping must start at `0x100000000`.

`references/masseffect-nx/sdk/src/core/guest_memory_switch.cpp` explains the
mirror design: heap-backed chunks are code-aliased and mapped into virtual and
physical views through `svcMapProcessMemory`; physical aliases include
`0xa0000000`, `0xc0000000`, and `0xe0000000`, with a 4 KiB offset on the latter.
This source is a design reference and has not been proven byte-for-byte equal
to the linked SDK. A host bridge must preserve aliasing semantics; independent
zero-filled buffers for mirror views would not be equivalent. This investigation
does not validate those mappings on iOS or prove every required SVC is supported.
