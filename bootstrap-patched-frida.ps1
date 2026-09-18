# bootstrap-patched-frida.ps1
# ---------------------------------------------------------------------------
# Fresh-machine setup for the anti-detection-patched Frida.
#
# Clones frida.git at the pinned base commit, initialises submodules, applies
# the patches in frida-patches\, then builds the core and installs the wheel
# into this project's .venv (via rebuild-patched-frida.ps1 -BuildCore).
#
# After a normal `git pull` of THIS repo, this is the one command needed to get
# a working patched Frida — you do not commit the Frida source, only the patches.
#
# Usage:
#   .\bootstrap-patched-frida.ps1                 # clone into ..\ba-helper\frida-patched
#   .\bootstrap-patched-frida.ps1 -FridaDir D:\src\frida-patched
#   .\bootstrap-patched-frida.ps1 -PatchOnly      # clone + apply patches, skip build
# ---------------------------------------------------------------------------
[CmdletBinding()]
param(
    [string]$FridaDir = (Join-Path $PSScriptRoot "..\ba-helper\frida-patched"),
    [string]$Base     = "b42f672daabcb8a480e9d79bbe25ceba803d0007",
    [switch]$PatchOnly
)

$ErrorActionPreference = "Stop"
$Root      = $PSScriptRoot
$Patches   = Join-Path $Root "frida-patches"

function Invoke-Git { & git @args; if ($LASTEXITCODE -ne 0) { throw "git $($args -join ' ') failed" } }

# 1. Clone (or reuse) frida.git at the base commit
if (-not (Test-Path (Join-Path $FridaDir ".git"))) {
    Write-Host "==> Cloning frida.git into $FridaDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path (Split-Path $FridaDir -Parent) | Out-Null
    Invoke-Git clone https://github.com/frida/frida.git $FridaDir
} else {
    Write-Host "==> Reusing existing clone at $FridaDir" -ForegroundColor Cyan
}

Push-Location $FridaDir
try {
    Invoke-Git fetch --tags origin
    Invoke-Git checkout $Base
    Invoke-Git submodule update --init --recursive

    # 2. Apply the patch set. `git apply --3way` tolerates a re-run (already applied).
    $map = @{
        "01-frida-core.patch"        = "subprojects/frida-core"
        "02-frida-gum.patch"         = "subprojects/frida-core/subprojects/frida-gum"
        "03-releng.patch"            = "releng"
        "04-frida-core-releng.patch" = "subprojects/frida-core/releng"
    }
    foreach ($name in ($map.Keys | Sort-Object)) {
        $patch = Join-Path $Patches $name
        $sub   = $map[$name]
        Write-Host "==> Applying $name -> $sub" -ForegroundColor Cyan
        & git -C $sub apply --reverse --check $patch 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    already applied, skipping." -ForegroundColor DarkGray
        } else {
            Invoke-Git -C $sub apply --whitespace=nowarn $patch
        }
    }
}
finally { Pop-Location }

if ($PatchOnly) {
    Write-Host "Patches applied. Skipping build (-PatchOnly)." -ForegroundColor Green
    return
}

# 3. Build core + install wheel into .venv
Write-Host "==> Building patched Frida and installing into .venv" -ForegroundColor Cyan
& (Join-Path $Root "rebuild-patched-frida.ps1") -BuildCore
