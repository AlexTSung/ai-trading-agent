#!/bin/bash
# Forex trading agent — runs 4× daily via launchd
# Credentials loaded from ~/.oanda_credentials (chmod 600, never committed)
# Pipeline: screener → research → judge → trade → journal → optimizer

# Do NOT use set -e here — optimizer failure should not abort the run

# Load credentials
if [ -f "$HOME/.oanda_credentials" ]; then
    source "$HOME/.oanda_credentials"
else
    echo "[$(date)] ERROR: ~/.oanda_credentials not found. Exiting."
    exit 1
fi

if [ -z "$OANDA_API_KEY" ] || [ -z "$OANDA_ACCOUNT_ID" ]; then
    echo "[$(date)] ERROR: OANDA_API_KEY or OANDA_ACCOUNT_ID not set. Exiting."
    exit 1
fi

cd "/Users/alexsung/Downloads/AI Trading Agent"

MONTH=$(date +%Y-%m)
LOG_FILE="logs/forex/cron-${MONTH}.log"
mkdir -p logs/forex

echo "" >> "$LOG_FILE"
echo "════════════════════════════════════════" >> "$LOG_FILE"
echo "[$(date)] Starting forex agent run" >> "$LOG_FILE"
echo "════════════════════════════════════════" >> "$LOG_FILE"

# Step 1: Full scan + trade pipeline
/usr/bin/python3 run_forex.py >> "$LOG_FILE" 2>&1
TRADE_EXIT=$?
echo "[$(date)] Trade run finished (exit: $TRADE_EXIT)" >> "$LOG_FILE"

# Step 2: Optimizer — tune weights from accumulated journal data
# Runs after every session so the agent learns continuously
echo "[$(date)] Running optimizer (daily weight tuning)..." >> "$LOG_FILE"
/usr/bin/python3 forex_optimizer.py daily >> "$LOG_FILE" 2>&1
OPT_EXIT=$?
echo "[$(date)] Optimizer finished (exit: $OPT_EXIT)" >> "$LOG_FILE"

# Step 3: Weekly strategy scrape (Sundays only)
DAY_OF_WEEK=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DAY_OF_WEEK" = "7" ]; then
    echo "[$(date)] Sunday — running weekly strategy scrape..." >> "$LOG_FILE"
    /usr/bin/python3 forex_optimizer.py weekly >> "$LOG_FILE" 2>&1
    echo "[$(date)] Weekly scrape done." >> "$LOG_FILE"
fi

echo "[$(date)] Full run complete." >> "$LOG_FILE"
