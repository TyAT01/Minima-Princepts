@echo off
setlocal

:: Change to the script's directory
cd /d %~dp0

:: Check if python is installed
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ❌ python could not be found. Please install it.
    pause
    exit /b 1
)

:: Run the master launcher
python master_launch.py %*

if %ERRORLEVEL% neq 0 (
    echo ❌ Launcher failed with error code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)

endlocal
