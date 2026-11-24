# Allow inbound TCP 5678 for Bridge of DG-LAB.
# ASCII only on purpose: Windows PowerShell 5.1 reads non-BOM scripts as ANSI.
# Run:
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\path\to\bridge_of_dg_lab\tools\allow_firewall.ps1

$ErrorActionPreference = 'Stop'
$ruleName = 'Bridge of DG-LAB'
$port = 5678

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host 'Requesting administrator privileges (UAC prompt)...'
    $argumentList = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath)
    )
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $argumentList
    exit 0
}

Write-Host ''
Write-Host "Adding inbound rule: $ruleName (TCP $port, all profiles)"
netsh advfirewall firewall delete rule name="$ruleName" | Out-Null
netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow protocol=TCP localport=$port profile=any

Write-Host ''
Write-Host '--- current rule ---'
netsh advfirewall firewall show rule name="$ruleName"

Write-Host ''
Write-Host 'Done. You can close this window or press Enter.'
Read-Host | Out-Null
