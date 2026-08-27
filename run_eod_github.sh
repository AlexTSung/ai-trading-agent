#!/usr/bin/env bash
# run_eod_github.sh — EOD pipeline for GitHub Actions.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"

# Trading-day date is computed in US/Eastern, not UTC. GitHub Actions scheduled
# workflows can run late — sometimes hours late under platform load — and a
# UTC-only date breaks the instant a delayed run crosses midnight UTC: on
# 2026-08-27 a run intended for 21:15 UTC (5:15 PM ET, 2026-08-26) actually
# started at 00:46 UTC the NEXT day. Plain `date +%Y-%m-%d` would have journaled
# that session's EOD under 2026-08-27 instead of the trading day it belonged to.
DATE=$(TZ="America/New_York" date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "=== EOD Run: $DATE $(TZ="America/New_York" date +%H:%M:%S) ET ==="

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: Alpaca keys not set in environment."
  exit 1
fi

export APCA_BASE_URL="https://paper-api.alpaca.markets"

DAY=$(TZ="America/New_York" date +%u)
[ "$DAY" -ge 6 ] && echo "Weekend — skipping EOD." && exit 0

cd "$AGENT_DIR"

# ── GUARDRAIL G1: Idempotency ─────────────────────────────────────────────
if grep -q "Reviewer Summary" "$LOG_DIR/$DATE.md" 2>/dev/null; then
  echo "GUARDRAIL G1: EOD already completed today — skipping."
  exit 0
fi

# ── GUARDRAIL G3: Time window — must be after 4:00 PM ET ──────────────────
# Real 2026-08-27 failure: the run above started at 00:46 UTC (a ~3.5h
# scheduler delay past the 21:15 UTC cron target), which is 20:46 ET —
# hours AFTER market close. The old check computed HOUR_UTC="00" and
# evaluated "00 -lt 20" as true, aborting a legitimate EOD session as "too
# early" when it was actually very late. Comparing a raw UTC hour to a
# fixed threshold silently breaks the moment a delayed run crosses midnight
# UTC. Deriving the hour directly from the America/New_York zone fixes that
# (it reflects true ET wall-clock time no matter which UTC calendar day the
# runner lands on) and is DST-safe for free — the old fixed "20:00 UTC"
# threshold was only ever correct during EDT; during EST it let EOD start as
# early as 3:00 PM ET, an hour before the market even closes.
#
# %-H is unpadded (no leading zero) to dodge a real bash footgun: bash parses
# a leading-zero numeral as octal in arithmetic/test context, so an hour of
# "08" or "09" — not a valid octal digit — crashes `[ "$HOUR" -lt 20 ]`
# outright with "value too great for base". Confirmed by reproducing it.
HOUR_ET=$(TZ="America/New_York" date +%-H)
if [ "$HOUR_ET" -lt 16 ]; then
  echo "GUARDRAIL G3: Before 4:00 PM ET (currently ${HOUR_ET}:00 ET) — too early for EOD. Aborting."
  exit 1
fi

echo ""
echo "--- [1/3] Reviewer ---"
python3 reviewer.py eod || echo "WARN: reviewer.py failed — continuing"

echo ""
echo "--- [2/3] Journal ---"
python3 journal.py eod || echo "WARN: journal.py failed — continuing"

echo ""
echo "--- [3/3] Learning Agent ---"
python3 learning_agent.py eod || echo "WARN: learning_agent.py failed — non-critical"

echo ""
echo "--- Trade learnings (per-source, per-tier, hold time, veto cost) ---"
# Evidence only — changes no parameter. judge.py's LEARNING_TIERS decides how
# much of this may affect scoring.
python3 learning_analytics.py \
  | tee "$LOG_DIR/$DATE-learnings.md" \
  || echo "WARN: learning_analytics.py failed — non-critical"

echo ""
echo "=== EOD complete: $(TZ="America/New_York" date +%H:%M:%S) ET ==="
