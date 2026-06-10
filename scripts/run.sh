#!/usr/bin/env bash
# Cron-friendly wrapper: run one scan. The CLI auto-loads ../.env.
#
# Example crontab (every 5 min, weekdays, 9:30a–4p ET ≈ 13:30–20:00 UTC in EST;
# shift to 12:30–19:00 UTC during daylight saving):
#   */5 13-20 * * 1-5  /path/to/trading/scripts/run.sh >> /path/to/trading/scan.log 2>&1
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$DIR/trader.py" scan "$@"
