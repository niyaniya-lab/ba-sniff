# install-cert.ps1 - run ELEVATED. Installs the mitmproxy CA into the machine
# Trusted Root store so Blue Archive's TLS trusts the local proxy.
$cer = "$env:USERPROFILE\.mitmproxy\mitmproxy-ca-cert.cer"

Write-Host "Installing mitmproxy CA into LocalMachine\Root ..." -ForegroundColor Cyan
if (-not (Test-Path $cer)) {
    Write-Host "Cert file not found: $cer" -ForegroundColor Red
    Write-Host "Run mitmdump once first so it generates the CA." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    return
}

certutil -addstore -f Root "$cer"
Write-Host "certutil exit code: $LASTEXITCODE"

# Also clean up the stray copy that Import-Certificate put in the Intermediate (CA) store.
certutil -delstore CA "mitmproxy" 2>$null | Out-Null

$ok = Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*mitmproxy*" }
if ($ok) {
    Write-Host "CONFIRMED in LocalMachine\Root:" -ForegroundColor Green
    $ok | ForEach-Object { Write-Host "  $($_.Subject)  thumb=$($_.Thumbprint)" -ForegroundColor Green }
} else {
    Write-Host "STILL NOT in LocalMachine\Root - tell Claude." -ForegroundColor Red
}
Write-Host ""
Read-Host "Press Enter to close"
