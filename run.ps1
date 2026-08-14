# run.ps1 - launch the passive BA gateway sniffer in the background.
# Captures ONLY BlueArchive.exe traffic (OS-level local redirect mode), forwards
# everything to the real Nexon servers untouched, and decodes gateway packets.
#
# Prereq (one-time): install the mitmproxy CA into the Local Machine root store.
#   See README.md -> "One-time: certificate".

param(
    [switch]$Web,          # use mitmweb (UI at 127.0.0.1:8081) instead of headless mitmdump
    [string]$Process = "BlueArchive.exe"
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$addon = Join-Path $here "ba_sniff.py"

if (-not (Test-Path $addon)) {
    Write-Host "Cannot find ba_sniff.py next to run.ps1" -ForegroundColor Red
    exit 1
}

# Prefer the bundled venv (isolated, correct mitmproxy 12 + Python 3.12); fall back to PATH.
$toolName = if ($Web) { "mitmweb" } else { "mitmdump" }
$venvTool = Join-Path $here ".venv\Scripts\$toolName.exe"
if (Test-Path $venvTool) {
    $tool = $venvTool
} else {
    $onPath = Get-Command $toolName -ErrorAction SilentlyContinue
    if (-not $onPath) {
        Write-Host "$toolName not found. Expected it in .venv or on PATH." -ForegroundColor Red
        Write-Host "Rebuild the venv:  py -3.12 -m venv .venv; .\.venv\Scripts\python -m pip install 'mitmproxy>=11'" -ForegroundColor Yellow
        exit 1
    }
    $tool = $onPath.Source
}
Write-Host "Using $tool" -ForegroundColor DarkGray

Write-Host "Starting passive sniffer ($tool) on process '$Process'..." -ForegroundColor Cyan
Write-Host "Captures -> $(Join-Path $here 'captures')" -ForegroundColor DarkGray
Write-Host "Launch Blue Archive from Steam now. Ctrl+C here to stop." -ForegroundColor Green
Write-Host ""

# --mode local:<proc>  = transparent, per-process capture (no system proxy, no root).
# --no-http2           = BA's gateway is HTTP/1.1; matches the known-good repo setup.
# -q (mitmdump)        = quiet mitmproxy's own noise; our addon prints what matters.
$common = @("--mode", "local:$Process", "--no-http2", "-s", $addon)

if ($Web) {
    & $tool @common --set termlog_verbosity=warn
} else {
    & $tool @common -q
}
