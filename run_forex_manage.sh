#!/bin/bash
# Forex position manager — runs every 30 min via cron
# Handles trailing stops, time stops, and partial closes only.
# No screener / research / new orders — fast and rate-limit safe.
# Credentials loaded from ~/.oanda_credentials (chmod 600, never committed)

set -e

# Load credentials
if [ -f "$HOME/.oanda_credentials" ]; then
    source "$HOME/.oanda_credentials"
else
    echo "[$(date)] ERROR: ~/.oanda_credentials not found. Exiting." >> "$HOME/forex_manage.log"
    exit 1
fi

if [ -z "$OANDA_API_KEY" ] || [ -z "$OANDA_ACCOUNT_ID" ]; then
    echo "[$(date)] ERROR: OANDA_API_KEY or OANDA_ACCOUNT_ID not set. Exiting." >> "$HOME/forex_manage.log"
    exit 1
fi

cd "/Users/alexsung/Downloads/AI Trading Agent"

MONTH=$(date +%Y-%m)
LOG_FILE="logs/forex/manage-${MONTH}.log"
mkdir -p logs/forex

echo "" >> "$LOG_FILE"
echo "[$(date)] --- position manager run ---" >> "$LOG_FILE"

/usr/bin/python3 - >> "$LOG_FILE" 2>&1 << 'PYEOF'
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else '.')

# Safety check — never run against live account
assert os.getenv("OANDA_ENV", "practice") == "practice", \
    "FATAL: OANDA_ENV must be 'practice'"

from forex_trade import manage_trailing_stops, check_time_stops
from forex_journal import sync_closed_trades

from datetime import datetime, timezone
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)

log("Trailing stops...")
try:
    manage_trailing_stops()
    log("  Trailing stops OK")
except Exception as e:
    log(f"  Trailing stops FAILED: {e}")

log("Time stops...")
try:
    check_time_stops()
    log("  Time stops OK")
except Exception as e:
    log(f"  Time stops FAILED: {e}")

log("Journal sync...")
try:
    sync_closed_trades()
    log("  Journal sync OK")
except Exception as e:
    log(f"  Journal sync FAILED: {e}")

log("Done.")
PYEOF

EXIT_CODE=$?
echo "[$(date)] manage run finished (exit: $EXIT_CODE)" >> "$LOG_FILE"
