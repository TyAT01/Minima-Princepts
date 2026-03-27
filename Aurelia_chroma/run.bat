@echo off
rem This script starts the Aurelia Chroma Companion.
rem It will first install the required dependencies,
rem and then it will run the Discord bot.
rem
rem Make sure to set the following environment variables:
rem - AURELIA_CHROMA_DISCORD_TOKEN
rem - AURELIA_CHROMA_DISCORD_GUILD_ID
rem - AURELIA_CHROMA_DISCORD_VOICE_CHANNEL_ID

rem Change to the script's directory
cd /d "%~dp0"

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Starting Aurelia Chroma Companion..."
python app.py --discord
