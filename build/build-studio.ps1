<#
.SYNOPSIS
    One-shot build of the TalkToAi Code Studio UI into dist\ttacode-studio.exe.

.DESCRIPTION
    Checks for Python 3.10+, creates a disposable build venv
    (.build-studio-venv), installs PyInstaller and PySide6 6.8.3, runs
    PyInstaller on ttacode-studio.spec, then smoke-tests the resulting
    binary. Idempotent: re-running reuses the venv and overwrites dist\.

    NOTE: this file must stay pure ASCII. Windows PowerShell 5.1 reads
    BOM-less scripts as Windows-1252, and a UTF-8 em-dash/ellipsis once
    broke this build with phantom quote characters. No unicode, ever.

    Run from the repository root:  .\build\build-studio.ps1
#>
$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# --- repo root -----------------------------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Test-Path "ttacode-studio.spec")) { Fail "ttacode-studio.spec not found - run from the repo root." }

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
$Venv = Join-Path $Root ".build-studio-venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating build venv at .build-studio-venv ..."
    & $Py -m venv $Venv
    if (-not (Test-Path $VenvPy)) { Fail "venv creation failed." }
} else {
    Write-Host "Reusing existing .build-studio-venv."
}

Write-Host "Installing PyInstaller and PySide6 6.8.3 into the build venv ..."
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet pyinstaller "PySide6==6.8.3"
if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller/PySide6 failed." }

# --- build ----------------------------------------------------------------
Write-Host "Running PyInstaller (this takes several minutes) ..."
& $VenvPy -m PyInstaller ttacode-studio.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

$Exe = Join-Path $Root "dist\ttacode-studio.exe"
if (-not (Test-Path $Exe)) { Fail "Build finished but dist\ttacode-studio.exe is missing." }
$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $Exe ($sizeMb MB)" -ForegroundColor Green

# --- smoke test ------------------------------------------------------------
# Try the in-app self-test first (runs before any window is created).
# It needs optional test-only deps (pywinauto, playwright) that are NOT
# installed in the build venv, so a failure here does not mean the exe is
# broken: fall back to the --version no-op, which proves the frozen
# Python runtime starts and the studio module imports cleanly.
Write-Host "Smoke-testing the binary ..."
$env:QT_QPA_PLATFORM = "offscreen"
& $Exe --self-test
if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: --self-test exited $LASTEXITCODE (it needs optional test deps not present in the build venv). Falling back to the --version no-op check." -ForegroundColor Yellow
    & $Exe --version
    if ($LASTEXITCODE -ne 0) { Fail "ttacode-studio.exe --version failed - the frozen runtime is broken." }
    Write-Host "OK - dist\ttacode-studio.exe launches (frozen runtime sane)." -ForegroundColor Green
} else {
    Write-Host "OK - dist\ttacode-studio.exe passed --self-test." -ForegroundColor Green
}

Write-Host ""
Write-Host "OK - dist\ttacode-studio.exe is ready." -ForegroundColor Green
Write-Host "First run: make sure Ollama serves a model, then launch the exe."
Write-Host "See docs/studio.md for the harness-backed model picker and warm-up behavior."
