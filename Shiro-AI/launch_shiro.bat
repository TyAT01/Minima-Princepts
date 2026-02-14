@echo off
setlocal enabledelayedexpansion

echo [Shiro-AI] Launching Shiro from %CD%...

:: 1. Check if Ollama is running
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo [Shiro-AI] Ollama is already running.
) else (
    echo [Shiro-AI] Starting Ollama serve...
    start "" ollama serve
    timeout /t 5 /nobreak
)

:: 2. Check for virtual environment
if not exist "venv" (
    echo [ERROR] Virtual environment not found. Please run setup_shiro.bat first.
    pause
    exit /b 1
)

:: 3. Activate environment and run
echo [Shiro-AI] Activating environment...
call venv\Scripts\activate

echo [Shiro-AI] Starting Shiro AI Dashboard...
python main.py

pause
