<#
.SYNOPSIS
    Bridge of DG-LAB 启动器（死亡搁浅 × 郊狼 3.0）

.DESCRIPTION
    自动切到项目目录、检查依赖、按参数启动 bridge。
    不传 -Arm 时是预览模式：会连设备、读血量、在设置页面显示状态，但不会输出。

.EXAMPLE
    .\start.ps1
    预览模式，通道和上限都按 config\app.json

.EXAMPLE
    .\start.ps1 -Arm -Channel B
    武装模式，只驱动 B 通道（受伤时真的输出）

.EXAMPLE
    .\start.ps1 -Arm -Channel B -HostIp 192.168.1.42
    换网络 / 手机扫码连不上时，指定二维码里要写的地址

.EXAMPLE
    .\start.ps1 -DryRun
    只检查 Python、依赖和最终命令，不启动（想先确认参数对不对时用）
#>
[CmdletBinding()]
param(
    [ValidateSet('A', 'B', 'dual')]
    [string]$Channel,

    [switch]$Arm,

    [string]$HostIp,

    [string]$Python,

    [switch]$DebugLog,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# 控制台按 UTF-8 输出：中文提示与二维码方块字才不会变乱码
try {
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    $OutputEncoding = [Text.Encoding]::UTF8
} catch { }

# 无论从哪里调用，都切到脚本所在目录（项目根）
Set-Location -LiteralPath $PSScriptRoot

function Fail($message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    Read-Host "按回车退出"
    exit 1
}

# ── 1. 找 Python ──────────────────────────────────────────────
$candidates = @()
if ($Python) { $candidates += $Python }
if ($env:BRIDGE_PYTHON) { $candidates += $env:BRIDGE_PYTHON }
$candidates += 'python'

$py = $null
foreach ($c in $candidates) {
    try {
        $cmd = Get-Command $c -ErrorAction Stop
        $null = & $cmd.Source -c "import sys" 2>&1
        if ($LASTEXITCODE -eq 0) { $py = $cmd.Source; break }
    } catch { }
}

if (-not $py) {
    Fail @"
找不到可用的 Python。
请先安装 Python 3.12，或在启用脚本时指定：.\start.ps1 -Python C:\path\to\python.exe
"@
}

# ── 2. 检查依赖 ───────────────────────────────────────────────
$null = & $py -c "import pydglab_ws, qrcode" 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail @"
缺少依赖：$py 里没有 pydglab-ws 或 qrcode。
请执行：  & "$py" -m pip install pydglab-ws qrcode
"@
}

# ── 3. 组参数 ─────────────────────────────────────────────────
$argv = @('-m', 'bridge')
if ($Arm) { $argv += '--arm' }
if ($Channel) { $argv += '--channel', $Channel }
if ($HostIp) {
    $argv += '--host-ip', $HostIp
    $env:BRIDGE_PUBLIC_HOST = $HostIp
}
if ($DebugLog -or $PSBoundParameters.ContainsKey('Verbose')) { $argv += '--verbose' }

# ── 4. 只检查不启动 ───────────────────────────────────────────
if ($DryRun) {
    $cfgPath = Join-Path $PSScriptRoot 'config\app.json'
    $addr = $null
    if ($HostIp) { $addr = $HostIp }
    elseif ($env:BRIDGE_PUBLIC_HOST) { $addr = $env:BRIDGE_PUBLIC_HOST }
    elseif (Test-Path $cfgPath) {
        # 必须显式 -Encoding UTF8：PS 5.1 默认按 ANSI 读，中文注释会让 JSON 解析失败
        try { $addr = (Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json).public_host } catch { }
    }
    if (-not $addr) { $addr = '自动探测本机 IP' }

    Write-Host ""
    Write-Host "  [DryRun] 只检查，不启动" -ForegroundColor Cyan
    Write-Host "  Python    : $py" -ForegroundColor DarkGray
    Write-Host "  工作目录  : $PSScriptRoot" -ForegroundColor DarkGray
    Write-Host "  二维码地址: $addr" -ForegroundColor DarkGray
    Write-Host "  模式      : $(if ($Arm) { '武装（会真的输出）' } else { '预览（不输出）' })" -ForegroundColor DarkGray
    Write-Host "  命令      : $py $($argv -join ' ')" -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

# ── 5. 启动 ───────────────────────────────────────────────────
Write-Host ""
if ($Arm) {
    Write-Host "  ⚠ 武装模式：受伤时会真的输出电流" -ForegroundColor Yellow
    Write-Host "    直接从最低强度试；任何时候 Ctrl+C 或关掉郊狼主机电源。" -ForegroundColor DarkGray
    Write-Host "    3 秒后启动，要取消就按 Ctrl+C…" -ForegroundColor DarkGray
    Start-Sleep -Seconds 3
} else {
    Write-Host "  预览模式：不会输出（要真的输出请加 -Arm）" -ForegroundColor DarkGray
}
Write-Host "  Python: $py" -ForegroundColor DarkGray
Write-Host "  工作目录: $PSScriptRoot" -ForegroundColor DarkGray
Write-Host ""

& $py @argv
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ""
    Write-Host "bridge 退出，返回码 $code" -ForegroundColor Red
    Read-Host "按回车退出"
}
exit $code
