# rebuild-patched-frida.ps1
# ---------------------------------------------------------------------------
# Rebuilds the anti-detection–patched Frida from source and installs it into
# this project's .venv, replacing the stock pip `frida`.
#
# The patched Frida SOURCE lives in the sibling ba-helper checkout:
#     ..\ba-helper\frida-patched   (a clone of frida/frida.git + local patches)
#
# Two stages:
#   1. (optional, slow) Build frida-core / frida-python from C/Vala source.
#      Only needed after changing the Frida source. Pass -BuildCore to run it.
#   2. (fast) Package the prebuilt _frida.pyd into a wheel and pip-install it
#      into .venv. This is the part that "wires" the patched Frida into the app.
#
# Usage:
#   .\rebuild-patched-frida.ps1              # repackage + install (source already built)
#   .\rebuild-patched-frida.ps1 -BuildCore   # full rebuild from source, then install
#
# Note on version: the source is currently configured with frida_version=0.0.0,
# so `frida.__version__` reports "0.0.0" (cosmetic — only affects --version
# output). The wheel's package metadata is stamped with $FridaVersion below.
# To make __version__ correct, rebuild the core with -Dfrida_version=<x> set.
# ---------------------------------------------------------------------------
[CmdletBinding()]
param(
    [switch]$BuildCore,
    [string]$FridaVersion = "17.18.1"
)

$ErrorActionPreference = "Stop"

$Root       = $PSScriptRoot
$FridaSrc   = Join-Path $Root "..\ba-helper\frida-patched" | Resolve-Path | Select-Object -ExpandProperty Path
$BuildDir   = Join-Path $FridaSrc "build"
$PyDir      = Join-Path $BuildDir "subprojects\frida-python\frida"
$Extension  = Join-Path $PyDir "_frida.pyd"
$VenvPy     = Join-Path $Root ".venv\Scripts\python.exe"
# Python used to drive the Frida meson build (not the venv — Frida's build
# scripts run under whichever python has meson/lief available).
$BuildPy    = "C:\ProgramData\Anaconda3\python.exe"

if (-not (Test-Path $VenvPy)) { throw "venv python not found: $VenvPy (create .venv first)" }

if ($BuildCore) {
    Write-Host "==> Building patched Frida core from source (this is slow)..." -ForegroundColor Cyan
    & $BuildPy -c "import sys; sys.path.insert(0, r'$FridaSrc'); from releng.meson_make import main; main()" $FridaSrc ".\build"
    if ($LASTEXITCODE -ne 0) { throw "Frida core build failed" }
}

if (-not (Test-Path $Extension)) {
    throw "Prebuilt extension missing: $Extension`nRun with -BuildCore first."
}

Write-Host "==> Packaging wheel from $Extension" -ForegroundColor Cyan
$WheelOut = Join-Path $env:TEMP ("frida-wheel-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $WheelOut | Out-Null
$env:FRIDA_EXTENSION = $Extension
$env:FRIDA_VERSION   = $FridaVersion
Push-Location (Join-Path $FridaSrc "subprojects\frida-python")
try {
    & $VenvPy -m pip wheel . -w $WheelOut --no-build-isolation --no-deps
    if ($LASTEXITCODE -ne 0) { throw "wheel build failed" }
}
finally {
    Pop-Location
    Remove-Item Env:\FRIDA_EXTENSION, Env:\FRIDA_VERSION -ErrorAction SilentlyContinue
}

$Wheel = Get-ChildItem -Path $WheelOut -Filter "frida-*.whl" | Select-Object -First 1
if (-not $Wheel) { throw "no wheel produced in $WheelOut" }

Write-Host "==> Installing $($Wheel.Name) into .venv" -ForegroundColor Cyan
& $VenvPy -m pip install --force-reinstall --no-deps $Wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

Write-Host "==> Verifying" -ForegroundColor Cyan
& $VenvPy -c "import frida; d=frida.get_local_device(); print('OK  frida', frida.__version__, '  device:', d.name)"

Write-Host "Done. Patched Frida is now active in .venv." -ForegroundColor Green
