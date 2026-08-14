@echo off
REM ===============================================================
REM  Global -> Shittim private server, in one go.
REM
REM  Double-click this, THEN start Blue Archive from Steam. It waits for the
REM  client, captures your account, writes the import file and closes the game.
REM
REM  Three of the four pieces arrive on their own while you log in. The fourth,
REM  your ID card, is only sent when the client asks for the friend list, so it
REM  asks you to open that screen and gives it a short window. Skipping it just
REM  means the ID card is not imported.
REM
REM  Read-only: same hook as launch_gl_passive.bat, no gc.choose, no writes to
REM  the client. Global only - JP cannot be attached to (see docs/FINDINGS.md).
REM
REM  Output: captures\shittim_latest.json
REM  Load it: Shittim Control Center -> Accounts -> New -> Browse
REM
REM  Pass --keep to leave the game running at the end:
REM     launch_shittim.bat --keep
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" shittim_capture.py %*
