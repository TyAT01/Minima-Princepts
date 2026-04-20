@echo off
setlocal enabledelayedexpansion
title Shiro AI
cd /d "%~dp0"

echo [Shiro-AI] Starting...

set "SHIRO_DIR=%~dp0"
set "SOVITS_DIR=%SHIRO_DIR%gpt_sovits_setup\GPT-SoVITS"
set "SOVITS_VENV=%SOVITS_DIR%\venv\Scripts\activate.bat"

:: ── VRAM allocation ──────────────────────────────────────────────────────────
:: llama3.1:8b-instruct-q4_K_M at 32 layers = ~4.07 GB.
:: OLLAMA_MAX_VRAM caps Ollama at 4 GB so SoVITS (CUDA) still has headroom.
:: OLLAMA_NUM_GPU=99 tells Ollama to offload ALL layers — the cap enforces the
:: actual VRAM limit. Using a fixed layer count (18) was causing partial CPU
:: offload which slowed inference and destabilised Shiro's responses.
set "CUDA_VISIBLE_DEVICES=0"
set "OLLAMA_MAX_VRAM=4294967296"
set "OLLAMA_NUM_GPU=99"

:: ── 1. Ollama ─────────────────────────────────────────────────────────────────
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL 2>&1
if %ERRORLEVEL%==0 (
    echo [1/3] Ollama already running.
) else (
    echo [1/3] Starting Ollama...
    start "" ollama serve
    :: Wait for Ollama to be ready — poll instead of fixed sleep
    set "_ollama_ready=0"
    for /L %%i in (1,1,15) do (
        if !_ollama_ready!==0 (
            timeout /t 1 /nobreak >nul
            curl -s -o nul http://localhost:11434 >nul 2>&1
            if !ERRORLEVEL!==0 set "_ollama_ready=1"
        )
    )
    if !_ollama_ready!==1 (
        echo       Ollama ready.
    ) else (
        echo       WARNING: Ollama may still be loading. Continuing anyway.
    )
)

:: ── 2. GPT-SoVITS ─────────────────────────────────────────────────────────────
netstat -an 2>NUL | find ":9880" | find "LISTENING" >NUL 2>&1
if %ERRORLEVEL%==0 (
    echo [2/3] GPT-SoVITS already running on port 9880.
    goto :start_shiro
)
if not exist "%SOVITS_VENV%" (
    echo [2/3] WARNING: GPT-SoVITS venv not found - text-only mode.
    echo       Run setup_shiro.bat to enable voice.
    goto :start_shiro
)
if not exist "%SOVITS_DIR%\api_v2.py" (
    echo [2/3] WARNING: api_v2.py not found - text-only mode.
    goto :start_shiro
)

echo [2/3] Starting GPT-SoVITS TTS server...
start "GPT-SoVITS API" /D "%SOVITS_DIR%" cmd /k "call venv\Scripts\activate && python api_v2.py -a 127.0.0.1 -p 9880"
echo       Waiting for TTS server (up to 35s)...

:: Poll every 2 seconds instead of 8 fixed-sleep loops — faster detection,
:: same total timeout ceiling (35s).
set "_tts_ready=0"
for /L %%i in (1,1,17) do (
    if !_tts_ready!==0 (
        timeout /t 2 /nobreak >nul
        netstat -an 2>NUL | find ":9880" | find "LISTENING" >NUL 2>&1
        if !ERRORLEVEL!==0 set "_tts_ready=1"
    )
)
if !_tts_ready!==1 (
    echo       GPT-SoVITS TTS server ready.
) else (
    echo       [WARNING] TTS server slow to start - continuing anyway.
)

:: ── 3. Shiro ───────────────────────────────────────────────────────────────────
:start_shiro
echo [3/3] Starting Shiro AI...
if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] Shiro venv not found. Run setup_shiro.bat first.
    pause
    exit /b 1
)
echo.
call venv\Scripts\activate
python main.py

echo.
echo [Shiro-AI] Shiro has exited.
pause