@echo off
echo [Aurelia-AI] Initializing Setup...

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Please install Python 3.10+ and add it to your PATH.
    pause
    exit /b 1
)

:: Create virtual environment
echo [Aurelia-AI] Creating virtual environment...
python -m venv .venv

:: Activate venv and install requirements
echo [Aurelia-AI] Activating environment and installing dependencies...
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

:: [FIX] Workaround for ctranslate2 ROCm path error on Windows
echo [Aurelia-AI] Applying compatibility patches...
set "ROCM_PATH=.venv\Lib\site-packages\_rocm_sdk_core\bin"
if not exist "%ROCM_PATH%" (
    echo [Aurelia-AI] Creating compatibility directory for ctranslate2...
    mkdir "%ROCM_PATH%" >nul 2>&1
)

echo [Aurelia-AI] Setup complete! You can now use launch_aurelia.bat
pause
