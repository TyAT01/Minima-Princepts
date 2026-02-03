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
set "ROCM1=.venv\Lib\site-packages\_rocm_sdk_core\bin"
set "ROCM2=.venv\Lib\site-packages\_rocm_sdk_libraries_custom\bin"

if not exist "%ROCM1%" (
    echo [Aurelia-AI] Creating compatibility directory 1 for ctranslate2...
    mkdir "%ROCM1%" >nul 2>&1
)
if not exist "%ROCM2%" (
    echo [Aurelia-AI] Creating compatibility directory 2 for ctranslate2...
    mkdir "%ROCM2%" >nul 2>&1
)

echo [Aurelia-AI] Verifying persona files...
if not exist "..\Aurelia_chroma\aurelia_sheet.yaml" (
    if not exist "aurelia_sheet.yaml" (
        echo [WARNING] Character sheet 'aurelia_sheet.yaml' not found!
        echo Please ensure it is in the 'Aurelia_chroma' sibling folder or the current folder.
    )
)

echo [Aurelia-AI] Setup complete! You can now use launch_aurelia.bat
pause
