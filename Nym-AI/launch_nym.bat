@echo off
cd /d "%~dp0"
echo [Nym-AI] Launching Nym: The Ultimate Villain from %CD%...

:: 1. Check/Start Ollama
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Nym-AI] Ollama is already running.
) else (
    echo [Nym-AI] Starting Ollama serve...
    start /B ollama serve
    timeout /t 5 >nul
)

:: 2. Activate venv and Launch App
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_env.bat first.
    pause
    exit /b 1
)

echo [Nym-AI] Activating environment...
call .venv\Scripts\activate

echo [Nym-AI] Starting Nym AI Dashboard...
python main.py

pause
