@echo off
cd /d "%~dp0"
title Paneleo 0.1.0-beta.1 - Debug

if not exist ".venv\Scripts\python.exe" (
    echo Paneleo is not installed yet.
    echo Running installer first...
    call INSTALL.bat
    if errorlevel 1 goto :fail
)

echo Starting Paneleo 0.1.0-beta.1 in debug mode...
".venv\Scripts\python.exe" app.py 2>crash_log.txt
set ERR=%errorlevel%

if %ERR% neq 0 (
    echo.
    echo Paneleo could not start. Error code: %ERR%
    echo Error saved to: %~dp0crash_log.txt
    echo.
    type crash_log.txt
    echo.
    pause
    exit /b %ERR%
)
exit /b 0

:fail
echo.
echo Installation/startup failed.
pause
exit /b 1
