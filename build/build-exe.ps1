<#
.SYNOPSIS
    One-shot build of the TTACode headless CLI into dist\ttacode-cli.exe.

.DESCRIPTION
    Checks for Python 3.10+, creates a disposable build venv
    (.build-venv), installs PyInstaller, runs PyInstaller on
    ttacode.spec, then smoke-tests the resulting binary.
    Idempotent: re-running reuses the venv and overwrites dist\.

    Run from the repository root:  .\build\build-exe.ps1
#>
$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# --- repo root -----------------------------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Test-Path "ttacode.spec")) { Fail "ttacode.spec not found - run from the repo root." }

# --- Python 3.10+ ---------------------------------------------------------
$Py = $null
foreach ($cmd in @("py", "python", "python3")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) { $Py = $found.Source; break }
}
if (-not $Py) { Fail "No Python found. Install Python 3.10+ from https://www.python.org/downloads/ and re-run." }

$verOut = & $Py -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if (-not $verOut) { Fail "Could not query the Python version." }
$ver = [version]$verOut
if ($ver -lt [version]"3.10") { Fail "Python 3.10+ required, found $verOut." }
Write-Host "Using Python $verOut ($Py)"

# --- build venv -----------------------------------------------------------
$Venv = Join-Path $Root ".build-venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating build venv at .build-venv ..."
    & $Py -m venv $Venv
    if (-not (Test-Path $VenvPy)) { Fail "venv creation failed." }
} else {
    Write-Host "Reusing existing .build-venv."
}

Write-Host "Installing PyInstaller into the build venv ..."
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller failed." }

# --- build ----------------------------------------------------------------
Write-Host "Running PyInstaller (this takes a few minutes) ..."
& $VenvPy -m PyInstaller ttacode.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

$Exe = Join-Path $Root "dist\ttacode-cli.exe"
if (-not (Test-Path $Exe)) { Fail "Build finished but dist\ttacode-cli.exe is missing." }
$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $Exe ($sizeMb MB)" -ForegroundColor Green

# --- smoke test ------------------------------------------------------------
Write-Host "Smoke-testing the binary ..."
& $Exe --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "ttacode-cli.exe --help failed." }
& $Exe models list
if ($LASTEXITCODE -ne 0) { Fail "ttacode-cli.exe models list failed." }

Write-Host ""
Write-Host "OK - dist\ttacode-cli.exe is ready." -ForegroundColor Green
Write-Host "Next: ttacode-cli.exe models list   (or: ttacode-cli.exe chat)"
Write-Host "Then: ttacode-cli.exe run --headless `"your task`""
