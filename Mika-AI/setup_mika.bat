@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo [Mika-AI] Initializing Setup in %CD%...

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found! Please install Python 3.10+ and add it to your PATH.
    pause
    exit /b 1
)

:: Create virtual environment
if not exist ".venv" (
    echo [Mika-AI] Creating virtual environment...
    python -m venv .venv
) else (
    echo [Mika-AI] Virtual environment already exists.
)

:: Activate venv and install requirements
echo [Mika-AI] Activating environment and installing dependencies...
call .venv\Scripts\activate
python -m pip install --upgrade pip

if exist "requirements.txt" (
    pip install -r requirements.txt
) else (
    echo [ERROR] requirements.txt not found in %CD%
    pause
    exit /b 1
)

:: [FIX] Workaround for ctranslate2 ROCm path error on Windows
echo [Mika-AI] Applying compatibility patches...
set "ROCM1=.venv\Lib\site-packages\_rocm_sdk_core\bin"
set "ROCM2=.venv\Lib\site-packages\_rocm_sdk_libraries_custom\bin"

if not exist "%ROCM1%" (
    echo [Mika-AI] Creating compatibility directory 1 for ctranslate2...
    mkdir "%ROCM1%" >nul 2>&1
)
if not exist "%ROCM2%" (
    echo [Mika-AI] Creating compatibility directory 2 for ctranslate2...
    mkdir "%ROCM2%" >nul 2>&1
)

echo [Mika-AI] Verifying persona files...
if not exist "mika_sheet.yaml" (
    echo [WARNING] Character sheet 'mika_sheet.yaml' not found in %CD%!
    echo Please ensure it is in the same folder as this script.
)

echo [Mika-AI] Setup complete! You can now use launch_mika.bat
pause
