@echo off
cd /d "%~dp0"
echo [Seed-AI] Launching Seed from %CD%...

:: 1. Check/Start Ollama
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Seed-AI] Ollama is already running.
) else (
    echo [Seed-AI] Starting Ollama serve...
    start /B ollama serve
    timeout /t 5 >nul
)

:: 2. Activate venv and Launch App
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_env.bat first.
    pause
    exit /b 1
)

echo [Seed-AI] Activating environment...
call .venv\Scripts\activate

echo [Seed-AI] Starting Seed AI Dashboard...
python main.py

pause
