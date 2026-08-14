# build_shittim_exe.ps1 - package the one-click capture as a standalone .exe.
#
#   .\build_shittim_exe.ps1
#
# Produces dist\ba-shittim-capture.exe: a single file with Python, frida, the compiled Frida
# agents and the exporter inside. Drop it in any folder, run it, start the game; the capture
# and the import file land next to the exe.
#
# The agents must be built first (they are git-ignored, so a fresh clone has none):
#   cd frida\agent
#   npm install
#   npm run build:capture
#   npm run build:refresh
#
# The refresh agent is what lets it fetch your ID card without you opening the friend screen.
# Without it the exe still works, but asks you to open that screen.

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$venv = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $venv)) {
    Write-Host "No .venv found. Create one first:" -ForegroundColor Red
    Write-Host "  py -3.12 -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\python -m pip install frida-tools" -ForegroundColor Yellow
    exit 1
}

$hook = Join-Path $root 'frida\agent\_capture.js'
if (-not (Test-Path $hook)) {
    Write-Host "Frida agent not built: frida\agent\_capture.js is missing." -ForegroundColor Red
    Write-Host "  cd frida\agent; npm install; npm run build:capture; npm run build:refresh" -ForegroundColor Yellow
    exit 1
}
$refresh = Join-Path $root 'frida\agent\_capture_refresh.js'
if (-not (Test-Path $refresh)) {
    Write-Host "Note: _capture_refresh.js missing - the exe will ask you to open the friend" -ForegroundColor Yellow
    Write-Host "      screen for the ID card instead of fetching it. Run npm run build:refresh." -ForegroundColor Yellow
}

Write-Host "[1/3] ensuring pyinstaller is present..." -ForegroundColor Cyan
& $venv -m pip install --quiet --disable-pip-version-check pyinstaller

Write-Host "[2/3] cleaning previous build..." -ForegroundColor Cyan
foreach ($d in 'build', 'dist') {
    $p = Join-Path $root $d
    if (Test-Path $p) { Remove-Item $p -Recurse -Force }
}

# --add-data pairs are <source>;<destination inside the bundle>. The destinations mirror the
# repo layout because the script resolves them with resource_path(), which joins onto
# PyInstaller's temp extraction dir at runtime and onto the repo root otherwise.
Write-Host "[3/3] building..." -ForegroundColor Cyan
$dataArgs = @(
    "--add-data", "$hook;frida/agent",
    "--add-data", "$(Join-Path $root 'frida\capture.py');frida",
    "--add-data", "$(Join-Path $root 'exporters\shittim.py');exporters",
    "--add-data", "$(Join-Path $root 'ba_codec.py');."
)
if (Test-Path $refresh) { $dataArgs += @("--add-data", "$refresh;frida/agent") }

# capture.py and ba_codec.py ride along as data, so PyInstaller never analyses their imports
# and would leave cryptography out - ba_codec imports it at module level and the capture dies
# on the first packet without it.
$dataArgs += @("--collect-all", "cryptography")

& $venv -m PyInstaller `
    --onefile `
    --console `
    --name ba-shittim-capture `
    --distpath (Join-Path $root 'dist') `
    --workpath (Join-Path $root 'build') `
    --specpath (Join-Path $root 'build') `
    @dataArgs `
    --clean `
    --noconfirm `
    (Join-Path $root 'shittim_capture.py')

$exe = Join-Path $root 'dist\ba-shittim-capture.exe'
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Built $exe ($mb MB)" -ForegroundColor Green
    Write-Host "Drop it anywhere, run it, then start Blue Archive. Output lands beside the exe."
} else {
    Write-Host "Build finished but the exe is missing - check the output above." -ForegroundColor Red
    exit 1
}
