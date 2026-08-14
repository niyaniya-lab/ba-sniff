@echo off
REM ===============================================================
REM  Blue Archive GLOBAL - FULL mode (live companion GUI).
REM  Attaches to the running Steam client (Global has no anti-cheat).
REM  ACTIVE: live auto-sync, AP counter, and the materials-to-max
REM  calculators (skills / star / UE / gear). Highest in-process footprint.
REM
REM  Launch the game via Steam and log in FIRST, then run this.
REM  Export -> captures\export\  (region: Global)
REM
REM  Prefer launch_gl_passive.bat if you want the lightest touch.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" ba_gui.py
