$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython -m venv (Join-Path $projectRoot ".venv")
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv (Join-Path $projectRoot ".venv")
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv (Join-Path $projectRoot ".venv")
    } else {
        throw "Python was not found. Install Python 3.12 first."
    }
}
& $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
Write-Host "Setup complete. Start Ollama, then run .\run.ps1"
