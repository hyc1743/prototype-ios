# iPhone 测试

当前测试设备由用户提供：iPhone 17 Pro Max、iOS 27。2026-10-23 收到的实机
日志显示 A19 Pro GPU 已完成 Vulkan 与 guest 内存初始化。用户已确认开场动画
可播放、菜单可呼出，同时报告动画后黑屏、声音杂音和默认语言不符。
完整游戏可玩性仍未验证；本地未连接该 iPhone。

## 签名和安装

公共 Release 的 `Prototype-native-development.ipa` 未签名，需使用自己的
证书与有效描述文件签名。不要将 Apple ID 密码、私钥或描述文件提交到仓库。

如果设备日志显示地址区间保留／映射失败，签名时使用
[Prototype.entitlements](../ios/source-port/Prototype.entitlements) 中的
`com.apple.developer.kernel.extended-virtual-addressing` 能力，并确认最终签名和
描述文件允许该能力。仅把 plist 放进 IPA 不会赋予权限。早期日志显示的
`Unable to reserve the 4gb guest address space` 是共享映射句柄创建失败；该日志当时尚未
证明是扩展地址权限问题。`v0.1.0-native-dev.3` 已改用应用临时目录里的稀疏文件
作共享后备，并在 `engine.log` 写出文件创建、扩展和后续地址映射的具体系统错误。
后续日志已确认该内存后备修正能够进入游戏；升级测试时保留 Documents 中
已经解包的 `game/`。针对画面、音频、默认语言与图标的修正见
[native-dev.4 复测说明](native-dev4.md)。
原 SDK 需要预留从 `0` 到 `0x11FFFFFFF` 的连续 guest 窗口（4.5 GiB 虚拟地址）；
预留地址空间与实际占用同等大小的物理内存是不同概念。

这项能力的定义见 Apple 官方文档：
`https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.kernel.extended-virtual-addressing`。
不同签名工具是否保留自定义能力需要通过最终签名检查确认。`weixing3` 使用的
NRO 宿主不创建本项目的 Xbox guest arena，因此同一签名工具能运行 weixing3
并不能单独确认这项能力已应用到 Prototype。当前发布包没有请求动态代码执行或 JIT 的能力。

## 资源与首次启动

1. 从 `resources-v1` Release 下载全部 14 个 `PrototypeData.partNNN.ptdata` 分卷。
2. 复制到“文件 → 我的 iPhone → Prototype”，或其中的 `ResourcePacks` 文件夹。
3. 保留约 7.3 GB 额外空间用于解包，在前台等待自动校验完成。
4. 记录能否进入开场、主菜单和实际可操作场景，以及触控和实体手柄的反应。
5. 记录声音、保存／重新打开读取存档、切到后台再恢复的行为。

确认导入完成后可以删除分卷，保留 `game/`、`saves/` 和 `prototype.toml`。
测试更换 IPA 时保留应用 Documents，可避免重新导入资源和删除存档。

## 定位问题

“文件 → 我的 iPhone → Prototype”下的 `log.txt` 包含启动／异常信息，
`engine.log` 包含 SDK 日志，`log.prev.txt` 保留上一次启动记录。
若闪退，另行保留 iOS 的 Prototype 崩溃记录以及 jetsam 记录（如有）。
需要区分资源导入失败、共享映射后备创建失败、guest 地址空间映射／权限失败、
渲染初始化失败和进入游戏后的故障。`weixing3` 使用的 NRO 宿主与当前原生重编译路线
不同；前者成功启动不能单独证明 Xbox guest 的共享内存布局已在此 IPA 正常工作。
分享日志前可自行去掉与问题无关的个人路径信息；不需要发送游戏资源或签名私钥。

iPhone 开场动画及菜单已有用户反馈；修正后的游戏画面、音频和存档仍需实机复测。
