@echo off
echo [Aurelia-AI] Launching Aurelia Vale...

:: 1. Check/Start Ollama
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Aurelia-AI] Ollama is already running.
) else (
    echo [Aurelia-AI] Starting Ollama serve...
    start /B ollama serve
    timeout /t 5 >nul
)

:: 2. Activate venv and Launch App
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_env.bat first.
    pause
    exit /b 1
)

echo [Aurelia-AI] Activating environment...
call .venv\Scripts\activate

echo [Aurelia-AI] Starting Aurelia AI Dashboard...
python main.py

pause
