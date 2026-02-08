@echo off
cd /d "%~dp0"
echo [Mika-AI] Launching Mika from %CD%...

:: 1. Check/Start Ollama
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Mika-AI] Ollama is already running.
) else (
    echo [Mika-AI] Starting Ollama serve...
    start /B ollama serve
    timeout /t 5 >nul
)

:: 2. Activate venv and Launch App
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found. Please run setup_mika.bat first.
    pause
    exit /b 1
)

echo [Mika-AI] Activating environment...
call .venv\Scripts\activate

echo [Mika-AI] Starting Mika AI Dashboard...
python main.py

pause
