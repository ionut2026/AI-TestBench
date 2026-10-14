#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Python = "python"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $root ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$savedEnvironment = @{}

function Invoke-CheckedPython {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml") -PathType Leaf)) {
        throw "Run the setup script from a complete GeneralRobotFramework checkout."
    }
    if (Test-Path -LiteralPath $venv) {
        if (-not (Test-Path -LiteralPath (Join-Path $venv "pyvenv.cfg") -PathType Leaf) -or
            -not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "Existing .venv is not a complete Windows Python virtual environment. It was left unchanged."
        }
        $interpreter = $venvPython
    }
    else {
        if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
            throw "Python was not found. Install Python 3.11 or newer, or pass -Python with its executable path."
        }
        $interpreter = $Python
    }
    Invoke-CheckedPython $interpreter @("-c", "import sys; print('Python:', sys.version.split()[0]); sys.exit('Python 3.11 or newer is required.') if sys.version_info < (3, 11) else None")

    $temporary = Join-Path $root "results\setup\tmp"
    $cache = Join-Path $root "results\pip-cache"
    New-Item -ItemType Directory -Force $temporary, $cache | Out-Null
    foreach ($name in @("TEMP", "TMP", "PIP_CACHE_DIR")) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    $env:TEMP = $temporary
    $env:TMP = $temporary
    $env:PIP_CACHE_DIR = $cache

    Push-Location $root
    try {
        if (-not (Test-Path -LiteralPath $venv)) {
            Write-Host "Creating the project's Python environment in $venv"
            Invoke-CheckedPython $interpreter @("-m", "venv", $venv)
        }
        else {
            Write-Host "Reusing the project's Python environment in $venv"
        }
        Invoke-CheckedPython $venvPython @("-c", "import pathlib, sys; expected = pathlib.Path(sys.argv[1]).resolve(); sys.exit('The interpreter is not using the expected virtual environment; refusing installation.') if pathlib.Path(sys.prefix).resolve() != expected or sys.prefix == sys.base_prefix else None", $venv)
        Write-Host "Installing GRF, Robot Framework and development tools (internet access may be needed)..."
        Invoke-CheckedPython $venvPython @("-m", "pip", "install", "-e", ".[dev]")
        Invoke-CheckedPython $venvPython @("-m", "pip", "check")
        Invoke-CheckedPython $venvPython @("-c", "from importlib.metadata import version; import grf, robot; print('GRF:', version('general-robot-framework')); print('Robot Framework:', robot.__version__); print('Robot Framework location:', robot.__file__)")
    }
    finally {
        Pop-Location
    }
    Write-Host "Setup complete. No product tests, services or instrument operations were started."
    Write-Host "Next: follow docs\GETTING-STARTED.md to create and run a practice product."
}
catch {
    Write-Error "GRF setup failed: $($_.Exception.Message)" -ErrorAction Continue
    exit 1
}
finally {
    foreach ($name in $savedEnvironment.Keys) {
        if ($null -eq $savedEnvironment[$name]) {
            if (Test-Path -LiteralPath "Env:$name") {
                Remove-Item -LiteralPath "Env:$name"
            }
        }
        else {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
        }
    }
}
