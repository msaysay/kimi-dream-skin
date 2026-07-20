# ============================================================================
# start-dream-skin.ps1 —— 以调试端口启动 Kimi 并注入皮肤
#
# 安全边界：
#   - 调试端口只绑定 127.0.0.1
#   - 绝不强制结束正在运行的 Kimi；检测到在运行时会提示用户手动退出
#   - PID / 端口写入 %APPDATA%\KimiDreamSkin\state.json 供 verify/restore 使用
# ============================================================================
[CmdletBinding()]
param(
    [ValidateSet('diva', 'rose', 'caishen', 'blackgold', 'scifi', 'gothic',
        'dunhuang', 'qinghua', 'jiangshan', 'moyun',
        'diva-surprise', 'rose-surprise', 'caishen-surprise', 'blackgold-surprise', 'scifi-surprise',
        'gothic-surprise', 'dunhuang-surprise', 'qinghua-surprise', 'jiangshan-surprise', 'moyun-surprise')]
    [string]$Theme = 'jiangshan',
    [switch]$Watch        # 驻留模式：新窗口/跳转后自动补注入（前台运行，Ctrl+C 停止）
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$exe = Find-KimiExe
if (-not $exe) { Write-Host '未找到 Kimi.exe，请先运行 install-dream-skin.ps1 检查环境。' -ForegroundColor Red; exit 1 }

# --- 单实例保护：Kimi 已在运行时，调试参数不会生效（会转发给旧实例），必须先退出 ---
$running = Get-KimiProcesses
if ($running) {
    Write-Host '检测到 Kimi 正在运行。' -ForegroundColor Yellow
    Write-Host '皮肤需要在启动时附加调试端口，请先在 Kimi 内保存工作并完全退出（托盘图标也要退出），然后重新运行本脚本。'
    Write-Host '本工具绝不会替你强制结束 Kimi 进程。'
    exit 2
}

$port = Get-FreePort
Write-Host "启动 Kimi（调试端口 127.0.0.1:$port，主题 $Theme）..."
$proc = Start-Process -FilePath $exe -PassThru -ArgumentList @(
    "--remote-debugging-port=$port",
    '--remote-debugging-address=127.0.0.1'
)

$state = [ordered]@{
    pid       = $proc.Id
    port      = $port
    theme     = $Theme
    exe       = $exe
    startedAt = (Get-Date).ToString('o')
}
Write-KdsState $state
Write-Host "Kimi 已启动 (PID $($proc.Id))，等待调试端口就绪..."

# 等待 /json 就绪后注入
$engine = Join-Path $script:KdsRoot 'engine'
if ($Watch) {
    Write-Host '驻留模式：保持本窗口开启，Ctrl+C 停止自动补注入。'
    & node (Join-Path $engine 'inject.mjs') --port $port --theme $Theme --watch
} else {
    & node (Join-Path $engine 'inject.mjs') --port $port --theme $Theme
    if ($LASTEXITCODE -ne 0) { Write-Host '注入失败，可稍后重跑 verify-dream-skin.ps1 诊断。' -ForegroundColor Red; exit 1 }
    Write-Host ''
    Write-Host "皮肤 [$Theme] 已生效。验证：.\verify-dream-skin.ps1  恢复：.\restore-dream-skin.ps1" -ForegroundColor Green
}
