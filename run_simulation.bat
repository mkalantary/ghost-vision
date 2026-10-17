@echo off
setlocal
title Ghost-Vision
rem ---------------------------------------------------------------------
rem  Double-click this file to try Ghost-Vision with no hardware at all.
rem  The first run downloads what it needs (a few minutes). Later runs
rem  start straight away.
rem ---------------------------------------------------------------------
cd /d "%~dp0console"

rem run_ghost_vision.bat calls this file with "live" to use real boards.
set "MODE=--sim"
set "LABEL=simulation mode"
if /i "%~1"=="live" (
    set "MODE="
    set "LABEL=live mode - using your boards"
)

rem Find Python. "py" is the launcher that comes with python.org's installer.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo.
    echo  Python was not found on this computer.
    echo.
    echo  1. Download it from https://www.python.org/downloads/
    echo  2. In the installer, tick "Add python.exe to PATH".
    echo  3. Double-click this file again.
    echo.
    pause
    exit /b 1
)

rem A private folder for Ghost-Vision's add-ons, so nothing else is touched.
if not exist ".venv\Scripts\python.exe" (
    echo  First run: setting things up. This happens only once...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

echo  Checking add-ons...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :fail

echo  Starting Ghost-Vision in %LABEL%...
".venv\Scripts\python.exe" ghost_vision_ops.py %MODE%
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo  Something went wrong. The message above says what.
echo  Help: see docs\TROUBLESHOOTING.md, or open an issue on GitHub.
echo.
pause
exit /b 1
