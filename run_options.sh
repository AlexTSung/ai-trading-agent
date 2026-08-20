#!/bin/bash
# Options trading run — executes once per trading day via cron
# Screens watchlist, selects strategies, places paper trades on Alpaca.
# Credentials loaded from ~/.alpaca_credentials (chmod 600, never committed)
#
# Suggested crontab (runs at 9:35 AM ET Mon–Fri):
#   35 9 * * 1-5 /bin/bash /Users/alexsung/Downloads/AI\ Trading\ Agent/run_options.sh

set -e

# Load credentials
if [ -f "$HOME/.alpaca_credentials" ]; then
    source "$HOME/.alpaca_credentials"
else
    echo "[$(date)] ERROR: ~/.alpaca_credentials not found. Exiting."
    exit 1
fi

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
    echo "[$(date)] ERROR: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set. Exiting."
    exit 1
fi

# Safety: paper trading only
export ALPACA_PAPER_TRADE=true

cd "/Users/alexsung/Downloads/AI Trading Agent"

MONTH=$(date +%Y-%m)
LOG_FILE="logs/options/cron-${MONTH}.log"
mkdir -p logs/options

echo ""                                           >> "$LOG_FILE"
echo "[$(date)] === options run ==="              >> "$LOG_FILE"

/usr/bin/python3 run_options.py >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date)] options run finished (exit: $EXIT_CODE)" >> "$LOG_FILE"
