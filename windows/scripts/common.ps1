# ============================================================================
# common.ps1 —— 四个脚本的公共函数（被 dot-source 引用，不单独运行）
# ============================================================================

# 状态文件：记录 PID / 端口 / 主题，供 verify / restore 使用
$script:KdsStateDir  = Join-Path $env:APPDATA 'KimiDreamSkin'
$script:KdsStateFile = Join-Path $script:KdsStateDir 'state.json'
$script:KdsRoot      = Split-Path $PSScriptRoot -Parent   # windows/ 目录

function Find-KimiExe {
    # 注意：某些最小环境下 $env:ProgramFiles 可能为空，Join-Path 不能直接吃空值
    $candidates = @()
    if ($env:LOCALAPPDATA)        { $candidates += Join-Path $env:LOCALAPPDATA 'Programs\kimi-desktop\Kimi.exe' }
    if ($env:ProgramFiles)        { $candidates += Join-Path $env:ProgramFiles 'kimi-desktop\Kimi.exe' }
    if (${env:ProgramFiles(x86)}) { $candidates += Join-Path ${env:ProgramFiles(x86)} 'kimi-desktop\Kimi.exe' }
    $candidates += 'C:\Program Files\Kimi\Kimi.exe'
    $candidates = $candidates | Where-Object { $_ -and (Test-Path $_) }
    if ($candidates.Count -gt 0) { return @($candidates)[0] }  # 强制数组，单元素时避免取到字符串首字符
    # 最后尝试 PATH / 注册表卸载信息
    $uninstallRoots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($root in $uninstallRoots) {
        $hit = Get-ItemProperty $root -ErrorAction SilentlyContinue |
               Where-Object { $_.DisplayName -match 'Kimi' -and $_.InstallLocation } |
               Select-Object -First 1
        if ($hit) {
            $exe = Join-Path $hit.InstallLocation 'Kimi.exe'
            if (Test-Path $exe) { return $exe }
        }
    }
    return $null
}

function Get-NodeVersion {
    try {
        $v = (& node -v 2>$null) -replace '^v', ''
        return [version]$v
    } catch { return $null }
}

# 申请一个 127.0.0.1 上的空闲端口
function Get-FreePort {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

# Kimi 是否在运行；返回进程对象数组（不含我们自己记录过的已退出 PID）
function Get-KimiProcesses {
    Get-Process -Name 'Kimi' -ErrorAction SilentlyContinue
}

function Read-KdsState {
    if (Test-Path $script:KdsStateFile) {
        try { return (Get-Content $script:KdsStateFile -Raw | ConvertFrom-Json) } catch { return $null }
    }
    return $null
}

function Write-KdsState($obj) {
    New-Item -ItemType Directory -Force -Path $script:KdsStateDir | Out-Null
    $obj | ConvertTo-Json | Set-Content -Encoding UTF8 $script:KdsStateFile
}

function Clear-KdsState {
    if (Test-Path $script:KdsStateFile) { Remove-Item $script:KdsStateFile -Force }
}
