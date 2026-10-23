# Third-party components

The MIT license in LICENSE covers the original iOS adapter and tooling in this
repository. Tools adapted from the sibling Biko3 iOS project retain that project's
2026 contributor copyright notice. It does not relicense game executables,
game assets, or upstream dependencies.

| Component | Source and fixed version | Use and license |
|---|---|---|
| nxnative | seedds/nxnative, 677345b0933f5f8754aeec3125d14df32683ca97 | Native host, offline AArch64 rewriter, input UI and system services; MIT with bundled dependency licenses |
| MoltenVK | KhronosGroup/MoltenVK v1.4.2 | Vulkan graphics and compute on Metal; Apache-2.0 and bundled notices |
| prototype-switch | fungalspores/prototype-switch, 292de4b382dbe335011d8b4f56ec4212df5ef74c | Reference for supplied Switch build identity and behavior; GPL-3.0; reference checkout excluded from publication |
| devkitPro libnx/newlib | Public ABI declarations verified against the supplied executable | Homebrew startup and system ABI documentation; upstream licenses remain applicable |
| Biko3 iOS infrastructure | Local ../weixing3 reference | Adapted authenticated guest container and GitHub transport; MIT |

The native host dependencies keep their upstream licenses and notices. The build
stages nxnative and MoltenVK notices into the application. Python dependencies
retain their own package licenses.

No GPL renderer or engine source from prototype-switch is copied into the
original MIT infrastructure. If GPL source is subsequently incorporated, its
license and corresponding-source requirements must be retained; this MIT notice
does not override them. The supplied guest executable is independently identified
by SHA-256 in ci-inputs/guest.json and is not relicensed here.

Switch keys, NSPs, plaintext guest executables, and game resources are excluded
from public source history. Game resources are distributed separately in volumes
of no more than 1024 MiB each; the IPA imports the matching volumes at runtime.
