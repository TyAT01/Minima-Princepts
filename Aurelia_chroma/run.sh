#!/bin/bash

# --- Aurelia Vale Launcher (Linux/macOS) ---
# Optimized for high-spec hardware
# Supports running from external drives

# Change to the script's directory
cd "$(dirname "$0")"

echo ""
echo " 🌸 Starting Aurelia Vale Setup... 🌸"
echo ""

# --- Virtual Environment Setup ---
if [ -d ".venv" ]; then
    echo "[INFO] Existing virtual environment found."
else
    echo "[INFO] Creating new virtual environment in .venv folder..."
    python3 -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create virtual environment."
        exit 1
    fi
fi

echo "[INFO] Activating virtual environment..."
source .venv/bin/activate
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to activate virtual environment."
    exit 1
fi

echo "[INFO] Ensuring pip is up to date..."
pip install --upgrade pip > /dev/null 2>&1

echo "[INFO] Installing/Updating dependencies..."
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] Dependency installation failed."
    exit 1
fi

echo ""
echo "[SUCCESS] Environment is ready!"
echo ""
echo "Starting Aurelia Vale Companion..."
echo ""

# Run the application
python app.py "$@"
