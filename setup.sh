#!/bin/bash
# One-time setup for the day trading signal engine
set -e

echo "=== Day Trader Setup ==="

# Check Python
python3 --version || { echo "ERROR: python3 not found"; exit 1; }

# Install dependencies
echo "Installing Python dependencies..."
pip3 install --quiet yfinance anthropic pytz

# Prompt for ANTHROPIC_API_KEY if not set
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo ""
  echo "ANTHROPIC_API_KEY is not set."
  echo "Get your key from: https://console.anthropic.com/settings/api-keys"
  read -p "Paste your API key (or press Enter to skip): " key
  if [ -n "$key" ]; then
    # Add to ~/.zshrc (or ~/.bashrc)
    PROFILE="$HOME/.zshrc"
    [ -f "$HOME/.bashrc" ] && PROFILE="$HOME/.bashrc"
    echo "" >> "$PROFILE"
    echo "# Day trader — Claude API key" >> "$PROFILE"
    echo "export ANTHROPIC_API_KEY=\"$key\"" >> "$PROFILE"
    export ANTHROPIC_API_KEY="$key"
    echo "Saved to $PROFILE"
  fi
fi

# Test the script
echo ""
echo "Running a test scan..."
python3 "$HOME/trading/day_trader.py"

# Install cron job
echo ""
read -p "Install cron job to run every 15 min on weekdays during market hours? (y/N) " ans
if [[ "$ans" =~ ^[Yy] ]]; then
  CRON_LINE="*/15 14-21 * * 1-5 ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY /usr/local/bin/python3 $HOME/trading/day_trader.py >> $HOME/trading/signals.log 2>&1"
  # 14-21 UTC = 9AM-4PM ET (EST, no DST). Adjust to 13-20 in summer (EDT).
  (crontab -l 2>/dev/null | grep -v "day_trader.py"; echo "$CRON_LINE") | crontab -
  echo "Cron job installed. Logs → ~/trading/signals.log"
  echo ""
  echo "NOTE: UTC hours used. Adjust 14-21 → 13-20 during daylight saving time (Mar–Nov)."
  crontab -l | grep day_trader
fi

echo ""
echo "Setup complete."
echo "  Script:  ~/trading/day_trader.py"
echo "  Logs:    ~/trading/signals.log"
echo "  Manual:  python3 ~/trading/day_trader.py"
