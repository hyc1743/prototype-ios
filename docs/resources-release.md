# Prototype 资源分卷 v1

此 Release 提供匹配 iOS 内置清单的完整资源分卷。IPA 正在独立构建和验证，
此处不代表游戏移植或实机验证已经完成。

下载 `PrototypeData.part001.ptdata` 至 `PrototypeData.part014.ptdata` 全部 14 卷。
每卷最多 536,870,912 字节（512 MiB），末卷 231,825,523 字节。
41 个原始文件总计 7,211,147,379 字节。

资源清单身份：`ea15579ce7a45045ca313f06221090c575ad6c6affd26932a8d59393732ba3a4`。
所有分卷及展开文件均已与本地原始目录通过 SHA-256 校验。

安装匹配的 Prototype IPA 后，将全部分卷复制至“文件 → 我的 iPhone → Prototype”
根目录或其中的 `ResourcePacks` 文件夹。应用自动校验和解包到 `game`。
无需手工解压，无需复制源代码压缩包或清单 JSON。

导入前另留至少约 7.3 GB 空间；完成后可删除分卷，保留 `game`。
中断时补齐分卷并点击应用中的“重新检查 / 继续解包”。
