@echo off
setlocal
cd /d "%~dp0"
title Paneleo 0.1.0-beta.1

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "VENV_OK=1"

if not exist "%VENV_PY%" set "VENV_OK=0"
if not exist "%VENV_PYW%" set "VENV_OK=0"

if "%VENV_OK%"=="1" (
    "%VENV_PY%" -c "import sys; import PySide6; import pymupdf; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
    if errorlevel 1 set "VENV_OK=0"
)

if "%VENV_OK%"=="0" (
    echo Paneleo's private Python environment is missing or invalid.
    echo Recreating the generated .venv environment safely...
    if exist ".venv" rmdir /s /q ".venv"
    if exist ".venv" (
        echo Could not remove the invalid .venv. Close Paneleo and try again.
        goto :fail
    )
    call INSTALL.bat
    if errorlevel 1 goto :fail
)

if not exist "%VENV_PY%" goto :fail
if not exist "%VENV_PYW%" goto :fail
"%VENV_PY%" -c "import sys; import PySide6; import pymupdf; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
if errorlevel 1 goto :fail

if exist crash_log.txt del /q crash_log.txt >nul 2>nul
start "Paneleo" /D "%~dp0" "%VENV_PYW%" "%~dp0START.pyw"
exit /b 0

:fail
echo.
echo Installation/startup failed.
pause
exit /b 1
