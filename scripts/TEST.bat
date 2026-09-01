@echo off
setlocal
cd /d "%~dp0.."
title Paneleo Regression Tests

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :missing

"%VENV_PY%" -c "import sys; import PySide6; import pymupdf; import pytest; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
if errorlevel 1 goto :broken

set "PYTHONNOUSERSITE=1"
echo Running Paneleo regression tests...
"%VENV_PY%" -m pytest -q
set "ERR=%errorlevel%"

if %ERR% neq 0 (
    echo.
    echo Paneleo regression tests failed.
    exit /b %ERR%
)

echo.
echo All Paneleo regression tests passed.
exit /b 0

:missing
echo Paneleo's .venv is missing.
echo Create the environment as described in docs\BUILDING.md, then run:
echo .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
exit /b 1

:broken
echo Paneleo's test environment is missing, broken, or incomplete.
echo Create the environment as described in docs\BUILDING.md, then run:
echo .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
exit /b 1
