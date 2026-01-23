@echo off
rem This script starts the Aurelia Chroma Companion.
rem It assumes you have already started your LLM server (e.g., llama.cpp or Ollama).
rem
rem Example for llama.cpp:
rem llama-server --model /path/to/your/model.gguf --port 8080
rem
rem Once the LLM server is running, you can run this script.

rem Change to the script's directory
cd /d "%~dp0"

echo "Starting Aurelia Chroma Companion with Discord and Dashboard..."
python app.py --discord --dashboard
