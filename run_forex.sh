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

# Step 2: Graduation checker + post-trade learning loop
# Learns from every closed trade: adjusts weights, deprioritizes losing pairs,
# updates graduation progress report
echo "[$(date)] Running graduation checker + learning loop..." >> "$LOG_FILE"
/usr/bin/python3 forex_graduation.py eod >> "$LOG_FILE" 2>&1
GRAD_EXIT=$?
echo "[$(date)] Graduation + learning finished (exit: $GRAD_EXIT)" >> "$LOG_FILE"

# Step 3: Optimizer — pattern detection + additional weight tuning
echo "[$(date)] Running optimizer (daily weight tuning)..." >> "$LOG_FILE"
/usr/bin/python3 forex_optimizer.py daily >> "$LOG_FILE" 2>&1
OPT_EXIT=$?
echo "[$(date)] Optimizer finished (exit: $OPT_EXIT)" >> "$LOG_FILE"

# Step 4: Weekly strategy scrape (Sundays only)
DAY_OF_WEEK=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DAY_OF_WEEK" = "7" ]; then
    echo "[$(date)] Sunday — running weekly strategy scrape..." >> "$LOG_FILE"
    /usr/bin/python3 forex_optimizer.py weekly >> "$LOG_FILE" 2>&1
    echo "[$(date)] Weekly scrape done." >> "$LOG_FILE"
fi

echo "[$(date)] Full run complete." >> "$LOG_FILE"
