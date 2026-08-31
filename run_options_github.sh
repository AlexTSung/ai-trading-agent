#!/usr/bin/env bash
# run_options_github.sh — Options pipeline for GitHub Actions.
#
# CLAUDE.md's own options-improvements list has said "Schedule it" since the
# strategy was first documented: equities and forex run in GitHub Actions,
# options only ever ran from a local run_options.sh with a hardcoded machine
# path and credentials read from ~/.alpaca_credentials — so it only managed
# positions when a laptop happened to be on. Short options are the LEAST safe
# thing to monitor intermittently: a short-premium position does not pause
# decaying or gamma-accelerating just because nobody's machine is running.
#
# This mirrors run_github.sh / run_midday_github.sh exactly: keys from
# GitHub Secrets, "today" computed in US/Eastern (not the runner's UTC clock —
# see run_eod_github.sh for why that distinction is load-bearing), a weekend
# skip, and an idempotency marker so redundant scheduled triggers are free.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/options"
DATE=$(TZ="America/New_York" date +%Y-%m-%d)
DONE_MARKER="$LOG_DIR/$DATE.done"

mkdir -p "$LOG_DIR"

echo "=== Options Run: $DATE $(TZ="America/New_York" date +%H:%M:%S) ET ==="

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: APCA_API_KEY_ID or APCA_API_SECRET_KEY not set."
  echo "Add them as GitHub Secrets: Settings -> Secrets -> Actions"
  exit 1
fi

# Belt + suspenders — run_options.py asserts this again at import of
# options_trade, but fail here with a clear message rather than a traceback.
export ALPACA_PAPER_TRADE=true
export APCA_BASE_URL="https://paper-api.alpaca.markets"

DAY=$(TZ="America/New_York" date +%u)
[ "$DAY" -ge 6 ] && echo "Weekend — skipping options run." && exit 0

cd "$AGENT_DIR"

# ── GUARDRAIL G1: Idempotency ─────────────────────────────────────────────
# run_options.py has no daily summary file of its own to grep the way
# journal.py's "Reviewer Summary" marker works for equities, so this script
# writes its own marker on a successful run. Every redundant scheduled
# trigger past the first becomes a free no-op once this exists.
if [ -f "$DONE_MARKER" ]; then
  echo "GUARDRAIL G1: Options run already completed today — skipping."
  exit 0
fi

# ── GUARDRAIL G2: Time window — 9:35 AM to 11:30 AM ET ────────────────────
# Same window as the equities morning pipeline. IV rank and the option chain
# are freshest right after open; running this against a stale mid-afternoon
# snapshot is worse than not running, so it does not run all day like the
# equities midday check does.
HOUR_ET=$(TZ="America/New_York" date +%-H)
MIN_ET=$(TZ="America/New_York" date +%-M)
MINS_ET=$((HOUR_ET * 60 + MIN_ET))
if [ "${TEST_MODE}" != "1" ]; then
  if [ "$MINS_ET" -lt $((9 * 60 + 35)) ] || [ "$MINS_ET" -gt $((11 * 60 + 30)) ]; then
    printf "GUARDRAIL G2: outside the 9:35-11:30 AM ET window (%02d:%02d ET) — skipping (not a failure).\n" \
      "$HOUR_ET" "$MIN_ET"
    exit 0
  fi
fi

echo ""
echo "--- Options pipeline (manage existing, then scan for new trades) ---"
# `cmd; EXIT_CODE=$?` would never reach the assignment under `set -e` — a
# failing command exits the script immediately, before the next line runs.
# if/else captures the status without that trap.
if python3 run_options.py; then
  echo "$(TZ="America/New_York" date -Iseconds)" > "$DONE_MARKER"
else
  EXIT_CODE=$?
  echo "WARN: run_options.py exited $EXIT_CODE — no done-marker written, next trigger will retry"
fi

echo ""
echo "=== Options run complete: $(TZ="America/New_York" date +%H:%M:%S) ET ==="
