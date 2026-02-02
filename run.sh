#!/bin/bash

# Change to the script's directory
cd "$(dirname "$0")"

# Check if python3 is installed
if ! command -v python3 &> /dev/null
then
    echo "❌ python3 could not be found. Please install it."
    exit 1
fi

# Run the master launcher
python3 master_launch.py "$@"
