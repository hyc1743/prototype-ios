# Prototype iOS

将当前 `switch/prototype-nx` 的 Prototype 版本适配到 iPhone / iPad，重新编译
原始 PPC 翻译、原生渲染器和 ReXGlue SDK，使用 SDL3/UIKit 与 MoltenVK。
参考实现为本地 `../weixing3` 项目。

原生 IPA 已发布至 [v0.1.0-native-dev.3](https://github.com/hyc1743/prototype-ios/releases/tag/v0.1.0-native-dev.3)，
为需要自行签名的开发预发行版。资源分卷已发布至
[resources-v1](https://github.com/hyc1743/prototype-ios/releases/tag/resources-v1)。
生成 IPA、通过启动测试与完整游戏可玩性是分别验证的项目；
完整游戏、iPhone 实机画面、音频及存档尚未验证。构建结果见
[GitHub Actions](https://github.com/hyc1743/prototype-ios/actions)。

`native-dev.2` 修复预先连接的 SDL 手柄未进入 SDK 的问题；`native-dev.3` 为 iOS
Xbox guest arena 增加应用目录内的稀疏共享映射后备。真实设备上完整资源导入后仍需
重新验证内存初始化和运行行为，实机结果以该设备日志为准。

## 资源

游戏资源独立于 IPA，分为 14 卷。每卷最多 **512 MiB（536,870,912 字节）**，
低于每卷 1024 MB 的上限。总计 7,211,147,379 字节。

首次安装后，将所有 `PrototypeData.part001.ptdata` 至
`PrototypeData.part014.ptdata` 复制到“文件 → 我的 iPhone → Prototype”
根目录或其中的 `ResourcePacks` 文件夹。打开应用后自动校验、展开到 `game/`。
解包需要至少约 7.3 GB 额外空间；确认导入后可以删除分卷，保留 `game/`。
应用内置匹配的 SHA-256 清单，无需手工解压或复制 JSON。

资源生成、校验及恢复方法见 [资源指南](docs/resource-packs.md)。
实机签名、资源导入和日志定位见 [iPhone 测试](docs/device-testing.md)。

## 构建

公共仓库在 macOS GitHub Actions 使用 Xcode 26 构建，IPA 为未签名开发包，
需要自行签名侧载；最低 iOS 26。不要求在 iPhone 上启用 JIT。

源仓库只提交代码、文本清单和认证加密后的固定构建输入。
NSP、Switch keys、明文 NRO/XEX、游戏生成源码和资源不会进入 Git 历史。
生产构建使用 Secret `PROTOTYPE_CODEGEN_KEY`；独立 NRO 诊断使用 `PROTOTYPE_GUEST_KEY`。
详见 [云端构建](docs/github-cloud.md)。

生产路线已在 iPhoneOS SDK 下成功构建，实际 IPA 的 iOS 设备平台标记、全部 53,211 个
翻译函数、系统动态库依赖、内置资源清单及许可证已独立核验。
`build-source-ios.yml` 在 main 更新时自动构建此路线；
`build-ios.yml` 保留为手动触发的原 NRO 启动诊断。两者都不会仅凭编译成功声称游戏可玩。

本地分析工具严格核对当前 NRO 的 SHA-256：
`16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`。
更换 NRO 后必须重新核对偏移和指令，不能套用现有 hook。

触控界面沿用 nxnative，触控和 GameController 输入合并到 SDL 虚拟 Xbox 手柄；配置为 `prototype.toml`，
首次启动复制到 Documents，之后保留玩家修改。存档路径及完整运行行为仍需验证。

## 致谢

- fungalspores 的 [Prototype Switch 移植](https://github.com/fungalspores/prototype-switch)
- [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk) 与 masseffect-nx、Prototype 2 原生渲染器贡献者
- [seedds/nxnative](https://github.com/seedds/nxnative)
- [KhronosGroup/MoltenVK](https://github.com/KhronosGroup/MoltenVK)
- weixing3 / Biko3 项目的构建、资源导入与平台适配实现

代码及第三方许可见 [LICENSE](LICENSE) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。
