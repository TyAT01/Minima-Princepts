@echo off
setlocal enabledelayedexpansion
title Shiro AI
cd /d "%~dp0"

echo [Shiro-AI] Starting...

set "SHIRO_DIR=%~dp0"
set "KOKORO_DIR=%SHIRO_DIR%kokoro"

:: ── VRAM allocation ──────────────────────────────────────────────────────────
:: llama3.1:8b-instruct-q4_K_M at 32 layers = ~4.07 GB.
:: OLLAMA_MAX_VRAM caps Ollama at 4 GB so Kokoro (ONNX) still has headroom.
:: Kokoro uses ~0.2 GB VRAM via ONNX CUDA provider.
:: OLLAMA_NUM_GPU=99 tells Ollama to offload ALL layers — the cap enforces the
:: actual VRAM limit.
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

:: ── 2. Kokoro TTS ─────────────────────────────────────────────────────────────
if not exist "%KOKORO_DIR%\kokoro-v0_19.onnx" (
    echo [2/3] WARNING: Kokoro model not found in %KOKORO_DIR%
    echo       Shiro will run in text-only mode.
) else (
    echo [2/3] Kokoro TTS engine located.
)

:: ── 3. Shiro ───────────────────────────────────────────────────────────────────
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