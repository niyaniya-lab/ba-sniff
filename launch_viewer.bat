@echo off
REM ===============================================================
REM  Blue Archive - offline profile VIEWER.
REM  NO game contact. Pick a captured profile (captures\profile*.json)
REM  and it opens a rich HTML report in your browser - account, roster,
REM  items, currencies, planning. The report is a LOCAL file; nothing
REM  is uploaded. Works for Global and JP profiles.
REM ===============================================================
cd /d "%~dp0"
".venv\Scripts\python.exe" ba_view.py
