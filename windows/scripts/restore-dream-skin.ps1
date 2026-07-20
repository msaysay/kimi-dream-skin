# ============================================================================
# restore-dream-skin.ps1 —— 恢复原生外观
#
# 皮肤只存在于运行进程的内存中，因此"恢复"= 移除当前注入 + 提示重启。
# 本脚本不会强制结束用户的 Kimi 进程；是否重启由用户决定。
# ============================================================================
[CmdletBinding()]
param(
    [switch]$RestartKimi   # 显式指定后，才会温和地关闭由本工具启动的那个 Kimi 实例并以正常方式重启
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')

$state = Read-KdsState

# 1) 如果调试端口还活着，先在线移除 <style>/<meta>，立即恢复原生外观
if ($state -and $state.port) {
    Write-Host "尝试连接 127.0.0.1:$($state.port) 在线移除皮肤..."
    & node (Join-Path $script:KdsRoot 'engine\inject.mjs') --port $state.port --remove
}

# 2) 可选：只关闭"由本工具启动的"那个实例（按 state.json 里的 PID 精确匹配，绝不按进程名批量杀）
if ($RestartKimi -and $state -and $state.pid) {
    $proc = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -eq 'Kimi') {
        Write-Host "正在关闭由本工具启动的 Kimi 实例 (PID $($state.pid))..."
        $proc.CloseMainWindow() | Out-Null
        if (-not $proc.WaitForExit(10000)) {
            Write-Host '窗口未在 10 秒内退出，请手动关闭 Kimi。未强制结束进程。' -ForegroundColor Yellow
        } else {
            Write-Host '已退出。以原生方式重新启动 Kimi...'
            Start-Process -FilePath $state.exe
        }
    } else {
        Write-Host 'state.json 中的进程已不存在，无需关闭。'
    }
}

Clear-KdsState
Write-Host ''
Write-Host '已恢复。皮肤本就只存在于内存中——下次以正常方式启动 Kimi 即为 100% 原生。' -ForegroundColor Green
