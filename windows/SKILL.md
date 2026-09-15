---
name: kimi-dream-skin-windows
description: 在 Windows 上为 Kimi 桌面版（Electron）更换皮肤的操作流程。当用户要求给 Kimi 桌面版换肤、美化界面、注入主题 CSS、验证或恢复皮肤时使用。
version: 1.0.0category: customizationplatforms:  - windowslicense: MIT
---

# Kimi Dream Skin —— AI Agent 操作说明

本技能描述如何安全地为 **Windows 版 Kimi 桌面客户端** 注入 CSS 皮肤。原理与 Codex Dream Skin 相同：Kimi 是 Electron 应用，以 `--remote-debugging-port` 启动后，通过 CDP（Chrome DevTools Protocol）向渲染页面注入 CSS。**不修改 app.asar，不破坏签名，不读写用户数据目录。**

## 绝对安全红线

1. **永远不要 kill / 关闭 / 重启用户正在运行的 Kimi 实例**——用户可能正在里面工作（包括正在与你对话）。
2. **永远不要读写 `%APPDATA%\kimi-desktop`（真实 profile）下的任何文件。**
3. 测试或演示时，必须用独立临时 profile 启动第二个实例：
   ```powershell
   Start-Process "C:\Users\<用户>\AppData\Local\Programs\kimi-desktop\Kimi.exe" -ArgumentList `
     "--remote-debugging-port=<随机端口>","--remote-debugging-address=127.0.0.1","--user-data-dir=<临时目录>"
   ```
   结束后只按启动时记录的 PID 精确结束该实例，**绝不按进程名批量杀**。
4. 调试端口一律绑定 `127.0.0.1`。

## 目录约定（相对本文件所在 windows/ 目录）

- `engine/cdp.mjs` —— 零依赖 CDP 客户端（Node 22+ 内置 WebSocket）
- `engine/inject.mjs` —— 注入引擎（支持 `--watch` 驻留、`--remove` 移除）
- `engine/verify.mjs` —— 验证标记 + 截图
- `engine/probe.mjs` —— DOM 结构探测器（选择器失效时重新适配用）
- `themes/*.css` —— 皮肤文件（10 套经典：diva 青蓝歌姬 / rose 粉雾玫瑰 / caishen 财神打工 / blackgold 舞台黑金 / scifi 红白科幻 / gothic 哥特虚空 / dunhuang 敦煌飞天 / qinghua 青花瓷 / jiangshan 千里江山(默认) / moyun 水墨佳人；每套另有 <主题>-surprise 惊喜版，共 20 个。背景图在 themes/assets/，注入时自动内联）
- `scripts/*.ps1` —— install / start / verify / restore

## 标准流程

### 1. 环境检查
```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-dream-skin.ps1
```

### 2. 启动换肤（要求 Node >= 18，建议 22+）
先确认 `Get-Process Kimi` 无结果（在运行则提示用户手动退出，绝不代杀），然后：
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-dream-skin.ps1 -Theme dream
```
脚本会：探测 Kimi.exe → 申请空闲端口 → 以调试参数启动 → 写 `%APPDATA%\KimiDreamSkin\state.json` → 调用 `node engine/inject.mjs --port <端口> --theme dream` 注入。

### 3. 验证
```powershell
powershell -ExecutionPolicy Bypass -File scripts\verify-dream-skin.ps1
```
验证依据：页面 `<head>` 中存在 `<meta name="kimi-dream-skin" content="<主题>">` 与 `<style id="kimi-dream-skin-style">`，并截图存证。

### 4. 恢复
```powershell
powershell -ExecutionPolicy Bypass -File scripts\restore-dream-skin.ps1
```
皮肤只存在于进程内存：在线移除 style/meta 即恢复外观；彻底干净只需用户自行重启 Kimi。

## 手动调用引擎（脚本背后的等价命令）

```bash
node engine/inject.mjs --port 9333 --theme diva           # 注入一次
node engine/inject.mjs --port 9333 --theme diva  --watch  # 驻留，新页面自动补注入
node engine/inject.mjs --port 9333 --remove               # 在线移除
node engine/verify.mjs --port 9333 --out shot.png         # 验证 + 截图
node engine/probe.mjs  --port 9333                        # 探测 DOM 结构
```

## 已知坑（来自真实环境验证）

- Kimi 有单实例机制：正式换肤必须在 Kimi 完全退出后以调试参数启动；测试第二实例必须换 `--user-data-dir`，且建议加 `--no-sandbox`（裸参数启动的测试实例观测到过一次中途退出）。
- 主窗口页面 URL 是 `app://localhost/kimi-agent.html`，标题 "Kimi Agent"；CDP `/json` 里还可能看到 `volcano-tracker.html` 等其它 page，注入目标要选 kimi-agent。
- 页面 URL 带 `theme=light`：深色系皮肤必须在 CSS 里覆盖文字颜色，否则深底黑字不可读。
- 登录弹窗（`.login-modal`）保持原生配色，避免干扰登录流程。

## 皮肤选择器适配

Kimi 更新后 class 可能变化。先跑 `node engine/probe.mjs --port <端口>` 拿到最新结构，再改 `themes/*.css`。当前已验证的稳定 class：

- 布局：`.page`、`.sidebar`、`.main-pane`、`.win-titlebar-drag`
- 侧栏：`.sidebar-scroll`、`.nav-item`、`.nav-label`、`.section-title`、`.conversation-list`
- 输入区：`.composer`、`.composer-box`、`.composer-editor`、`.composer-wrap`、`.send-btn`、`.model-picker-trigger`
- 主页：`.home-view`、`.home-scroll`、`.hero`、`.greeting`、`.greeting-subtitle`、`.mode-tab`
- 消息：`.message-list-container`、`.message-list`
- 弹窗：`.login-modal`、`.kimi-button`
