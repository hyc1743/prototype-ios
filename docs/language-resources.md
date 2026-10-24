# 游戏文本语言

资源中的语言槽名不能证明正文语言。当前 resources-v1 的
`00art.rcf` 内 `textbible_english.p3d.rz` 有 2,951 个文本项，正文实际为巴西葡语。
前端文件 `fe_textbible.p3d.rz` 的 English 分支也相同；其余分支为法语、意大利语和西班牙语。
未发现中文文本分支。`user_language = 1` 选择 English 分支，无法撤销资源内已有的葡语替换。

本地 NSP 是启动转发器，不包含这些游戏资源。修改 NSP 的语言列表也无法恢复英文。

检查玩家已有的资源：

```powershell
python tools/game_localization.py --game-root "switch/prototype-nx/game"
```

从玩家自己的未替换英文副本恢复菜单、任务和 HUD 文本：

```powershell
python tools/game_localization.py --game-root "switch/prototype-nx/game" --donor "D:/OriginalPrototype/00art.rcf" --output "work/english-overlay"
```

`--donor` 也可指定原版的 `textbible_english.p3d` 或 `.p3d.rz` 文件。
工具检查正文是否为英文、所有目标文本键是否齐全，再生成
`game/art/hud/fe_textbible.p3d.rz` 和 `textbible_english.p3d.rz` 两个覆盖文件。
目标的键顺序与其他语言分支保持不变；不修改原始 RCF 或存档。
文件校验值记录在输出目录的 `english-restoration.json`。

这些覆盖文件需要在真实游戏运行中确认读取优先级；静态解析通过不代表实机已切换成功。
它们仅恢复文本表中的内容，不声称恢复单独存放于音频或过场资源内的字幕。
工具不附带、不下载或重新翻译原版游戏文本。
