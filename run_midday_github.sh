#!/usr/bin/env bash
# run_midday_github.sh — Midday risk check for GitHub Actions.
#
# CLAUDE.md and routines.json have both described a 12:00 PM stop-loss check
# since the project started, but no workflow ever implemented it, so stop-losses
# were only evaluated once a day at the 9:45 AM session. A position could breach
# its stop at 10:00 AM and sit unmanaged until the next morning.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"

# Computed in US/Eastern, not UTC — see the GUARDRAIL comment below and
# run_eod_github.sh for why a UTC-only computation breaks across a scheduler
# delay that crosses midnight UTC, and drifts by an hour across DST.
DATE=$(TZ="America/New_York" date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "=== Midday Risk Check: $DATE $(TZ="America/New_York" date +%H:%M:%S) ET ==="

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set."
  echo "Add them as GitHub Secrets: Settings → Secrets → Actions"
  exit 1
fi

export APCA_BASE_URL="https://paper-api.alpaca.markets"

DAY=$(TZ="America/New_York" date +%u)
if [ "$DAY" -ge 6 ]; then
  echo "Weekend (day $DAY) — skipping midday check."
  exit 0
fi

cd "$AGENT_DIR"

# ── GUARDRAIL: only run while the market is open (10:00 AM–4:00 PM ET) ─────
# Derived directly from the America/New_York zone rather than a fixed UTC
# offset. The old "14:00-20:00 UTC" window was only correct during EDT — in
# EST it would skip an hour of real market-open time — and a delayed run
# crossing midnight UTC could read as hour 00-01 and be wrongly excluded here
# too, exactly as happened to the EOD guard on 2026-08-27 (see
# run_eod_github.sh). %-H is unpadded to avoid bash reading a leading-zero
# hour like "08" as an invalid octal literal and crashing the comparison.
HOUR_ET=$(TZ="America/New_York" date +%-H)
if [ "${TEST_MODE}" != "1" ] && { [ "$HOUR_ET" -lt 10 ] || [ "$HOUR_ET" -ge 16 ]; }; then
  echo "GUARDRAIL: outside market hours (${HOUR_ET}:00 ET) — skipping (not a failure)."
  exit 0
fi

echo ""
echo "--- [1/3] Phantom equity / corporate action check ---"
# Exit code 2 means suspect positions were found. Surface it loudly but do not
# fail the job — the stop-loss check below still needs to run.
python3 trade.py check-phantom | tee "$LOG_DIR/$DATE-phantom-check.json" || {
  echo "⚠️  PHANTOM EQUITY DETECTED — see $DATE-phantom-check.json"
  echo "⚠️  Reported portfolio value is inflated. Reconcile before trusting P&L."
}

echo ""
echo "--- [2/3] Stop-loss + inverse ETF max-hold check ---"
python3 trade.py check-stops | tee "$LOG_DIR/$DATE-midday-stops.json"

echo ""
echo "--- [3/3] Midday research (signal reversals) ---"
python3 research.py midday > "$LOG_DIR/$DATE-midday.json" \
  || echo "WARN: research.py midday failed — stop checks above still applied"

echo ""
echo "=== Midday check complete: $(TZ="America/New_York" date +%H:%M:%S) ET ==="
