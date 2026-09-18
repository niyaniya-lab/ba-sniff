@echo off
REM ===============================================================
REM  Blue Archive GLOBAL - GADGET / SCRIPT mode (experimental).
REM  Injects frida-gadget.dll configured to auto-run frida\agent\_capture.js
REM  standalone (no Python client).
REM
REM  NOTE: _capture.js send()s packets to a client, so in script mode they go
REM  to the gadget log, NOT to captures\. Use this to confirm the hook arms
REM  without Python; standalone persistence needs an agent that writes to disk.
REM  See gadget\README.md.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" gadget\inject_gadget.py --mode script --script frida\agent\_capture.js
