@echo off
REM ===============================================================
REM  Blue Archive JP (Yostar) - PASSIVE mode (the only JP mode).
REM  JP ships XIGNCODE3 anti-cheat: we CANNOT attach to a running client,
REM  and gc.choose (the active calculators / name resolution) hangs it. So
REM  JP is passive-only: inject the read-only hook at process creation and
REM  capture the packets the game produces. No pump, no calculators.
REM
REM  ORDER MATTERS:
REM    1. Run this FIRST and accept the UAC prompt (it arms the hook).
REM    2. THEN press Launch in the Blue Archive JP (Yostar) launcher.
REM    3. Log in, play/navigate; close the game to finish -> export_jp\.
REM
REM  WARNING: JP has anti-cheat. Injection is currently undetected but this
REM  is anti-cheat-adjacent - use at your own risk / on an account you accept
REM  the risk on.  Global has none; prefer launch_gl_passive.bat there.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" jp_capture.py
