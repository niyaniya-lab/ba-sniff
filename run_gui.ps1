# run_gui.ps1 - launch the ba-sniff GUI with the bundled venv.
# Uses pythonw (no console window). For debugging, run instead:
#   .\.venv\Scripts\python.exe ba_gui.py
$here = $PSScriptRoot
$pyw = Join-Path $here ".venv\Scripts\pythonw.exe"
$gui = Join-Path $here "ba_gui.py"
if (-not (Test-Path $pyw)) {
    Write-Host "venv not found. Create it: py -3.12 -m venv .venv; .\.venv\Scripts\python -m pip install frida-tools" -ForegroundColor Yellow
    exit 1
}
Start-Process -FilePath $pyw -ArgumentList $gui -WorkingDirectory $here
Write-Host "ba-sniff GUI launched." -ForegroundColor Green
