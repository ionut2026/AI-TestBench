[CmdletBinding()]
param(
    [string]$GrfPath,
    [string]$Python = "python",
    [switch]$SkipService
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $true
$root = Split-Path -Parent $PSScriptRoot
$scratch = Join-Path $root "results\setup"
New-Item -ItemType Directory -Force $scratch | Out-Null
$env:TEMP = $scratch
$env:TMP = $scratch
$env:TMPDIR = $scratch
$env:PIP_CACHE_DIR = Join-Path $scratch "pip-cache"
$env:npm_config_cache = Join-Path $scratch "npm-cache"
if (-not $GrfPath) {
    $parent = Split-Path -Parent $root
    $inPlace = Join-Path $parent "general-robot-framework"
    $sibling = Join-Path $parent "GeneralRobotFramework"
    $GrfPath = if (Test-Path (Join-Path $inPlace "pyproject.toml")) { $inPlace } else { $sibling }
}
$GrfPath = (Resolve-Path $GrfPath).Path
if (-not (Test-Path (Join-Path $GrfPath "src\grf\project.py"))) {
    throw "Not a GRF checkout: $GrfPath"
}

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE" }
}

$venv = Join-Path $root ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    Invoke-Checked $Python @("-m", "venv", $venv)
}
Invoke-Checked $pythonExe @("-m", "pip", "install", "-r", (Join-Path $root "requirements-dev.lock"))
Invoke-Checked $pythonExe @("-m", "pip", "install", "--no-deps", "-e", $GrfPath)
Invoke-Checked $pythonExe @("-m", "pip", "install", "--no-deps", "-e", $root)
Invoke-Checked $pythonExe @("-m", "pip", "check")
if (-not $SkipService) {
    Invoke-Checked "npm" @("ci", "--prefix", (Join-Path $root "service"))
    Invoke-Checked "npm" @("run", "build", "--prefix", (Join-Path $root "service"))
}
Write-Output "Installed SMM with editable GRF from $GrfPath. No product environment was started."
