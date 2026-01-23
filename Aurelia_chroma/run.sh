#!/bin/bash
# This script starts the Aurelia Chroma Companion.
# It assumes you have already started your LLM server (e.g., llama.cpp or Ollama).
#
# Example for llama.cpp:
# llama-server --model /path/to/your/model.gguf --port 8080
#
# Once the LLM server is running, you can run this script.

# Change to the script's directory
cd "$(dirname "$0")"

echo "Starting Aurelia Chroma Companion with Discord and Dashboard..."
python app.py --discord --dashboard
