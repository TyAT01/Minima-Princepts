@echo off
setlocal enabledelayedexpansion

rem --- Aurelia Vale Launcher ---
rem Optimized for RTX 3070, i9 9900k, 64GB RAM
rem Supports running from external drives (e.g. F:)

cd /d "%~dp0"

echo.
echo  🌸 Starting Aurelia Vale Setup... 🌸
echo.

rem Check for administrative privileges
net session >nul 2>&1
if %errorLevel% == 0 (
    echo [INFO] Running with administrative privileges.
) else (
    echo [WARN] Not running as administrator. This is usually fine, but if you hit
    echo        permission issues, try right-clicking and 'Run as administrator'.
)

rem --- Virtual Environment Setup ---
if exist .venv\ (
    echo [INFO] Existing virtual environment found.
) else (
    echo [INFO] Creating new virtual environment in .venv folder...

    rem Try to use conda if available and requested, otherwise use standard venv
    where conda >nul 2>&1
    if %errorLevel% == 0 (
        echo [INFO] Conda detected. You can use 'conda activate' manually if preferred.
        echo        But for portability, we will use a local python venv.
    )

    python -m venv .venv
    if %errorLevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        echo         Make sure Python is installed and in your PATH.
        pause
        exit /b 1
    )
)

echo [INFO] Activating virtual environment...
call .venv\Scripts\activate
if %errorLevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

echo [INFO] Ensuring pip is up to date...
python -m pip install --upgrade pip >nul 2>&1

echo [INFO] Installing/Updating dependencies (this may take a while)...
pip install -r requirements.txt
if %errorLevel% neq 0 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [SUCCESS] Environment is ready!
echo.
echo Starting Aurelia Vale Companion...
echo.

rem Run the application with Discord and Web Dashboard enabled by default
python app.py --discord --web

if %errorLevel% neq 0 (
    echo.
    echo [ERROR] Aurelia stopped with an error (Code: %errorLevel%).
    pause
)

pause
