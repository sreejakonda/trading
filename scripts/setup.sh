#!/usr/bin/env bash
# One-time setup: install dependencies and create .env.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Trading system setup =="
python3 --version

echo "Installing dependencies..."
python3 -m pip install --quiet -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  echo "Created .env from template — edit it to set your keys/mode."
else
  echo ".env already exists — leaving it untouched."
fi

echo
echo "Verifying configuration:"
python3 "$DIR/trader.py" doctor

echo
echo "Done. Try a scan:  python3 $DIR/trader.py scan --force"
