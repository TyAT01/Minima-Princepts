@echo off
cd /d "%~dp0"
echo [Yuzu-AI] Launching Yuzu from %CD%...

:: 1. Check/Start Ollama
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Yuzu-AI] Ollama is already running.
) else (
    echo [Yuzu-AI] Starting Ollama serve...
    start /B ollama serve
    timeout /t 5 >nul
)

:: 2. Activate venv and Launch App
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_env.bat first.
    pause
    exit /b 1
)

echo [Yuzu-AI] Activating environment...
call .venv\Scripts\activate

echo [Yuzu-AI] Starting Yuzu AI Dashboard...
python main.py

pause
