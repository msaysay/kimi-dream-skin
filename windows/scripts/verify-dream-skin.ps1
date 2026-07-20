# ============================================================================
# verify-dream-skin.ps1 —— 验证皮肤是否生效并截图取证
# 读取 state.json 中的端口，检查注入标记、页面标题/URL，截图存入 screenshots/
# ============================================================================
[CmdletBinding()]
param(
    [string]$Out   # 自定义截图输出路径（默认 screenshots/verify-<主题>-<时间>.png）
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$state = Read-KdsState
if (-not $state) {
    Write-Host '没有找到状态文件，请先运行 start-dream-skin.ps1。' -ForegroundColor Red
    exit 1
}

if (-not $Out) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Out = Join-Path (Split-Path $script:KdsRoot -Parent) "screenshots\verify-$($state.theme)-$stamp.png"
}

Write-Host "连接 127.0.0.1:$($state.port) 验证皮肤 [$($state.theme)] ..."
& node (Join-Path $script:KdsRoot 'engine\verify.mjs') --port $state.port --out $Out
if ($LASTEXITCODE -eq 0) {
    Write-Host "验证通过，截图已保存: $Out" -ForegroundColor Green
} else {
    Write-Host '验证未通过：未检测到注入标记。请确认 Kimi 是通过 start-dream-skin.ps1 启动的。' -ForegroundColor Yellow
    exit $LASTEXITCODE
}
