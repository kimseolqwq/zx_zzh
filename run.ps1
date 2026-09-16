$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$lanRuleName = "择机手机推荐系统 (TCP 8000)"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment not found. Run scripts\setup.ps1 first."
}

try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3
} catch {
    throw "Ollama is not running. Start Ollama, then run this script again."
}

try {
    $healthResponse = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
    if ($healthResponse.status -eq "ok") {
        $lanAddress = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "WLAN" -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "169.254.*" } |
            Select-Object -First 1 -ExpandProperty IPAddress
        Write-Host "The website is already running locally: http://127.0.0.1:8000"
        if ($lanAddress) {
            Write-Host "LAN address: http://${lanAddress}:8000"
            if (-not (Get-NetFirewallRule -DisplayName $lanRuleName -ErrorAction SilentlyContinue)) {
                Write-Warning "LAN firewall rule is missing. Run scripts\setup_lan_access.ps1 once as administrator."
            }
        }
        exit 0
    }
} catch {
    # Port 8000 is not serving this application, so start it below.
}

Set-Location -LiteralPath $projectRoot
$warmupScript = Join-Path $projectRoot "scripts\warm_models.py"
Write-Host "Warming the three local models in parallel..."
& $pythonPath $warmupScript
if ($LASTEXITCODE -ne 0) {
    Write-Warning "One or more models could not be warmed. The website will still start with timeout fallback enabled."
}
$lanAddress = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "WLAN" -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1 -ExpandProperty IPAddress
Write-Host "Starting website locally at http://127.0.0.1:8000"
if ($lanAddress) {
    Write-Host "Other devices on the same LAN can try: http://${lanAddress}:8000"
    if (-not (Get-NetFirewallRule -DisplayName $lanRuleName -ErrorAction SilentlyContinue)) {
        Write-Warning "LAN firewall rule is missing. Run scripts\setup_lan_access.ps1 once as administrator."
    }
}
Write-Host "Keep this window open. Press Ctrl+C to stop the website."
& $pythonPath -m uvicorn app.main:app --host 0.0.0.0 --port 8000
