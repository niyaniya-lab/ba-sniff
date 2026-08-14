# restore-client.ps1 - flip the Global client between the private server and the real one.
#
# Starting Shittim-Server patches two files in the game install so the client talks to
# localhost instead of Nexon:
#
#   BlueArchive_Data\Plugins\x86_64\gamescale.core.dll        auth/IAS endpoints -> 127.0.0.1:5000
#   BlueArchive_Data\il2cpp_data\Metadata\global-metadata.dat gateway RSA public key -> Shittim's
#
# Each patch writes a sidecar .json next to its file recording the original bytes at every
# offset, so restoring is a byte-level undo rather than a 34 MB redownload from Steam.
#
#   .\restore-client.ps1            show what state the client is in
#   .\restore-client.ps1 restore    put the original bytes back (real server)
#
# Re-patching needs no script: the server rewrites both files every time it starts.
#
# WHAT THIS CANNOT UNDO: the server also reported an older "inface config" patch that
# carries no saved original state ("It cannot be restored automatically on shutdown").
# If the client still misbehaves against the real server after a restore, use Steam ->
# Properties -> Installed Files -> Verify integrity of game files, which rebuilds
# everything regardless of sidecars.
#
# ba-sniff note: never run a Global capture while patched. Capture merges into
# profile_latest.json rather than replacing it, so private-server packets would
# contaminate the profile the exporter reads.

param([ValidateSet('status', 'restore', 'baseline')][string]$Action = 'status')

$ErrorActionPreference = 'Stop'

$GameData = 'D:\SteamLibrary\steamapps\common\BlueArchive\BlueArchive_Data'

# Files the server modifies that keep NO sidecar, so there is nothing to restore from and the
# only fix is Steam's verify-integrity. Tracked here purely so status can SAY so: the first
# version of this script reported "Done - launch and play" while ExcelDB.db was still patched,
# and the client was rejected by the official server with "Abnormal client".
$BaselinePath = Join-Path $PSScriptRoot 'captures\client_baseline.json'
$Unrestorable = @(
    @{ Name = 'ExcelDB.db'
       File = "$GameData\StreamingAssets\PUB\Resource\Preload\TableBundles\ExcelDB.db" }
)

$Targets = @(
    @{ Name = 'gamescale.core.dll'
       File = "$GameData\Plugins\x86_64\gamescale.core.dll"
       Side = "$GameData\Plugins\x86_64\gamescale.core.dll.shittim_native_ias_patch.json"
       List = 'Patches' },
    @{ Name = 'global-metadata.dat'
       File = "$GameData\il2cpp_data\Metadata\global-metadata.dat"
       Side = "$GameData\il2cpp_data\Metadata\global-metadata.dat.shittim_patch.json"
       List = 'Chunks' }
)

# Prefer the whole-file hash when the sidecar records one: it is exact. The per-offset scan
# below is only a fallback for the metadata sidecar, which records no hashes -- and it cannot
# classify entries whose Original and Patched bytes are identical (the IAS sidecar has two,
# captured during a re-patch when the value was already pointing at 127.0.0.1), so it reports
# a false "unrecognised" on a file that is in fact clean.
function Get-PatchState($target) {
    if (-not (Test-Path $target.File)) { return 'missing' }
    if (-not (Test-Path $target.Side)) { return 'no-sidecar' }

    $side = Get-Content $target.Side -Raw | ConvertFrom-Json

    if ($side.OriginalSha256 -and $side.PatchedSha256) {
        $hash = (Get-FileHash $target.File -Algorithm SHA256).Hash.ToLower()
        if ($hash -eq $side.OriginalSha256.ToLower()) { return 'original' }
        if ($hash -eq $side.PatchedSha256.ToLower()) { return 'patched' }
        return 'unrecognised (matches neither recorded hash)'
    }

    $entries = $side.($target.List)
    $stream = [System.IO.File]::OpenRead($target.File)
    try {
        $patched = 0; $original = 0
        foreach ($e in $entries) {
            $want = [Convert]::FromBase64String($e.Patched)
            $orig = [Convert]::FromBase64String($e.Original)
            $buf = New-Object byte[] $want.Length
            $stream.Seek([long]$e.Offset, 'Begin') | Out-Null
            $stream.Read($buf, 0, $buf.Length) | Out-Null
            if (@(Compare-Object $buf $want -SyncWindow 0).Count -eq 0) { $patched++ }
            elseif (@(Compare-Object $buf $orig -SyncWindow 0).Count -eq 0) { $original++ }
        }
        if ($patched -gt 0) { return "patched ($patched/$($entries.Count) offsets)" }
        if ($original -eq $entries.Count) { return 'original' }
        return 'unrecognised (neither original nor patched)'
    } finally { $stream.Dispose() }
}

function Restore-Target($target) {
    $side = Get-Content $target.Side -Raw | ConvertFrom-Json
    $entries = $side.($target.List)
    $stream = [System.IO.File]::Open($target.File, 'Open', 'ReadWrite')
    try {
        $written = 0
        foreach ($e in $entries) {
            $orig = [Convert]::FromBase64String($e.Original)
            $stream.Seek([long]$e.Offset, 'Begin') | Out-Null
            $stream.Write($orig, 0, $orig.Length)
            $written++
        }
        $stream.Flush()
        Write-Host "  restored $written offset(s) in $($target.Name)" -ForegroundColor Green
    } finally { $stream.Dispose() }
}

# Only restoring needs write access. status and baseline just hash files, which works while
# the game holds them open -- blocking those was pointless, and it cost a chance to record a
# baseline at the one moment the client was verified clean and the game happened to be up.
if ($Action -eq 'restore' -and (Get-Process -Name 'BlueArchive' -ErrorAction SilentlyContinue)) {
    Write-Host "Blue Archive is running - close it first, the files are locked." -ForegroundColor Red
    exit 1
}

# Sidecar-less files can only be judged against hashes recorded while the client was known
# clean (straight after a Steam verify).
function Get-Baseline {
    if (Test-Path $BaselinePath) { return Get-Content $BaselinePath -Raw | ConvertFrom-Json }
    return $null
}

function Show-Unrestorable {
    $base = Get-Baseline
    foreach ($u in $Unrestorable) {
        if (-not (Test-Path $u.File)) { continue }
        $hash = (Get-FileHash $u.File -Algorithm SHA256).Hash.ToLower()
        if ($null -eq $base -or -not $base.($u.Name)) {
            Write-Host ("  {0,-22} unknown - no baseline recorded" -f $u.Name) -ForegroundColor Yellow
        } elseif ($hash -eq $base.($u.Name)) {
            Write-Host ("  {0,-22} original" -f $u.Name) -ForegroundColor Green
        } else {
            Write-Host ("  {0,-22} MODIFIED - needs Steam verify (no sidecar to restore from)" -f $u.Name) -ForegroundColor Red
        }
    }
}

if ($Action -eq 'baseline') {
    Write-Host "Recording clean hashes. Only do this straight after a Steam verify," -ForegroundColor Cyan
    Write-Host "with the server stopped - otherwise you are baselining a patched file." -ForegroundColor Cyan
    $data = @{}
    foreach ($u in $Unrestorable) {
        if (Test-Path $u.File) {
            $data[$u.Name] = (Get-FileHash $u.File -Algorithm SHA256).Hash.ToLower()
            Write-Host "  $($u.Name)  $($data[$u.Name])" -ForegroundColor Green
        }
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $BaselinePath) | Out-Null
    $data | ConvertTo-Json | Set-Content $BaselinePath -Encoding UTF8
    Write-Host "saved -> $BaselinePath"
    exit 0
}

if ($Action -eq 'status') {
    Write-Host "Client state:" -ForegroundColor Cyan
    foreach ($t in $Targets) {
        $state = Get-PatchState $t
        $colour = if ($state -eq 'original') { 'Green' } elseif ($state -like 'patched*') { 'Yellow' } else { 'Red' }
        Write-Host ("  {0,-22} {1}" -f $t.Name, $state) -ForegroundColor $colour
    }
    Show-Unrestorable
    Write-Host ""
    Write-Host "  patched  -> talks to the private server (start Shittim-Server)"
    Write-Host "  original -> talks to Nexon"
    Write-Host ""
    Write-Host "Run '.\restore-client.ps1 restore' to go back to the real server."
    exit 0
}

# Shittim re-patches on startup, so restoring while it runs is pointless.
if (Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Shittim-Server is still listening on :5000 - stop it first, or it will" -ForegroundColor Red
    Write-Host "re-patch the client the next time it starts." -ForegroundColor Red
    exit 1
}

Write-Host "Restoring original bytes..." -ForegroundColor Cyan
foreach ($t in $Targets) {
    if (-not (Test-Path $t.Side)) {
        Write-Host "  no sidecar for $($t.Name) - skipping (use Steam verify)" -ForegroundColor Yellow
        continue
    }
    Restore-Target $t
}

Write-Host ""
Write-Host "Verifying:" -ForegroundColor Cyan
$allClean = $true
foreach ($t in $Targets) {
    $state = Get-PatchState $t
    if ($state -ne 'original') { $allClean = $false }
    Write-Host ("  {0,-22} {1}" -f $t.Name, $state) -ForegroundColor $(if ($state -eq 'original') { 'Green' } else { 'Red' })
}
Show-Unrestorable
Write-Host ""

$base = Get-Baseline
$unrestorableDirty = $false
foreach ($u in $Unrestorable) {
    if (-not (Test-Path $u.File)) { continue }
    $hash = (Get-FileHash $u.File -Algorithm SHA256).Hash.ToLower()
    if ($null -eq $base -or -not $base.($u.Name) -or $hash -ne $base.($u.Name)) { $unrestorableDirty = $true }
}

if ($allClean -and -not $unrestorableDirty) {
    Write-Host "Done - launch Blue Archive from Steam to play on the real server." -ForegroundColor Green
} elseif ($unrestorableDirty) {
    Write-Host "The sidecar files are restored, but a file with no sidecar is modified or" -ForegroundColor Red
    Write-Host "unverified. The official server will reject the client with 'Abnormal client'." -ForegroundColor Red
    Write-Host "Run Steam -> Properties -> Installed Files -> Verify integrity of game files," -ForegroundColor Yellow
    Write-Host "then '.\restore-client.ps1 baseline' so this can be detected next time." -ForegroundColor Yellow
} else {
    Write-Host "Something is still modified. Use Steam -> Verify integrity of game files." -ForegroundColor Red
}
