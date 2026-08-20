#!/bin/bash
# Options position manager — runs every 30 min via cron
# Checks Greeks drift, DTE danger zones, P&L targets/stops.
# Does NOT place new trades — only manages existing positions.
# Credentials loaded from ~/.alpaca_credentials (chmod 600, never committed)
#
# Crontab entry (every 30 min during market hours, Mon–Fri):
#   */30 9-16 * * 1-5 /bin/bash "/Users/alexsung/Downloads/AI Trading Agent/run_options_manage.sh"

set -e

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

export ALPACA_PAPER_TRADE=true

cd "/Users/alexsung/Downloads/AI Trading Agent"

MONTH=$(date +%Y-%m)
LOG_FILE="logs/options/manage-${MONTH}.log"
mkdir -p logs/options

echo ""                                              >> "$LOG_FILE"
echo "[$(date)] --- options monitor run ---"         >> "$LOG_FILE"

/usr/bin/python3 options_monitor.py >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date)] monitor run finished (exit: $EXIT_CODE)" >> "$LOG_FILE"
