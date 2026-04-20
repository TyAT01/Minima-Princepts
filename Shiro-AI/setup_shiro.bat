@echo off
setlocal enabledelayedexpansion
title Shiro AI Setup
cd /d "%~dp0"

echo [Shiro-AI] Setup starting...

set "SHIRO_DIR=%~dp0"
set "SOVITS_DIR=%SHIRO_DIR%gpt_sovits_setup\GPT-SoVITS"
set "SOVITS_VENV=%SOVITS_DIR%\venv"

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

:: 5. GPT-SoVITS venv
echo [5/6] Checking GPT-SoVITS voice engine...
set "SOVITS_FOUND=0"
if exist "%SOVITS_DIR%\api_v2.py" set "SOVITS_FOUND=1"

if "%SOVITS_FOUND%"=="0" (
    echo       [WARNING] GPT-SoVITS not found at %SOVITS_DIR%
    echo       Clone it with: git clone https://github.com/RVC-Boss/GPT-SoVITS
    echo       into: %SHIRO_DIR%gpt_sovits_setup\
) else (
    if exist "%SOVITS_VENV%\Scripts\activate.bat" (
        echo       GPT-SoVITS venv already exists.
    ) else (
        echo       Creating GPT-SoVITS venv...
        python -m venv "%SOVITS_VENV%"
        if %ERRORLEVEL% neq 0 (
            echo [ERROR] Failed to create GPT-SoVITS venv.
        ) else (
            echo       Installing GPT-SoVITS dependencies...
            call "%SOVITS_VENV%\Scripts\activate.bat"
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121 --quiet
            pip install numpy==1.26.4 --quiet
            pip install transformers==4.45.2 --quiet
            pip install -r "%SOVITS_DIR%\requirements.txt" --quiet
            echo       GPT-SoVITS venv ready.
            call "%SHIRO_DIR%venv\Scripts\activate.bat"
        )
    )
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
if not exist "%SOVITS_DIR%\api_v2.py" (
    echo       [MISSING] GPT-SoVITS api_v2.py
    set "_ok=0"
)
if not exist "%SOVITS_VENV%\Scripts\activate.bat" (
    echo       [MISSING] GPT-SoVITS venv
    set "_ok=0"
)
if "%_ok%"=="1" echo       All core files present.
if "%_ok%"=="0" echo       [WARNING] Some files are missing - check above.

echo.
echo [Shiro-AI] Setup complete. Run launch_shiro.bat to start.
echo.
pause