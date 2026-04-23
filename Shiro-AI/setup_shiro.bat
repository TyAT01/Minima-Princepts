@echo off
setlocal enabledelayedexpansion
title Shiro AI Setup
cd /d "%~dp0"

echo [Shiro-AI] Setup starting...

set "SHIRO_DIR=%~dp0"
set "KOKORO_DIR=%SHIRO_DIR%kokoro"

:: 1. Python check
python --version >NUL 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Install Python 3.11 and try again.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/6] Python %PYVER% found.

:: 2. Shiro venv
if exist "venv" (
    echo [2/6] Shiro venv already exists.
) else (
    echo [2/6] Creating Shiro venv...
    python -m venv venv
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create Shiro venv.
        pause
        exit /b 1
    )
)

:: 3. Install Shiro dependencies
echo [3/6] Installing Shiro dependencies...
call venv\Scripts\activate
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
echo       Done.

:: 4. ctranslate2 patch
echo [4/6] Applying compatibility patches...
set "CT2=%SHIRO_DIR%venv\Lib\site-packages\ctranslate2"
if exist "%CT2%" (
    if not exist "%CT2%\..\..\ctranslate2_data" mkdir "%CT2%\..\..\ctranslate2_data" >NUL 2>&1
    if not exist "%CT2%\..\..\..\ctranslate2_data" mkdir "%CT2%\..\..\..\ctranslate2_data" >NUL 2>&1
)
echo       Done.

:: 5. Kokoro TTS Check
echo [5/6] Checking Kokoro TTS engine...
if exist "%KOKORO_DIR%\kokoro-v0_19.onnx" (
    echo       Kokoro model found.
) else (
    echo       [WARNING] Kokoro model (kokoro-v0_19.onnx) not found in %KOKORO_DIR%
    echo       Download it to enable high-quality local voice.
)

:: 6. Verify
echo [6/6] Verifying files...
set "_ok=1"
for %%F in (main.py tts.py config.yaml shiro_sheet.yaml shiro_engine.py) do (
    if not exist "%%F" (
        echo       [MISSING] %%F
        set "_ok=0"
    )
)
if "%_ok%"=="1" echo       All core files present.
if "%_ok%"=="0" echo       [WARNING] Some files are missing - check above.

echo.
echo [Shiro-AI] Setup complete. Run launch_shiro.bat to start.
echo.
pause
