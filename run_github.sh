#!/usr/bin/env bash
# run_github.sh — Pipeline runner for GitHub Actions (no MCP config needed).
# Keys come from environment variables set by GitHub Secrets.
# Never hardcode credentials here.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"

echo "=== Trading Agent Run: $DATE $(date -u +%H:%M:%S) UTC ==="

# Keys must be injected by GitHub Actions via secrets — never hardcoded
if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set."
  echo "Add them as GitHub Secrets: Settings → Secrets → Actions → New repository secret"
  exit 1
fi

export APCA_BASE_URL="https://paper-api.alpaca.markets"

# Weekend check
DAY=$(date +%u)
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

# ── GUARDRAIL G2: Time window ─────────────────────────────────────────────
# Skip time check when TEST_MODE=1 (for manual workflow_dispatch runs only)
if [ "${TEST_MODE}" != "1" ]; then
  HOUR_UTC=$(date -u +%H)
  MIN_UTC=$(date -u +%M)
  MINS=$((HOUR_UTC * 60 + MIN_UTC))
  if [ "$MINS" -gt $((15 * 60 + 30)) ]; then
    echo "GUARDRAIL G2: Past 11:30 AM ET — aborting pipeline."
    exit 1
  fi
fi

echo ""
echo "--- [1/5] Intelligence scan ---"
python3 intelligence.py morning || { echo "WARN: intelligence.py failed — continuing"; }

echo ""
echo "--- [2/5] Social screener ---"
python3 reddit_screener.py scan || { echo "WARN: reddit_screener.py failed — continuing"; }

echo ""
echo "--- [3/5] Research ---"
python3 research.py full
RESEARCH_EXIT=$?
if [ $RESEARCH_EXIT -ne 0 ]; then
  echo "GUARDRAIL G9: research.py failed (exit $RESEARCH_EXIT) — aborting steps 4-5, no trades placed."
  exit $RESEARCH_EXIT
fi

echo ""
echo "--- [4/5] Judge ---"
python3 judge.py session
JUDGE_EXIT=$?
if [ $JUDGE_EXIT -ne 0 ]; then
  echo "GUARDRAIL G8: judge.py failed (exit $JUDGE_EXIT) — aborting executor, no trades placed."
  exit $JUDGE_EXIT
fi

echo ""
echo "--- [5/5] Executor ---"
python3 trade.py session

echo ""
echo "=== Morning pipeline complete: $(date -u +%H:%M:%S) UTC ==="
