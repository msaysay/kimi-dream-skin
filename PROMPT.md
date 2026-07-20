# 一套提示词安装（适合 Kimi Work 用户）

不想碰命令行？把下面这段提示词**原样发给 Kimi Work**（桌面版对话框），它会读取本仓库、完成环境检查、安装、验证，全程先报方案再动手。

> 前提：你已把本仓库克隆/下载到本机，并且知道它的路径。提示词里的 `仓库路径` 换成你的实际路径。

---

## 安装提示词（复制即用）

```text
请帮我安装 Kimi Dream Skin（一个通过 CDP 给 Kimi 桌面版注入 CSS 的开源换肤工具，不修改任何程序文件）。

仓库路径：<仓库路径>（例如 D:\cd\kimi-dream-skin）

请先完成以下检查，不要直接运行脚本：
1. 阅读仓库根目录 README.md 和 windows\SKILL.md，理解原理与安全边界；
2. 识别我的操作系统、Kimi.exe 的实际路径、Node.js 版本（要求 >= 18）；
3. 列出准备执行的命令、会写入的位置（应只有 %APPDATA%\KimiDreamSkin\state.json）和完整恢复方法；
4. 确认不会修改 app.asar、程序签名、账号数据、会话和任何工作区文件；
5. 确认调试端口只绑定 127.0.0.1。

把检查结果和执行计划展示给我，得到我确认后再执行：
- 提醒我手动完全退出 Kimi（包括托盘图标）——注意这会结束我们当前的对话，请把后续步骤写成我可以直接双击的指引；
- 运行 windows\scripts\start-dream-skin.ps1 -Theme gothic（也可以问我想用哪一套：diva 青蓝歌姬 / rose 粉雾玫瑰 / caishen 财神打工 / blackgold 舞台黑金 / scifi 红白科幻 / gothic 哥特虚空）；
- 重新打开对话后运行 verify-dream-skin.ps1 验证并截图；
- 如果验证失败，运行 restore-dream-skin.ps1 还原，不要继续尝试。

以后我想还原时，提醒我说"还原皮肤"或双击仓库里的 双击我-还原.cmd。
```

---

## 还原提示词

```text
请运行 <仓库路径>\windows\scripts\restore-dream-skin.ps1 帮我还原 Kimi 官方外观，并确认状态文件已清理。
```

---

## 它背后发生了什么（一句话版）

Kimi Work 读仓库 → 跑 `install` 检查环境 → 你手动退出 Kimi → 跑 `start`（以调试端口重启 Kimi 并注入 CSS）→ 跑 `verify` 截图确认。全程只注入样式，不动官方程序，`restore` 一键还原。
