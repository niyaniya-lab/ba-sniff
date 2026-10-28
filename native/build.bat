@echo off
REM Generic x64 DLL build: build.bat <source.cpp> [output.dll]
REM
REM Sources Microsoft's vcvarsall.bat (pinned to the 14.39 toolset + 10.0.22621.0
REM SDK) for a complete INCLUDE/LIB, then compiles a DLL. Used by build_recon.bat
REM and to build the length-decoder test DLL.

setlocal
cd /d "%~dp0"

if "%~1"=="" goto usage
set "SRC=%~1"
set "OUT=%~2"
if "%OUT%"=="" set "OUT=%~n1.dll"

set "VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat"
if not exist "%VCVARS%" goto no_vcvars

call "%VCVARS%" x64 10.0.22621.0 -vcvars_ver=14.39 >nul
if errorlevel 1 goto env_failed

REM /LD DLL (omitted for .exe output), /MT static CRT, /O2, /EHsc.
REM /INCREMENTAL:NO so &function is the real address, not a linker jump-thunk.
set "LDFLAG=/LD"
if /I "%OUT:~-4%"==".exe" set "LDFLAG="
cl /nologo /std:c++17 /O2 /MT /EHsc %LDFLAG% "%SRC%" /Fe:"%OUT%" /link /INCREMENTAL:NO kernel32.lib
if errorlevel 1 goto build_failed

del /q *.obj 2>nul
echo [+] built %~dp0%OUT%
endlocal
exit /b 0

:usage
echo usage: build.bat ^<source.cpp^> [output.dll]
endlocal
exit /b 1

:no_vcvars
echo [-] vcvarsall.bat not found at:
echo     "%VCVARS%"
endlocal
exit /b 1

:env_failed
echo [-] vcvarsall.bat failed to set the build environment.
endlocal
exit /b 1

:build_failed
echo [-] build failed.
endlocal
exit /b 1
