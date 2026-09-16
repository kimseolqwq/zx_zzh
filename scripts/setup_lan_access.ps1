param([switch]$Elevated)

$ErrorActionPreference = "Stop"
$ruleName = "择机手机推荐系统 (TCP 8000)"
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdministrator) {
    Write-Host "Requesting administrator permission to open TCP 8000..."
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-Elevated"
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Firewall setup was cancelled or failed. Exit code: $($process.ExitCode)"
    }
    exit 0
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Direction Inbound -Action Allow -Profile Private,Public
    $existing | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -Protocol TCP -LocalPort 8000
    $existing | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress LocalSubnet
} else {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Description "Allow teammates on the same local network to access the course project on TCP 8000." `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8000 `
        -RemoteAddress LocalSubnet `
        -Profile Private,Public | Out-Null
}

$configuration = Get-NetIPConfiguration |
    Where-Object { $_.NetAdapter.Status -eq "Up" -and $_.IPv4DefaultGateway -and $_.IPv4Address } |
    Select-Object -First 1
$address = $configuration.IPv4Address.IPAddress

Write-Host ""
Write-Host "LAN firewall access is ready." -ForegroundColor Green
Write-Host "Rule: TCP 8000, inbound, local subnet only."
if ($address) {
    Write-Host "Teammate URL: http://${address}:8000" -ForegroundColor Cyan
} else {
    Write-Warning "No active IPv4 address was found. Reconnect Wi-Fi and run this script again."
}
Write-Warning "The current administrator password must not remain Admin@123456 on a shared network."
Read-Host "Press Enter to close"
