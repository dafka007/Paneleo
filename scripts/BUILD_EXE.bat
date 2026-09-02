@echo off
setlocal
cd /d "%~dp0.."
title Paneleo 0.1.0-beta.1 - Build EXE

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Create the environment described in docs\BUILDING.md first.
    pause
    exit /b 1
)

"%VENV_PY%" -c "import sys; import PySide6; import pymupdf; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" >nul 2>nul
if errorlevel 1 (
    echo Paneleo's .venv is invalid. Recreate it as described in docs\BUILDING.md.
    pause
    exit /b 1
)

rem Keep unrelated developer tools and their DLLs out of the packaged app.
set "PATH=%~dp0.venv\Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PYTHONNOUSERSITE=1"

"%VENV_PY%" -m pip install --only-binary=:all: pyinstaller==6.22.2
if errorlevel 1 goto :fail

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
rmdir /s /q packaging\generated 2>nul
del /q Paneleo.spec 2>nul

"%VENV_PY%" packaging\prepare_windows_assets.py --app app.py --output-dir packaging\generated
if errorlevel 1 goto :fail

"%VENV_PY%" -m PyInstaller --noconfirm --clean --windowed --name Paneleo --icon packaging\generated\Paneleo.ico --version-file packaging\generated\Paneleo-version.txt app.py
if errorlevel 1 goto :fail

"%VENV_PY%" packaging\finalize_distribution.py --project-root "%CD%" --distribution dist\Paneleo
if errorlevel 1 goto :fail

echo.
echo Build complete.
echo Your app is in: dist\Paneleo\Paneleo.exe
echo Keep the whole Paneleo folder together.
echo.
if /I "%~1"=="--no-pause" exit /b 0
pause
exit /b 0

:fail
echo.
echo EXE build failed. Review the error above.
if /I "%~1"=="--no-pause" exit /b 1
pause
exit /b 1
