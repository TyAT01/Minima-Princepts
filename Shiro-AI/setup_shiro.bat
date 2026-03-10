@echo off
setlocal enabledelayedexpansion

echo [Shiro-AI] Initializing Setup in %CD%...

:: 1. Create Virtual Environment
if not exist "venv" (
    echo [Shiro-AI] Creating virtual environment...
    python -m venv venv
) else (
    echo [Shiro-AI] Virtual environment already exists.
)

:: 2. Install Dependencies
echo [Shiro-AI] Activating environment and installing dependencies...
call venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt

:: 3. Post-install fixes (ctranslate2 path bug)
echo [Shiro-AI] Applying compatibility patches...
set "PATCH_DIR1=%CD%\venv\Lib\site-packages\ctranslate2\..\..\ctranslate2_data"
set "PATCH_DIR2=%CD%\venv\Lib\site-packages\ctranslate2\..\..\..\ctranslate2_data"

if not exist "%PATCH_DIR1%" (
    echo [Shiro-AI] Creating compatibility directory 1 for ctranslate2...
    mkdir "%PATCH_DIR1%"
)
if not exist "%PATCH_DIR2%" (
    echo [Shiro-AI] Creating compatibility directory 2 for ctranslate2...
    mkdir "%PATCH_DIR2%"
)

:: 4. Verify Persona
echo [Shiro-AI] Verifying persona files...
if not exist "shiro_sheet.yaml" (
    echo [WARNING] Character sheet 'shiro_sheet.yaml' not found in %CD%!
)

echo [Shiro-AI] Setup complete! You can now use launch_shiro.bat
pause
