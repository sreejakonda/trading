#!/bin/bash
# Cron wrapper — loads env vars then runs the signal engine
ENV_FILE="$(dirname "$0")/.env"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"
exec /usr/local/bin/python3 "$(dirname "$0")/day_trader.py"
