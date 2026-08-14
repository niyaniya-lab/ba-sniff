@echo off
REM ===============================================================
REM  Global -> Shittim private server, in one go.
REM
REM  Double-click and walk away. It asks Steam to launch Blue Archive, captures
REM  your account, writes the import file and closes the game.
REM
REM  Three of the four pieces arrive on their own while you log in. The fourth,
REM  your ID card, is only sent when the client asks for the friend list, so it
REM  asks the game for that itself; if it cannot, it asks you to open the screen.
REM  Skipping it just means the ID card is not imported.
REM
REM  Read-only: same hook as launch_gl_passive.bat, no writes to the client.
REM  Global only - JP cannot be attached to (see docs/FINDINGS.md).
REM
REM  Output: captures\shittim_latest.json
REM  Load it: Shittim Control Center -> Accounts -> New -> Browse
REM
REM    launch_shittim.bat --keep        leave the game running at the end
REM    launch_shittim.bat --no-launch   do not touch Steam; start the game yourself
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" shittim_capture.py %*
