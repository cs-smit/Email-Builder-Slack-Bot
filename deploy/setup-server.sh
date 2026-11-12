#!/usr/bin/env bash
# Installs the email bot on an Ubuntu server (Oracle Cloud Always Free, ARM or x86) and runs it
# as a background service that starts on boot and restarts if it crashes.
#
# Run it from the bot's folder on the server:   bash deploy/setup-server.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="$(id -un)"
cd "$APP_DIR"

if [ ! -f .env ]; then
  echo "Missing .env in $APP_DIR. Copy it from your Mac first (see docs/oracle-cloud.md)."; exit 1
fi
if ! grep -qE '^(CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY)=.+' .env; then
  echo "Add Claude access to .env, then run this again:"
  echo "  CLAUDE_CODE_OAUTH_TOKEN=...   (your Claude subscription: run 'claude setup-token' on your Mac)"
  echo "  or ANTHROPIC_API_KEY=...      (pay-per-use key from console.anthropic.com)"
  exit 1
fi

echo "==> System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-venv python3-pip curl ca-certificates rsync \
  fonts-liberation fonts-dejavu-core fonts-noto-core

echo "==> Node.js 22 and Claude Code"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
sudo npm install -g @anthropic-ai/claude-code
CLAUDE_BIN="$(command -v claude)"

echo "==> Python packages"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Chromium for screenshots"
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

grep -q '^CLAUDE_BIN=' .env || echo "CLAUDE_BIN=$CLAUDE_BIN" >> .env
grep -q '^BROWSER_CHANNEL=' .env || echo "BROWSER_CHANNEL=" >> .env

echo "==> Checking Claude Code can sign in"
set -a; . ./.env; set +a
if ! "$CLAUDE_BIN" -p "Reply with the single word: ready" --output-format text 2>&1 | grep -qi ready; then
  echo "Claude Code didn't answer. Check CLAUDE_CODE_OAUTH_TOKEN (or ANTHROPIC_API_KEY) in .env."; exit 1
fi

echo "==> Background service"
sudo tee /etc/systemd/system/emailbot.service >/dev/null <<UNIT
[Unit]
Description=Email Builder (Slack bot)
After=network-online.target
Wants=network-online.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
Environment=HOME=$HOME
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=$APP_DIR/.venv/bin/python -m emailbot.slack_app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now emailbot
sleep 8
sudo systemctl --no-pager status emailbot | head -15
echo
echo "Done. Follow the logs with:  sudo journalctl -u emailbot -f"
