# ============================================================================
# install-dream-skin.ps1 —— 环境检查与安装引导
# 只做检查与提示，不修改 Kimi 的任何文件，也不启动应用。
# ============================================================================
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

Write-Host '=== Kimi Dream Skin 安装检查 ===' -ForegroundColor Cyan

# 1) Node.js >= 18（注入引擎运行环境；Node 22+ 有内置 WebSocket，零依赖）
$nodeVer = Get-NodeVersion
if (-not $nodeVer) {
    Write-Host '[缺失] 未找到 Node.js。请先安装 Node 18+：https://nodejs.org/' -ForegroundColor Red
    exit 1
}
if ($nodeVer -lt [version]'18.0.0') {
    Write-Host "[版本过低] Node $nodeVer，需要 >= 18（建议 22+）" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Node.js v$nodeVer" -ForegroundColor Green

# 2) 找到 Kimi.exe
$exe = Find-KimiExe
if (-not $exe) {
    Write-Host '[缺失] 未找到 Kimi.exe，请确认已安装 Kimi 桌面版。' -ForegroundColor Red
    exit 1
}
Write-Host "[OK] Kimi.exe: $exe" -ForegroundColor Green

# 3) 主题文件存在性
$themes = Get-ChildItem (Join-Path $script:KdsRoot 'themes') -Filter '*.css' -ErrorAction SilentlyContinue
if (-not $themes) {
    Write-Host '[缺失] themes/ 目录下没有任何 .css 皮肤文件。' -ForegroundColor Red
    exit 1
}
Write-Host "[OK] 可用皮肤: $($themes.BaseName -join ', ')" -ForegroundColor Green

# 4) 初始化状态目录
New-Item -ItemType Directory -Force -Path $script:KdsStateDir | Out-Null
Write-Host "[OK] 状态目录: $script:KdsStateDir" -ForegroundColor Green

Write-Host ''
Write-Host '安装检查通过。使用方式：' -ForegroundColor Cyan
Write-Host '  启动换肤:  .\start-dream-skin.ps1 -Theme dream'
Write-Host '  验证截图:  .\verify-dream-skin.ps1'
Write-Host '  恢复原生:  .\restore-dream-skin.ps1'
Write-Host ''
Write-Host '提示：皮肤只注入到运行中的进程内存，不改 app.asar、不碰签名，重启即恢复。'
