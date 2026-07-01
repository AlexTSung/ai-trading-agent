#!/usr/bin/env bash
# run_eod_github.sh — EOD pipeline for GitHub Actions.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "=== EOD Run: $DATE $(date -u +%H:%M:%S) UTC ==="

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: Alpaca keys not set in environment."
  exit 1
fi

export APCA_BASE_URL="https://paper-api.alpaca.markets"

DAY=$(date +%u)
[ "$DAY" -ge 6 ] && echo "Weekend — skipping EOD." && exit 0

cd "$AGENT_DIR"

# ── GUARDRAIL G1: Idempotency ─────────────────────────────────────────────
if grep -q "Reviewer Summary" "$LOG_DIR/$DATE.md" 2>/dev/null; then
  echo "GUARDRAIL G1: EOD already completed today — skipping."
  exit 0
fi

# ── GUARDRAIL G3: Time window — must be after 21:00 UTC (5:00 PM ET) ──────
HOUR_UTC=$(date -u +%H)
if [ "$HOUR_UTC" -lt 20 ]; then
  echo "GUARDRAIL G3: Before 4:00 PM ET — too early for EOD. Aborting."
  exit 1
fi

echo ""
echo "--- [1/2] Reviewer ---"
python3 reviewer.py eod || echo "WARN: reviewer.py failed — continuing to journal"

echo ""
echo "--- [2/2] Journal ---"
python3 journal.py eod || echo "WARN: journal.py failed"

echo ""
echo "=== EOD complete: $(date -u +%H:%M:%S) UTC ==="
