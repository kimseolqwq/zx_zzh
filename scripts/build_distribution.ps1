param(
    [string]$Version = "v1.7"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$workRoot = Join-Path $projectRoot "work"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stageRoot = Join-Path $workRoot "distribution-$Version-$stamp"
$packageName = "phone-recommendation-system-$Version"
$packageRoot = Join-Path $stageRoot $packageName
$zipPath = Join-Path $projectRoot "outputs\$packageName.zip"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Python virtual environment not found."
}

New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

foreach ($directory in @("app", "config", "docs", "scripts", "tests")) {
    $sourceDirectory = Join-Path $projectRoot $directory
    $targetDirectory = Join-Path $packageRoot $directory
    New-Item -ItemType Directory -Force -Path $targetDirectory | Out-Null
    Get-ChildItem -LiteralPath $sourceDirectory -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $targetDirectory -Recurse -Force
    }
}

foreach ($file in @(".env.example", ".gitignore", "pytest.ini", "README.md", "requirements.txt", "run.ps1")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $packageRoot
}

$packageData = Join-Path $packageRoot "data"
$packageArtifacts = Join-Path $packageRoot "artifacts"
New-Item -ItemType Directory -Force -Path $packageData, $packageArtifacts | Out-Null

& $pythonPath (Join-Path $projectRoot "scripts\create_distribution_db.py") (Join-Path $packageData "phone_recommender.db") --include-reviewed-prices

foreach ($file in @(
    "outputs\project-report-v1.7.md",
    "outputs\data-collection-report-v1.7.md",
    "outputs\defense-demo-script-v1.7.md",
    "outputs\phone-recommendation-defense-v1.7.pptx",
    "outputs\official-data-gap-queue-v1.7.csv",
    "outputs\market-price-gap-queue-v1.7.csv",
    "outputs\benchmark\benchmark-report.md",
    "outputs\benchmark\benchmark-results.csv",
    "outputs\benchmark\human-rating-template.csv",
    "outputs\benchmark\constraint-accuracy.svg"
)) {
    $source = Join-Path $projectRoot $file
    if (Test-Path -LiteralPath $source) {
        $relativeName = $file -replace "[\\/]", "__"
        Copy-Item -LiteralPath $source -Destination (Join-Path $packageArtifacts $relativeName)
    }
}

$cacheDirectories = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -Directory | Where-Object Name -eq "__pycache__")
foreach ($cacheDirectory in $cacheDirectories) {
    Remove-Item -LiteralPath $cacheDirectory.FullName -Recurse -Force
}
$compiledFiles = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -File | Where-Object Extension -in ".pyc", ".pyo")
foreach ($compiledFile in $compiledFiles) {
    Remove-Item -LiteralPath $compiledFile.FullName -Force
}

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -LiteralPath $packageRoot -DestinationPath $zipPath -CompressionLevel Optimal

$hash = Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
Write-Host "Package: $zipPath"
Write-Host "SHA256: $($hash.Hash)"
Write-Host "Staging: $packageRoot"
