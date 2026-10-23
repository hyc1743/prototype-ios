# Prototype 资源分卷

IPA 内置 `PrototypeData.manifest.json`。游戏资源独立分为 14 卷，复制全部
`PrototypeData.part001.ptdata` 至 `PrototypeData.part014.ptdata` 到“文件 → 我的 iPhone → Prototype”
根目录，或其中的 `ResourcePacks` 文件夹，保持原文件名。无需另外复制清单。

打开应用后保持前台运行，应用先校验分卷 SHA-256，再恢复文件并校验每个文件 SHA-256。
资源写入 `Documents/game`，例如 `Documents/game/default.xex` 和 `Documents/game/00art.rcf`。
文件已通过校验时会保留；导入中断后补齐分卷并点击“重新检查 / 继续解包”。未完成的当前文件重新展开。
也可以将完整的原始 `game` 文件夹直接复制到应用的文档目录。

资源原始总量为 **7,211,147,379 字节**，共 41 个文件。解包前，手机需在存放全部分卷之外
另外保留至少约 7.3 GB 可用空间；分卷与展开资源同时保留时合计约 14.43 GB。
确认导入完成后，可以删除手机上的 14 个 `.ptdata` 分卷以回收空间。

分卷使用原始字节连续拼接；每卷 512 MiB（536,870,912 字节），最后一卷 231,825,523 字节，
全部小于 1024 MB（按十进制 1,024,000,000 字节计算）。这不是 ZIP 文件，不能用系统压缩工具展开。
跨卷文件依靠 IPA 内置清单中的分段信息恢复。

在项目根目录执行：

```powershell
python tools/pack_resources.py pack --data switch/prototype-nx/game --out out/resources --manifest-copy ios/resources/PrototypeData.manifest.json
python tools/pack_resources.py verify --parts out/resources --data switch/prototype-nx/game
```

输出目录必须尚不存在，避免覆盖已有分卷。工具只读取指定游戏目录；不会读取项目根目录的密钥或 NSP。
资源和密钥不进入 Git；公开仓库存放移植源码和资源清单，游戏分卷通过 Release 附件发布。

仅检查分卷，或在电脑上恢复到新目录：

```powershell
python tools/pack_resources.py verify --parts out/resources --manifest ios/resources/PrototypeData.manifest.json
python tools/pack_resources.py restore --parts out/resources --out out/restored-resources
```

电脑恢复目录为 `out/restored-resources/game`。清单采用 `prototype-resource-parts-v1`，路径均为规范的
`game/...` 相对路径；文件与分卷均含 SHA-256，分段按顺序完整覆盖每个分卷。清单拒绝路径逃逸、链接、
大小写冲突、重叠和空洞。源文件清单身份 SHA-256：

`ea15579ce7a45045ca313f06221090c575ad6c6affd26932a8d59393732ba3a4`

`tools/publish_resources.py` 仅选择 `out/` 内与 IPA 内置清单一致的分卷，逐卷核对本地哈希，
并核对 GitHub 上传结果的 SHA-256。相同附件可跳过继续；附件同名但内容不同时拒绝覆盖。
