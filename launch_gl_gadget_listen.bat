@echo off
REM ===============================================================
REM  Blue Archive GLOBAL - GADGET / LISTEN mode (experimental).
REM  Injects frida-gadget.dll into a RUNNING BlueArchive.exe, which opens an
REM  in-process socket; then connects and captures with the normal _capture.js
REM  pipeline (output -> captures\, same as launch_gl_passive.bat).
REM
REM  This is an alternative injection path to test the gadget. For Global,
REM  launch_gl_passive.bat (plain attach) is simpler; use this to experiment.
REM  Launch the game and log in FIRST. Run as Administrator if injection fails.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" gadget\inject_gadget.py --mode listen --port 27042
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" gadget\gadget_capture.py 27042
