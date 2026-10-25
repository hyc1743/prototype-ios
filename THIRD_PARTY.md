# Third-party components

The MIT license in LICENSE covers the original iOS adapter and tooling in this
repository. Tools adapted from the sibling Biko3 iOS project retain that project's
2026 contributor copyright notice. It does not relicense game executables,
game assets, or upstream dependencies.

| Component | Source and fixed version | Use and license |
|---|---|---|
| nxnative | seedds/nxnative, 677345b0933f5f8754aeec3125d14df32683ca97 | Native host, offline AArch64 rewriter, input UI and system services; MIT with bundled dependency licenses |
| MoltenVK | KhronosGroup/MoltenVK v1.4.2 | Vulkan graphics and compute on Metal; Apache-2.0 and bundled notices |
| prototype-switch | fungalspores/prototype-switch, ea65b2e188c62be33fe00e566288e61914e4a78e | Original app and native renderer in the source build; GPL-3.0, preserved in the application notices |
| mgr-nx-deko | tuancapoo/mgr-nx-deko, imported by prototype-switch 1.4.0 | Post-codegen speed transforms; GPL-3.0 |
| ReXGlue / masseffect-nx | nebadasSwifty/masseffect-nx, a85b0b419ec3e332165ae1617c980bd399856a44, with the pinned Prototype patch | Native source runtime; upstream BSD and component licenses, retained in application notices |
| devkitPro libnx/newlib | Public ABI declarations verified against the supplied executable | Homebrew startup and system ABI documentation; upstream licenses remain applicable |
| Biko3 iOS infrastructure | Local ../weixing3 reference | Adapted authenticated guest container and GitHub transport; MIT |

The native host dependencies keep their upstream licenses and notices. The build
stages nxnative and MoltenVK notices into the application. Python dependencies
retain their own package licenses.

The source build compiles the original GPL renderer/app from its fixed public
revision. The adapted source-build CMake file retains GPL-3.0-or-later. The
reviewed transforms and dependency revisions are recorded by
tools/prepare_source_port.py; the corresponding upstream source and all local
changes remain available. The original MIT infrastructure does not relicense
these components or the supplied executable. The source build bundles the
Prototype GPL license and SDK dependency license texts with its app.

Switch keys, NSPs, plaintext guest executables, and game resources are excluded
from public source history. Game resources are distributed separately in volumes
of at most 512 MiB each (536,870,912 bytes, below 1024 MB); the IPA imports the
matching volumes at runtime.
