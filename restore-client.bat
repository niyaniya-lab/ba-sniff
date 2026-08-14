@echo off
REM ===============================================================
REM  Put the Global client back on the official server.
REM
REM  Starting Shittim-Server repoints the client at localhost by patching three
REM  files. This undoes all three:
REM
REM    gamescale.core.dll    auth endpoints          (from its sidecar)
REM    global-metadata.dat   gateway public key      (from its sidecar)
REM    ExcelDB.db            296 MB table database   (from captures\client_backup)
REM
REM  Close the game and stop the server first - it refuses otherwise, since the
REM  files are locked and the server would just re-patch them.
REM
REM  ExcelDB.db keeps no sidecar, so it needs a backup taken while the client was
REM  clean. If there is none it says so and Steam's verify-integrity is the fix.
REM  To make one, with the client clean and the server stopped:
REM
REM    copy the game's ExcelDB.db to captures\client_backup\ExcelDB.db
REM    restore-client.bat baseline
REM
REM    restore-client.bat            show which server the client points at
REM    restore-client.bat restore    put the original bytes back  (default)
REM    restore-client.bat baseline   record clean hashes
REM ===============================================================
cd /d "%~dp0"

set ACTION=%1
if "%ACTION%"=="" set ACTION=restore

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore-client.ps1" %ACTION%

echo.
pause
