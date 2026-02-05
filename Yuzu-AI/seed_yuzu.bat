@echo off
cd /d "%~dp0"
echo [Yuzu-AI] Seeding Yuzu's Personality Memory...

:: 1. Activate venv
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_env.bat first.
    pause
    exit /b 1
)

echo [Yuzu-AI] Activating environment...
call .venv\Scripts\activate

:: 2. Run Seed Script
echo [Yuzu-AI] Running seed_memory.py...
python seed_memory.py

echo.
echo [Yuzu-AI] Seeding process finished.
pause
