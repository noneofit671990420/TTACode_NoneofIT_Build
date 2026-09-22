<#
.SYNOPSIS
    One-shot build of the TTACode desktop chat GUI into dist\ttacode.exe.

.DESCRIPTION
    Checks for a compatible Python (3.10 through 3.13 - PySide6 6.8.3 has
    no wheels for Python 3.14), creates a disposable build venv
    (.build-gui-venv), installs PyInstaller and PySide6 6.8.3, runs
    PyInstaller on ttacode-gui.spec, then smoke-tests the resulting
    binary.

    This is the average-user launcher: a windowed (console-free) app.
    The headless CLI ships separately as ttacode-cli.exe.

    NOTE: this file must stay pure ASCII. Windows PowerShell 5.1 reads
    BOM-less scripts as Windows-1252, and a UTF-8 em-dash/ellipsis once
    broke a build with phantom quote characters. No unicode, ever.

    Run from the repository root:  .\build\build-gui.ps1
#>
$ErrorActionPreference = "Stop"

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# --- repo root -----------------------------------------------------------
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Test-Path "ttacode-gui.spec")) { Fail "ttacode-gui.spec not found - run from the repo root." }

# --- Python 3.10 through 3.13 ------------------------------------------------
# PySide6 6.8.3 ships no wheels for Python 3.14, so the interpreter must be
# in [3.10, 3.14). Preference order: the actions/setup-python pin
# ($env:pythonLocation), then the newest compatible interpreter via the
# py launcher, then whatever py/python/python3 resolves to on PATH.
function Get-PyVersion {
    param([string]$exe, [string[]]$argv = @())
    try {
        $o = & $exe @argv -c "import sys; print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))" 2>$null
        if ($o) { return [version]$o.Trim() }
    } catch { }
    return $null
}
function Test-PyOk($v) { return ($v -and $v -ge [version]"3.10" -and $v -lt [version]"3.14") }

$Py = $null
$PyArgv = @()
if ($env:pythonLocation) {
    $pin = Join-Path $env:pythonLocation "python.exe"
    if ((Test-Path $pin) -and (Test-PyOk (Get-PyVersion $pin))) { $Py = $pin }
}
if (-not $Py) {
    $launcher = (Get-Command "py" -ErrorAction SilentlyContinue)
    if ($launcher) {
        foreach ($mm in @("3.13", "3.12", "3.11", "3.10")) {
            $argv = @("-$mm")
            if (Test-PyOk (Get-PyVersion $launcher.Source $argv)) { $Py = $launcher.Source; $PyArgv = $argv; break }
        }
    }
}
if (-not $Py) {
    foreach ($cmd in @("py", "python", "python3")) {
        $found = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($found -and (Test-PyOk (Get-PyVersion $found.Source))) { $Py = $found.Source; break }
    }
}
if (-not $Py) { Fail "Need a 64-bit Python 3.10 through 3.13. PySide6 6.8.3 has no wheels for Python 3.14+. Install Python 3.12 from https://www.python.org/downloads/ and re-run." }

$verOut = (Get-PyVersion $Py $PyArgv).ToString()
Write-Host "Using Python $verOut ($Py $PyArgv)"

# --- build venv -----------------------------------------------------------
$Venv = Join-Path $Root ".build-gui-venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
$venvVer = $null
if (Test-Path $VenvPy) { $venvVer = Get-PyVersion $VenvPy }
if (-not (Test-PyOk $venvVer)) {
    if (Test-Path $Venv) {
        Write-Host "Removing stale or incompatible build venv ..."
        Remove-Item -Recurse -Force $Venv
    }
    Write-Host "Creating build venv at .build-gui-venv ..."
    & $Py @PyArgv -m venv $Venv
    if (-not (Test-Path $VenvPy)) { Fail "venv creation failed." }
} else {
    Write-Host "Reusing existing .build-gui-venv."
}

Write-Host "Installing PyInstaller and PySide6 6.8.3 into the build venv ..."
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet pyinstaller "PySide6==6.8.3"
if ($LASTEXITCODE -ne 0) { Fail "pip install pyinstaller/PySide6 failed." }

# --- build ----------------------------------------------------------------
Write-Host "Running PyInstaller (this takes several minutes) ..."
& $VenvPy -m PyInstaller ttacode-gui.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

$Exe = Join-Path $Root "dist\ttacode.exe"
if (-not (Test-Path $Exe)) { Fail "Build finished but dist\ttacode.exe is missing." }
$sizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built: $Exe ($sizeMb MB)" -ForegroundColor Green

# --- smoke test ------------------------------------------------------------
# --version exits before Qt is imported, so this proves the frozen
# runtime starts and the app module imports cleanly without opening
# a window.
Write-Host "Smoke-testing the binary ..."
& $Exe --version
if ($LASTEXITCODE -ne 0) { Fail "ttacode.exe --version failed - the frozen runtime is broken." }

Write-Host ""
Write-Host "OK - dist\ttacode.exe is ready." -ForegroundColor Green
Write-Host "First run: make sure Ollama serves a model, then double-click the exe."
