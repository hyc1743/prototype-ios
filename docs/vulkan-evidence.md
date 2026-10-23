# Prototype Vulkan wrapper boundary

The pinned guest's 24,612-byte lookup function at `0x2913a80` has SHA-256
`5f4bb09824b984aab55de58653763eba33bbea9485b7e9ab99709a2d20e82a50`.
`tools/analyze_vulkan.py` decodes every strcmp comparison in this bounded FDE,
resolves its literal Vulkan name and return-block GOT entry, reads the original
function pointer, and checks that it identifies an executable FDE. It recovers
768 distinct names and distinct function targets, including the final CMP/CSEL
comparison and GOT loads with a zero displacement. No Biko3 function-body
similarity is assumed. Complete name/GOT/FDE evidence is retained under
`vulkan_analysis` in `ios/runtime/guest-hooks.json`.

The game/SDK executable prefix ends at the first export wrapper, `0x290efb4`.
Scanning its B/BL instructions finds one direct Vulkan export call:
vkGetInstanceProcAddr at `0x1677f8`. Exact adjacent ADRP/LDR address-taking
sequences additionally load vkGetInstanceProcAddr at `0x1677d0` and
vkDestroyInstance at `0x1677e8`. No stale register values are propagated across
unrelated instructions. ADR and adjacent ADRP/ADD direct addresses are checked
as well. Any unrecognized target inside the wrapper ranges stops generation.

The resulting Vulkan slots are:

| Slot | Symbol | Guest offset |
|---|---|---|
| 0 | vkGetInstanceProcAddr | `0x2919b38` |
| 1 | vkGetDeviceProcAddr | `0x2919aa4` |
| 2 | vkDestroyInstance | `0x290efb4` |

The 45 existing runtime hook records at slots 1024 and above are preserved.
The reviewed host export include retains its existing symbols and adds any
newly required static boundary names. vkCreateViSurfaceNN remains a special
host surface bridge and is excluded from directly linked Vulkan symbols.

The macOS smoke-run log for Actions run 39878248508 identifies a real host
MoltenVK instance passed into the guest wrapper at `0x290efb4`, which loads a
Mesa dispatch pointer at instance+0xd0 and crashes. The literal lookup proves
this wrapper is vkDestroyInstance; vkEnumeratePhysicalDevices instead starts at
`0x290efd4`. The SDK stores its static destroy pointer into its function table
at `0x1677ec..0x1677f4`, then its instance destructor loads it at `0x166644` and
calls it at `0x166648`. This address-taking path explains why hosting only the
two procedure resolvers did not cover destruction. Slot 2 redirects that
original function entry to the host's Vulkan implementation.

These are static boundary and prior crash observations. The new hook requires
a rebuilt smoke run to establish that the reported crash is fixed. Other
indirect feature paths, device operations, presentation, and iOS gameplay remain
unverified; the complete export inventory is not a claim that all 768 Vulkan
extensions are implemented or linked by MoltenVK.
