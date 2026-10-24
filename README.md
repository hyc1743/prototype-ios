# Prototype iOS

Prototype iOS 是一个面向 iPhone 和 iPad 的原生移植项目。项目将 Nintendo Switch 版 Prototype 的 PPC 代码、原生渲染器和 ReXGlue SDK 适配到 Apple 平台，并使用 SDL3、UIKit 与 Vulkan/MoltenVK 提供图形和输入支持。

当前正式版本为 **1.0.0**，对应构建号 **11**。IPA 为未签名开发包，需要使用者自行签名安装。项目不包含受版权保护的原始游戏资源；运行时需要从合法来源准备游戏数据，并按资源指南导入应用。

正式版下载：

- [GitHub Release v1.0.0](https://github.com/hyc1743/prototype-ios/releases/tag/v1.0.0)
- [直接下载 IPA](https://github.com/hyc1743/prototype-ios/releases/download/v1.0.0/Prototype-1.0.0.ipa)
- [资源分卷说明](docs/resource-packs.md)

## 功能与状态

- 支持 iPhone / iPad 横屏运行，最低 iOS 26.0。
- 支持简体中文资源，保留英文音频和原始游戏音频。
- 使用游戏原图标，并提供触控、GameController 与 SDL 虚拟 Xbox 手柄输入。
- Vulkan 生命周期门控会在应用进入后台时暂停 GPU 提交，恢复时等待设备空闲，避免后台权限错误导致设备丢失。
- 已修复原生渲染中的顶点兼容、纹理、音频和后期合成问题；帧率和完整流程仍应以实际设备测试为准。

## 资源导入

IPA 不内置原始游戏资源。首次安装后，将资源分卷复制到应用的文件共享目录，再打开应用导入。详细的分卷命名、校验、恢复和空间要求见 [资源指南](docs/resource-packs.md)。实机签名、导入日志和故障定位见 [设备测试说明](docs/device-testing.md)。

## 构建

公开仓库仅提交源代码、文档、校验清单和经过加密的 CI 构建输入。原始 NSP、Switch 密钥、明文 NRO/XEX、完整游戏资源和生成源不会进入 Git 历史。GitHub Actions 使用 macOS、Xcode 和 iPhoneOS SDK 构建未签名 IPA；详细流程见 [云端构建说明](docs/github-cloud.md)。

构建结果不能单凭“编译成功”视为可玩。每个版本都应核对 IPA 的 Mach-O 平台、资源清单、版本号、哈希和实机日志，并完成启动、场景切换、音频、后台恢复与存档测试。

## 参考项目与致谢

本项目借鉴、复用或对照了以下公开项目、SDK 和平台资料，感谢所有维护者与贡献者：

- [fungalspores/prototype-switch](https://github.com/fungalspores/prototype-switch)：Prototype Switch 版本、游戏代码与资源结构参考。
- [nebadasSwifty/masseffect-nx](https://github.com/nebadasSwifty/masseffect-nx)：ReXGlue SDK 集成和 PPC 运行时参考。
- [rexglue/rexglue-sdk](https://github.com/rexglue/rexglue-sdk)：ReXGlue SDK、服务层和运行时组件。
- [seedds/nxnative](https://github.com/seedds/nxnative)：原生控制器、SDL 输入桥接和 iOS 控制器适配。
- [LNPANDY/NX2Apple](https://github.com/LNPANDY/NX2Apple)：Switch 项目向 Apple 平台移植的工程思路和兼容性参考。
- [jhaizhou-ops/d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile)：移动端游戏移植、资源导入和生命周期处理思路参考。
- [KhronosGroup/MoltenVK](https://github.com/KhronosGroup/MoltenVK)：Vulkan 到 Apple Metal 的实现。
- [libsdl-org/SDL](https://github.com/libsdl-org/SDL)：跨平台窗口、输入和控制器支持。
- 本地 [weixing3 / Biko3](https://github.com/hyc1743/biko3-ios) 项目：构建、资源导入、UIKit 生命周期和 Apple 平台适配经验参考。

第三方代码和许可证信息见 [LICENSE](LICENSE) 与 [THIRD_PARTY.md](THIRD_PARTY.md)。各项目的具体提交版本记录在 [references.lock.json](references.lock.json)。
