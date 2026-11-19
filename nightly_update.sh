#!/bin/bash
# nightly_update.sh
# Runs automatically every night on the server (set up by server_setup.sh):
# gets the newest code and data from GitHub, retrains the crowd model, and restarts the bot.
# You can also run it by hand any time:  bash nightly_update.sh

cd "$(dirname "$0")"
echo "=== $(date) ==="

# Get the latest version from GitHub. (reset --hard throws away the model file the server
# made last night, so it never clashes - .env and venv are untouched because Git ignores them.)
git fetch --quiet origin main && git reset --quiet --hard origin/main

venv/bin/pip install --quiet -r requirements.txt
venv/bin/python train.py > last_train.log 2>&1 && grep "My model" last_train.log
sudo systemctl restart transit-bot
echo "Bot restarted"
