---
name: ningloop
description: 宁少AI进化论的创作工作台 NingLoop。用户说「处理 NingLoop 的 AI 待办」「用 NingLoop 写个脚本」「出稿」「拆解对标爆款」「AI 复盘」时使用：用 ning agent 命令领任务、按说明写稿或复盘、交回工作台。
---

# NingLoop 出稿与 AI 待办

在 NingLoop 文件夹里运行命令（Windows PowerShell 用 `.\ning`，cmd 用 `ning`，Git Bash、macOS、Linux 用 `./ning`）。

1. 给一个新选题出稿：`ning agent new "<选题>"`（口播类加 `--talk`）。命令会打印完整任务说明。
2. 处理已排队的待办：先 `ning agent todo`，再对每条运行 `ning agent brief <编号>`。
3. 按任务说明写结果，开头 `---` 块里填结构标签，字段名用中文，值只能用字典里的。写完存成 UTF-8 文件，路径在说明末尾。
4. `ning agent submit <编号>` 交回。报错就按提示改好再交。
5. 告诉用户：稿子已经出现在工作台「创作」里对应选题上，阶段也自动推进了。

规矩：只用任务说明里的数据，不编数字；已定的结构不改；结尾只指向评论区关键词、主页置顶或私信。完整说明见仓库根目录的 AGENTS.md。
