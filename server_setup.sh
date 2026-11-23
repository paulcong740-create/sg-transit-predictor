#!/bin/bash
# server_setup.sh
# Run this ONCE on the Google Cloud server. It:
#   1. installs Python and the project's libraries
#   2. adds 1 GB of "swap" memory (the free server only has 1 GB of RAM)
#   3. trains the crowd model on the server
#   4. starts the bot as a background service that runs 24/7 and restarts itself if it crashes
#   5. schedules a nightly update (new data from GitHub -> retrain the model -> restart the bot)
#
# Usage:  bash server_setup.sh

set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
USER_NAME="$(whoami)"

if [ ! -f .env ]; then
    echo "❌ No .env file here yet. Create it first (see the guide), then run this again."
    exit 1
fi

echo "== 1/5 Installing Python =="
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git

echo "== 2/5 Adding swap memory =="
if [ ! -f /swapfile ]; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "== 3/5 Installing the project's libraries (takes a few minutes) =="
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "== 4/5 Training the crowd model =="
venv/bin/python train.py

echo "== 5/5 Starting the bot as a 24/7 service =="
sudo tee /etc/systemd/system/transit-bot.service > /dev/null <<EOF
[Unit]
Description=SG Transit Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now transit-bot

# Nightly update at 4:30 am Singapore time (20:30 UTC - the server's clock is in UTC)
chmod +x nightly_update.sh
( crontab -l 2>/dev/null | grep -v nightly_update.sh ; \
  echo "30 20 * * * bash $DIR/nightly_update.sh >> $DIR/nightly.log 2>&1" ) | crontab -

echo ""
echo "✅ Done! The bot is running 24/7."
echo "   Check it:      sudo systemctl status transit-bot"
echo "   See its logs:  sudo journalctl -u transit-bot -n 30"
