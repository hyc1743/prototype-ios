# Prototype iOS

将当前 `switch/prototype-nx` 的 Prototype 版本适配到 iPhone / iPad，使用
nxnative 原生宿主与 MoltenVK。参考实现为本地 `../weixing3` 项目。

当前处于开发阶段，**尚未发布可安装的 IPA**。资源分卷已发布至
[resources-v1](https://github.com/hyc1743/prototype-ios/releases/tag/resources-v1)。
生成 IPA、通过启动测试与完整游戏可玩性是分别验证的项目；
完整游戏、iPhone 实机画面、音频及存档尚未验证。构建结果见
[GitHub Actions](https://github.com/hyc1743/prototype-ios/actions)。

## 资源

游戏资源独立于 IPA，分为 14 卷。每卷最多 **512 MiB（536,870,912 字节）**，
低于每卷 1024 MB 的上限。总计 7,211,147,379 字节。

首次安装后，将所有 `PrototypeData.part001.ptdata` 至
`PrototypeData.part014.ptdata` 复制到“文件 → 我的 iPhone → Prototype”
根目录或其中的 `ResourcePacks` 文件夹。打开应用后自动校验、展开到 `game/`。
解包需要至少约 7.3 GB 额外空间；确认导入后可以删除分卷，保留 `game/`。
应用内置匹配的 SHA-256 清单，无需手工解压或复制 JSON。

资源生成、校验及恢复方法见 [资源指南](docs/resource-packs.md)。

## 构建

公共仓库在 macOS GitHub Actions 使用 Xcode 26 构建，IPA 为未签名开发包，
需要自行签名侧载；最低 iOS 26。不要求在 iPhone 上启用 JIT。

源仓库只提交代码、文本清单和认证加密后的固定 NRO 输入。
NSP、Switch keys、明文 NRO 和游戏资源不会进入 Git 历史。
构建密钥通过仓库 Secret `PROTOTYPE_GUEST_KEY` 提供。
详见 [云端构建](docs/github-cloud.md)。

生产移植路线正在把原始 PPC 静态翻译、原生渲染器及 ReXGlue SDK 重新编译为 Apple ARM64，
以正确处理 Apple 16 KiB 内存页。`build-source-ios.yml` 在 main 更新时自动构建此路线；
`build-ios.yml` 保留为手动触发的原 NRO 启动诊断。两者都不会仅凭编译成功声称游戏可玩。

本地分析工具严格核对当前 NRO 的 SHA-256：
`16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`。
更换 NRO 后必须重新核对偏移和指令，不能套用现有 hook。

虚拟与实体手柄由 nxnative 前端提供；配置为 `prototype.toml`，
首次启动复制到 Documents，之后保留玩家修改。存档路径及完整运行行为仍需验证。

## 致谢

- fungalspores 的 [Prototype Switch 移植](https://github.com/fungalspores/prototype-switch)
- [ReXGlue SDK](https://github.com/rexglue/rexglue-sdk) 与 masseffect-nx、Prototype 2 原生渲染器贡献者
- [seedds/nxnative](https://github.com/seedds/nxnative)
- [KhronosGroup/MoltenVK](https://github.com/KhronosGroup/MoltenVK)
- weixing3 / Biko3 项目的构建、资源导入与平台适配实现

代码及第三方许可见 [LICENSE](LICENSE) 和 [THIRD_PARTY.md](THIRD_PARTY.md)。
