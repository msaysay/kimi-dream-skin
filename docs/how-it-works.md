# 原理说明：Electron + CDP 注入，为什么不改 app.asar

## Kimi 桌面版是什么

Kimi 桌面版是一个标准的 Electron 应用：

- 可执行文件：`%LOCALAPPDATA%\Programs\kimi-desktop\Kimi.exe`（内核是 Chromium + Node）
- 前端代码打包在 `resources\app.asar` 里
- 主界面本质上是一个网页：`app://localhost/kimi-agent.html`（标题 "Kimi Agent"）

Electron 的内核是 Chromium，而 Chromium 天生支持 **CDP（Chrome DevTools Protocol）**——就是 DevTools 用来检查元素、改样式的那套协议。只要应用启动时带上 `--remote-debugging-port`，任何人都能通过 `http://127.0.0.1:<端口>/json` 枚举页面，通过 WebSocket 向页面执行 JavaScript。

## 换肤的四步

```
Kimi.exe --remote-debugging-port=N --remote-debugging-address=127.0.0.1
        │
        ▼
GET http://127.0.0.1:N/json          ← 找到 type=page 且 url 含 kimi-agent.html 的 target
        │
        ▼
WebSocket: ws://127.0.0.1:N/devtools/page/<id>
        │
        ├─ Page.addScriptToEvaluateOnNewDocument  ← 注册"每个新文档先注入 CSS"（跳转不丢）
        └─ Runtime.evaluate                        ← 当前页面立即注入 <style> + 标记 <meta>
```

注入的内容只有两样：

1. 一个 `<style id="kimi-dream-skin-style">` 元素，内容是主题 CSS（磨砂面板、渐变背景、强调色）；
2. 一个 `<meta name="kimi-dream-skin" content="主题名">` 标记，供 verify 脚本确认"皮肤确实挂上了"。

## 为什么不改 app.asar

| 方案 | 修改 app.asar / 补丁主进程 | CDP 注入（本项目） |
| --- | --- | --- |
| 破坏代码签名 | 是 | 否 |
| 触发应用自更新覆盖/校验失败 | 高 | 无（更新照常） |
| 被杀软 / 完整性检查报警 | 可能 | 不会 |
| 恢复原生 | 需要备份还原 | **重启即原生** |
| 法律/合规风险（改再分发二进制） | 有 | 只在本地运行时生效 |

CDP 注入的 CSS 只活在**当前进程的内存**里。磁盘上的 Kimi 一个字节都没变。把 Kimi 关掉再正常双击打开，就是 100% 原生。

## 安全边界

- 调试端口只绑定 `127.0.0.1`，局域网/外网无法连接。
- 启动器**绝不强制结束**正在运行的 Kimi；检测到在运行时只提示用户手动退出。
- 不读取、不写入用户数据目录（`%APPDATA%\kimi-desktop`）里的任何文件。
- 状态文件只记录 PID / 端口 / 主题名，放在 `%APPDATA%\KimiDreamSkin\state.json`。

## 局限

- Kimi 有单实例机制：皮肤要在启动参数里加调试端口，所以**必须先完全退出 Kimi**（含托盘），再用 start 脚本拉起。
- 应用大版本更新后 CSS class 可能变化，皮肤局部失效。用 `engine/probe.mjs` 重新探测 DOM、调整选择器即可，不需要动引擎。
- `--remote-debugging-port` 是 Chromium 官方特性，但理论上也让本机其他进程能连上调试端口。日常使用建议皮肤验证后按需保留，介意的话 restore 后用普通方式启动即可。
