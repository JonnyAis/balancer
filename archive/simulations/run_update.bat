@echo off
REM Batch file for running scheduled data updates
REM This can be used with Windows Task Scheduler

REM Set the working directory to the script location
cd /d "%~dp0"

REM Define Python environment path (adjust if needed)
set PYTHON_PATH=python

REM Check command line arguments
if "%1"=="" (
    echo Usage: run_update.bat [daily^|weekly^|monthly]
    echo Example: run_update.bat daily
    exit /b 1
)

REM Create logs directory if it doesn't exist
if not exist "logs" mkdir logs

REM Run the update script
echo Starting %1 data update at %date% %time%
%PYTHON_PATH% schedule_updates.py %1

REM Log the exit code
if %ERRORLEVEL% EQU 0 (
    echo %1 update completed successfully at %date% %time%
) else (
    echo %1 update failed with exit code %ERRORLEVEL% at %date% %time%
)

exit /b %ERRORLEVEL%
