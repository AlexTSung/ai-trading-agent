#!/usr/bin/env bash
# run_github.sh — Pipeline runner for GitHub Actions (no MCP config needed).
# Keys come from environment variables set by GitHub Secrets.
# Never hardcode credentials here.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"

# Computed in US/Eastern, not UTC — see the GUARDRAIL G2 comment below and
# run_eod_github.sh for why a UTC-only computation breaks across a scheduler
# delay that crosses midnight UTC, and drifts by an hour across DST.
DATE=$(TZ="America/New_York" date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "=== Trading Agent Run: $DATE $(TZ="America/New_York" date +%H:%M:%S) ET ==="

# Keys must be injected by GitHub Actions via secrets — never hardcoded
if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set."
  echo "Add them as GitHub Secrets: Settings → Secrets → Actions → New repository secret"
  exit 1
fi

export APCA_BASE_URL="https://paper-api.alpaca.markets"

# Weekend check
DAY=$(TZ="America/New_York" date +%u)
if [ "$DAY" -ge 6 ]; then
  echo "Weekend (day $DAY) — skipping pipeline."
  exit 0
fi

cd "$AGENT_DIR"

# ── GUARDRAIL G1: Idempotency ─────────────────────────────────────────────
if [ -f "$LOG_DIR/$DATE.json" ]; then
  echo "GUARDRAIL G1: Morning pipeline already ran today ($DATE) — skipping."
  exit 0
fi

# ── GUARDRAIL G2: Time window — must be at or before 11:30 AM ET ─────────
# Skip time check when TEST_MODE=1 (for manual workflow_dispatch runs only)
#
# Derived directly from the America/New_York zone, not a fixed UTC offset.
# The old "15:30 UTC" threshold assumed EDT (UTC-4); during EST it is off by
# an hour, and — the failure mode that actually hit the EOD guardrail on
# 2026-08-27 — a scheduler delay that pushes HOUR_UTC/MIN_UTC across midnight
# UTC makes MINS wrap back to a small number, which this check would read as
# "still morning" and run again on the wrong side of midnight. See
# run_eod_github.sh for the concrete incident. %-H/%-M are unpadded to avoid
# bash treating a leading-zero hour or minute (e.g. "08") as an invalid octal
# literal and crashing the arithmetic.
if [ "${TEST_MODE}" != "1" ]; then
  HOUR_ET=$(TZ="America/New_York" date +%-H)
  MIN_ET=$(TZ="America/New_York" date +%-M)
  MINS_ET=$((HOUR_ET * 60 + MIN_ET))
  if [ "$MINS_ET" -gt $((11 * 60 + 30)) ]; then
    echo "GUARDRAIL G2: Past 11:30 AM ET — skipping pipeline (not a failure)."
    exit 0
  fi
fi

echo ""
echo "--- [1/7] Intelligence scan (VIX, macro, Reddit, global markets) ---"
python3 intelligence.py morning || { echo "WARN: intelligence.py failed — continuing"; }

echo ""
echo "--- [2/7] Social screener (StockTwits trending) ---"
python3 reddit_screener.py scan || { echo "WARN: reddit_screener.py failed — continuing"; }

echo ""
echo "--- [3/7] Full market screener (S&P 500 + Nasdaq 100 + news firehose) ---"
python3 screener.py scan || { echo "WARN: screener.py failed — continuing without screener tickers"; }

echo ""
echo "--- [4/7] Research (watchlist + all discovered tickers) ---"
python3 research.py full
RESEARCH_EXIT=$?
if [ $RESEARCH_EXIT -ne 0 ]; then
  echo "GUARDRAIL G9: research.py failed (exit $RESEARCH_EXIT) — aborting, no trades placed."
  exit $RESEARCH_EXIT
fi

echo ""
echo "--- [5/7] Judge ---"
python3 judge.py session
JUDGE_EXIT=$?
if [ $JUDGE_EXIT -ne 0 ]; then
  echo "GUARDRAIL G8: judge.py failed (exit $JUDGE_EXIT) — aborting executor, no trades placed."
  exit $JUDGE_EXIT
fi

echo ""
echo "--- [6/7] Executor ---"
python3 trade.py session

echo ""
echo "--- [7/7] Done ---"
echo "=== Morning pipeline complete: $(TZ="America/New_York" date +%H:%M:%S) ET ==="
