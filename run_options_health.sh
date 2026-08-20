#!/bin/bash
# Daily options health check — runs at 8:00 AM ET Mon–Fri
# Verifies all cron jobs fired, positions are healthy, Alpaca is reachable.
# Prints a structured report to log and stdout.
#
# Crontab entry:
#   0 8 * * 1-5 /bin/bash "/Users/alexsung/Downloads/AI Trading Agent/run_options_health.sh"

set -e

if [ -f "$HOME/.alpaca_credentials" ]; then
    source "$HOME/.alpaca_credentials"
else
    echo "[$(date)] ERROR: ~/.alpaca_credentials not found. Exiting."
    exit 1
fi

export ALPACA_PAPER_TRADE=true

cd "/Users/alexsung/Downloads/AI Trading Agent"

MONTH=$(date +%Y-%m)
LOG_FILE="logs/options/health-${MONTH}.log"
mkdir -p logs/options

echo ""                                              >> "$LOG_FILE"
echo "[$(date)] === daily health check ==="          >> "$LOG_FILE"

/usr/bin/python3 options_health_check.py >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date)] health check finished (exit: $EXIT_CODE)" >> "$LOG_FILE"

# Exit 2 = critical failure — surface it loudly
if [ "$EXIT_CODE" -eq 2 ]; then
    echo "[$(date)] *** CRITICAL: options system health check FAILED — review logs/options/health-${MONTH}.log ***"
    exit 2
fi
