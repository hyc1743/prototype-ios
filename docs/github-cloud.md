# GitHub 云端构建

2026-10-23 已通过 GitHub API 只读验证：认证账号为 `hyc1743`
（用户 ID `27811593`）；`hyc1743/prototype-ios` 当时返回 HTTP 404，尚未创建。
已有 `../super-mario-odyssey-ios` 目录的 Git Credential Manager 配置可用于认证。

```powershell
python -m pip install -r requirements.txt
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios identity
```

认证从 `GH_TOKEN`、`GITHUB_TOKEN` 或非交互 Git Credential Manager 获取。
令牌只保存在进程内存及 Git 子进程环境中，不进入命令行、临时文件或日志。
下载 Actions artifact 时，跨主机跳转会移除 Authorization；Release 上传固定为
GitHub uploads 主机，并拒绝跳转。

公开源码由 `public-files.json` 的精确文件清单决定。每项须为仓库中的相对
POSIX 文件路径，不接受目录、重复路径、通配符、符号链接或路径穿越。
审阅命令输出每个文件的大小与 SHA-256。唯一二进制例外是经过格式和固定
原始 NRO 摘要验证的 `ci-inputs/guest.enc`，以及经过固定 ZIP 摘要验证的
`ci-inputs/codegen.enc`。其余二进制均拒绝进入源码清单。

```powershell
python tools/github_cloud.py review --manifest public-files.json
```

`publish` 在临时仓库创建只包含清单文件的提交，核对暂存文件集合，使用普通
快进 push 保留历史。原始 `switch/`、keys、NSP、依赖 checkout、工作目录、构建
产物和任意其他二进制不会进入源码提交。

以下命令创建用户要求的公共仓库、配置加密输入并发布构建源码：

```powershell
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios create-public prototype-ios
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios set-secret hyc1743/prototype-ios PROTOTYPE_GUEST_KEY --value-file .tools/private/prototype-guest.key
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios publish hyc1743/prototype-ios --manifest public-files.json
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios runs hyc1743/prototype-ios build-ios.yml
```

main 的 push 自动启动完整源码工作流 `build-source-ios.yml`；原 NRO 的
`build-ios.yml` 仅手动触发。以 `runs` 返回的 `head_sha`、创建时间和运行 ID
确认具体构建。重跑时再调用 `dispatch`。查询暂时超时不表示构建停止，应继续
查询同一运行 ID。

```powershell
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios dispatch hyc1743/prototype-ios build-ios.yml
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios run hyc1743/prototype-ios 12345
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios artifacts hyc1743/prototype-ios 12345
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios download hyc1743/prototype-ios 67890 work/cloud/artifact.zip
```

`12345` 和 `67890` 分别替换为实际运行 ID 与 artifact ID。

## 加密的构建输入

本项目的原始 guest 为 `switch/prototype-nx/prototype.nro`，大小 64,648,600 字节，
SHA-256 为 `16165370c4484394c81a775f48b1b2b47c833cc2bd412727935cb0fb17401330`。
`ci_guest.py` 只接受该摘要且带 NRO0 标识的文件，不读取游戏资源、NSP 或 keys。
容器使用 AES-256-GCM，头部和正文同时受到认证，随机 nonce 防止重复密文。

```powershell
python tools/ci_guest.py seal switch/prototype-nx/prototype.nro ci-inputs/guest.enc --manifest ci-inputs/guest.json
```

第一次 seal 生成 `.tools/private/prototype-guest.key`；已有密钥会复用，已有输出
则拒绝覆盖。密钥文件留在本地，只通过仓库公钥 sealed box 上传为 Actions
Secret `PROTOTYPE_GUEST_KEY`。CI 把此 Secret 注入同名环境变量后运行：

```sh
python tools/ci_guest.py open ci-inputs/guest.enc "$RUNNER_TEMP/guest.nro"
```

解密必须通过认证、固定 SHA-256 和 NRO0 校验；错误密钥或篡改内容会终止构建。
加密输入、构建脚本或编译成功本身都不能证明 iOS 游戏运行成功。

## Apple 原生源码构建

`build-source-ios.yml` 重新编译原始 PPC 翻译、原生渲染器和 ReXGlue SDK。
使用 Apple 的 16 KiB 页支持，避免套用 Switch 的 4 KiB 物理内存镜像。
原 NRO 的 `build-ios.yml` 保留为独立启动诊断。

源码生成输入来自本地 `game/default.xex`：SHA-256
`fda520ae3721eaddede1fd773af3ad2f6099a5ae2981849fb07ed4c7a8b53afe`。
官方 ReXGlue v0.10.0 已生成 157 个 C++ 单元、53,211 个翻译函数。
316 个必要源码／头文件通过确定性 ZIP 和 AES-256-GCM 认证加密后存于
`ci-inputs/codegen.enc`；明文源码及 XEX 不进入 Git 历史。
对应 Secret 为 `PROTOTYPE_CODEGEN_KEY`，密钥位于本地
`.tools/private/prototype-codegen.key`。

```powershell
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios set-secret hyc1743/prototype-ios PROTOTYPE_CODEGEN_KEY --value-file .tools/private/prototype-codegen.key
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios dispatch hyc1743/prototype-ios build-source-ios.yml
python tools/github_cloud.py --credential-dir ../super-mario-odyssey-ios runs hyc1743/prototype-ios build-source-ios.yml
```

CI 验证解密认证、固定 ZIP 摘要和每个文件摘要，准备固定的原项目、SDK 与
17 个依赖提交，再使用 iOS SDK 编译。输出检查要求全部 53,211 个翻译函数
存在于实际 ARM64 可执行文件、无未打包动态库，并核对资源清单和默认配置。
准备成功或编译成功不会自动建立游戏运行／画面／存档验证结论。

## IPA 与资源 Release

IPA 上传必须同时提供 `artifacts/release/` 内的构建报告，包含与 IPA 相符的
`ipa_sha256`、`ipa_bytes`，以及 `contains_guest_executable: true`、
`signed: false`、`runtime_verified: false`。工具检查应用目录为
`Payload/Prototype.app/Prototype`，拒绝包内原始 NRO、NSP、keys 和游戏 Data。
当前开发产物的签名、设备安装和运行验证情况应在 Release 中如实说明。

`release` 要求完整 40 位提交 SHA 和 Markdown 说明文件，默认创建预发行版本；
`--stable` 明确选择正式发布。`upload-release-assets` 核对报告和 IPA 摘要，
拒绝覆盖同名附件。游戏资源单独打包，任何一卷均不超过 1024 MiB
（1,073,741,824 字节），以清单中的字节数和 SHA-256 与应用版本绑定。
资源不进入 Git 源码或 IPA，单独上传为对应 Release 附件。
