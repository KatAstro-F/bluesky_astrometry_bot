#!/bin/bash

# Path to your Python script
SCRIPT_PATH="/home/kat/astrometry/bot.py"
# Path to your virtual environment activation script
VENV_ACTIVATE="/home/kat/astrometry/venv/bin/activate"
# Directory containing your script
SCRIPT_DIR="/home/kat/astrometry"

# Check if the script is already running
if pgrep -f "$SCRIPT_PATH" > /dev/null; then
    echo "Script is already running."
    exit 0
fi

# Activate virtual environment and run the script
source "$VENV_ACTIVATE"
cd "$SCRIPT_DIR"
python3 "$SCRIPT_PATH"

