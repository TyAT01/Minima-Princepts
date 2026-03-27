#!/bin/bash
# This script starts the Aurelia Chroma Companion.
# It will first install the required dependencies,
# and then it will run the Discord bot.
#
# Make sure to set the following environment variables:
# - AURELIA_CHROMA_DISCORD_TOKEN
# - AURELIA_CHROMA_DISCORD_GUILD_ID
# - AURELIA_CHROMA_DISCORD_VOICE_CHANNEL_ID

# Change to the script's directory
cd "$(dirname "$0")"

echo "Installing dependencies..."
pip install -r requirements.txt

echo "Starting Aurelia Chroma Companion..."
python app.py --discord
