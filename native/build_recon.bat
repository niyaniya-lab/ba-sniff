@echo off
REM Build recon.dll (x64). Thin wrapper over build.bat (shared toolchain setup).
call "%~dp0build.bat" recon.cpp recon.dll
