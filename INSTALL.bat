@echo off
setlocal
cd /d "%~dp0"
title Paneleo 0.1.0-beta.1 - Installer

echo ========================================
echo       Paneleo 0.1.0-beta.1
echo ========================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        set PY=python
    ) else (
        echo Python was not found.
        echo.
        echo Install Python 3.11 or 3.12 from:
        echo https://www.python.org/downloads/windows/
        echo IMPORTANT: tick "Add python.exe to PATH" during setup.
        echo.
        pause
        exit /b 1
    )
)

%PY% -c "import sys; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (3,15) else 1)"
if errorlevel 1 (
    echo Paneleo requires 64-bit Python 3.10 through 3.14.
    pause
    exit /b 1
)

for /f "delims=" %%A in ('%PY% -c "import platform; print(platform.machine())"') do set ARCH=%%A
if /I not "%ARCH%"=="AMD64" if /I not "%ARCH%"=="x86_64" (
    echo This security-hardened installer currently supports Windows x64 only.
    echo Detected architecture: %ARCH%
    pause
    exit /b 1
)

echo Creating private Paneleo environment...
%PY% -m venv .venv
if errorlevel 1 goto :fail

call .venv\Scripts\activate.bat
python -m pip install --only-binary=:all: --require-hashes -r requirements-win64.txt
if errorlevel 1 goto :fail

echo.
echo ========================================
echo Installation complete.
echo Double-click RUN.bat to start Paneleo.
echo ========================================
echo.
echo NOTE: CBR files require 7-Zip.
echo If needed: https://www.7-zip.org/
echo.
pause
exit /b 0

:fail
echo.
echo Installation failed. Use RUN_DEBUG.bat for details.
pause
exit /b 1
