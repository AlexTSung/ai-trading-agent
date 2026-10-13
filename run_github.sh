#!/usr/bin/env bash
# run_github.sh — Pipeline runner for GitHub Actions (no MCP config needed).
# Keys come from environment variables set by GitHub Secrets.
# Never hardcode credentials here.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"

# Computed in US/Eastern, not UTC — see run_eod_github.sh for why a UTC-only
# computation breaks across a scheduler delay that crosses midnight UTC, and
# drifts by an hour across DST.
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

# ── GUARDRAIL G1: Minimum re-run interval (not one-shot-per-day) ──────────
# Until 2026-10-13 this was a one-shot lock: if $DATE.json existed at all,
# skip. The first successful run of the day "used up" the guardrail, and
# every later trigger that day was a no-op. That was the correct trade-off
# when schedule: fired at most a handful of times a day and
# landing any real run at all inside market hours was the binding constraint.
# It stopped being correct the moment the external cron-job.org trigger
# started firing workflow_dispatch reliably every hour, around the clock, by
# design (Alex confirmed 2026-10-13: "I am running the cron jobs every hour").
# Intelligence, the social/market screeners and news sentiment inside
# research.py all go stale well within a day — a headline on a held position,
# a fresh insider buy, a new discovery ticker — and the one-shot lock was
# silently discarding every later re-check. It cost a real trading day:
# 2026-10-13's only research+judge run landed at 12:01 AM ET with
# market_open=false, the Judge APPROVEd NVDA/MSFT/NU off midnight-stale data,
# and every subsequent hourly trigger during actual market hours skipped
# outright because $LOG_DIR/$DATE.json already existed — so none of those
# approvals ever reached the executor.
#
# Fixed by re-running whenever $DATE.json is missing OR stale by more than
# 50 minutes, so an hourly external trigger produces an hourly real pipeline
# run instead of locking to whichever trigger happened to land first. 50, not
# 60, absorbs an hourly ping landing a few minutes early without causing a
# spurious double-run in the same hour.
#
# This does not loosen any trading criterion — buy_with_guardrails still only
# buys a symbol not already held (trade.py's `sym not in open_syms` check), so
# re-running the pipeline hourly cannot re-buy a position it already opened
# earlier the same day. It only changes how often stale-but-cached research
# gets refreshed.
MIN_RERUN_SECONDS=$((50 * 60))
if [ -f "$LOG_DIR/$DATE.json" ]; then
  LAST_RUN_EPOCH=$(stat -c %Y "$LOG_DIR/$DATE.json" 2>/dev/null || stat -f %m "$LOG_DIR/$DATE.json")
  NOW_EPOCH=$(date +%s)
  AGE_SECONDS=$((NOW_EPOCH - LAST_RUN_EPOCH))
  if [ "$AGE_SECONDS" -lt "$MIN_RERUN_SECONDS" ]; then
    echo "GUARDRAIL G1: Research ran $((AGE_SECONDS / 60))m ago (< 50m) — skipping."
    exit 0
  fi
  echo "GUARDRAIL G1: Last research run was $((AGE_SECONDS / 60))m ago — re-running for fresh data."
fi

# ── GUARDRAIL G2 (removed 2026-10-13) ─────────────────────────────────────
# Previously skipped the pipeline after 4:00 PM ET. That existed only to stop
# a single daily shot from landing so late it was pointless — it was never a
# trading-hours safety control; trade.py's own `research.get("market_open")`
# check (sourced from Alpaca's real-time clock) is what actually blocks order
# placement outside market hours, and that check is untouched by this file.
# With G1 now re-running hourly around the clock by design, an after-hours or
# overnight run is no longer a wasted last chance — it is exactly the
# continuous news/intelligence scanning Alex asked for on 2026-10-13 ("always
# scanning the internet for new data related to any stocks currently in
# portfolio... or any future trades"), and it costs nothing in safety since
# trade.py still refuses to place any order while is_open is false.
# TEST_MODE no longer changes this script's behavior — there is no longer a
# time window to bypass — but the input stays wired in morning-pipeline.yml
# for parity with the other three pipelines' workflow_dispatch inputs.

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
