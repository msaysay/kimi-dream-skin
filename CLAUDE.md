# NingLoop

给 AI 助手的完整说明在 [AGENTS.md](AGENTS.md)，先读它。要点：

- 你就是 NingLoop 的大模型。用户说「处理 NingLoop 的 AI 待办」时，运行 `ning agent todo`，然后对每一条依次 `ning agent brief <编号>` → 按说明写结果并存到 `data/agent/task-<编号>.md` → `ning agent submit <编号>`。
- 用户说「用 NingLoop 写个脚本：<选题>」时，运行 `ning agent new "<选题>"`（口播类加 `--talk`），再照上面的写、存、交。
- 命令写法：Windows PowerShell 用 `.\ning`，cmd 用 `ning`，Git Bash、macOS、Linux 用 `./ning`。
- 只用 brief 里的数据；结构标签只能用字典里的值；结尾不写微信号和外链。
