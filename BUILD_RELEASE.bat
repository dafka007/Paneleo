@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Paneleo Windows Distribution Build

set "VENV_PY=.venv\Scripts\python.exe"
set "APP_VERSION="
set "APP_VERSION_NUMERIC="
set "ISCC="

if not exist "%VENV_PY%" (
    echo Paneleo's build environment is missing. Run RUN.bat first.
    goto :fail
)

"%VENV_PY%" -c "import sys; import PySide6; import pymupdf; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
if errorlevel 1 (
    echo Paneleo's .venv is invalid. Run RUN.bat to recreate it first.
    goto :fail
)

for /f "delims=" %%V in ('"%VENV_PY%" packaging\prepare_windows_assets.py --app app.py --print-version') do set "APP_VERSION=%%V"
for /f "delims=" %%V in ('"%VENV_PY%" packaging\prepare_windows_assets.py --app app.py --print-numeric-version') do set "APP_VERSION_NUMERIC=%%V"
if not defined APP_VERSION goto :fail
if not defined APP_VERSION_NUMERIC goto :fail

call BUILD_EXE.bat --no-pause
if errorlevel 1 goto :fail

if exist "release" rmdir /s /q "release"
mkdir "release"
if errorlevel 1 goto :fail

"%VENV_PY%" packaging\create_portable_zip.py --source dist\Paneleo --output "release\Paneleo-Portable-%APP_VERSION%.zip"
if errorlevel 1 goto :fail

for %%I in (
    "%LOCALAPPDATA%\Programs\Inno Setup 7\ISCC.exe"
    "%ProgramFiles%\Inno Setup 7\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 7\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) do if not defined ISCC if exist "%%~I" set "ISCC=%%~I"

if not defined ISCC (
    echo Inno Setup was not found. Install it from https://jrsoftware.org/isdl.php
    goto :fail
)

"%ISCC%" /Qp "/DAppVersion=%APP_VERSION%" "/DAppVersionNumeric=%APP_VERSION_NUMERIC%" "/DSourceDir=%CD%\dist\Paneleo" "/DOutputDir=%CD%\release" packaging\Paneleo.iss
if errorlevel 1 goto :fail

"%VENV_PY%" packaging\create_corresponding_source.py --project-root "%CD%" --ref HEAD --output "release\Paneleo-Corresponding-Source-%APP_VERSION%.zip"
if errorlevel 1 goto :fail

if not exist "release\Paneleo-Setup-%APP_VERSION%.exe" goto :fail
if not exist "release\Paneleo-Portable-%APP_VERSION%.zip" goto :fail
if not exist "release\Paneleo-Corresponding-Source-%APP_VERSION%.zip" goto :fail

echo.
echo Windows distributions created successfully:
echo   release\Paneleo-Setup-%APP_VERSION%.exe
echo   release\Paneleo-Portable-%APP_VERSION%.zip
echo   release\Paneleo-Corresponding-Source-%APP_VERSION%.zip
echo.
if /I "%~1"=="--no-pause" exit /b 0
pause
exit /b 0

:fail
echo.
echo Windows distribution build failed.
if /I "%~1"=="--no-pause" exit /b 1
pause
exit /b 1
