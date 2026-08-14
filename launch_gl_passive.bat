@echo off
REM ===============================================================
REM  Blue Archive GLOBAL - PASSIVE mode (lightest touch).
REM  Only READS the packets the game already produced - it installs
REM  the JSON-deserialize hook and nothing else:
REM    NO per-frame pump, NO network-task firing, NO memory walk (gc.choose).
REM  Same data grab as playing normally; the materials-to-max calculators
REM  are NOT available in this mode (those need the active/full mode).
REM
REM  For risk-averse users who want minimal in-process activity.
REM  (Note: it still injects Frida, so the ToS risk isn't zero - there is
REM   no zero-risk option - but this touches the game as little as possible.)
REM
REM  HOW TO USE:
REM   1. Launch the game via Steam and log in.
REM   2. Run this. It attaches and captures as you play.
REM   3. Play / navigate the screens whose data you want.
REM   4. CLOSE THE GAME to finish - it then exports to captures\export\.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" frida\capture.py
".venv\Scripts\python.exe" ba_export.py
