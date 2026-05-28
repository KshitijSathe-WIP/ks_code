@echo off
setlocal enabledelayedexpansion
title ZCOP Frontend — Setup

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "PY_EXE=%VENV%\Scripts\python.exe"
set "PYW_EXE=%VENV%\Scripts\pythonw.exe"
set "FRONTEND=%ROOT%zcop_frontend.py"
set "PACKAGES=openpyxl"

echo.
echo  ============================================================
echo   ZCOP Frontend Launcher
echo  ============================================================

REM ── 1. Python availability ───────────────────────────────────────────────────
REM  Check exit code of --version (Windows Store alias exits non-zero / code 9009)
set "PY_CMD="
python --version >nul 2>&1
if not errorlevel 1 set "PY_CMD=python"
if not defined PY_CMD (
    py --version >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py"
)
if not defined PY_CMD (
    echo.
    echo  [ERROR] Python not found.
    echo  Install Python 3.8+ from https://python.org and check "Add Python to PATH".
    echo  If the Store alias is blocking: Settings ^> Apps ^> App execution aliases
    echo  and disable the python.exe / python3.exe entries.
    echo.
    goto :fail
)
for /f "tokens=*" %%v in ('%PY_CMD% --version 2^>^&1') do set "PY_VER=%%v"
echo  [OK] %PY_VER% (via %PY_CMD%)

REM ── 2. Virtual environment ───────────────────────────────────────────────────
if not exist "%PY_EXE%" (
    echo  [..] Creating virtual environment...
    %PY_CMD% -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo  [ERROR] Failed to create virtual environment.
        echo  Ensure Python is correctly installed with the venv module.
        echo.
        goto :fail
    )
    echo  [OK] Virtual environment created.
) else (
    echo  [OK] Virtual environment found.
)

REM ── 3. Required packages ─────────────────────────────────────────────────────
echo  [..] Installing / verifying packages: %PACKAGES%
"%PY_EXE%" -m pip install --quiet --disable-pip-version-check %PACKAGES%
if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to install required packages: %PACKAGES%
    echo  Check your internet connection and try again.
    echo.
    goto :fail
)
echo  [OK] Packages satisfied.

REM ── 4. Launch app ────────────────────────────────────────────────────────────
echo  [OK] Launching ZCOP Frontend...
echo.
start "" "%PYW_EXE%" "%FRONTEND%"
endlocal
exit /b 0

:fail
echo  Setup failed. No processes were left running.
echo.
pause
endlocal
exit /b 1
