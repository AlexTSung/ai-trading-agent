#!/usr/bin/env bash
# run_agent.sh — Daily trading agent pipeline
# Reads Alpaca keys from MCP config (never hardcoded here).
# Safe to schedule via cron or cloud trigger — keys stay in MCP config only.

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"
DATE=$(date +%Y-%m-%d)
RUN_LOG="$LOG_DIR/run-$DATE.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$RUN_LOG") 2>&1

echo "=== Trading Agent Run: $DATE $(date +%H:%M:%S) ET ==="

# ── Load Alpaca keys from MCP config (no hardcoding) ──────────────────────
MCP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [ ! -f "$MCP_CONFIG" ]; then
  echo "ERROR: MCP config not found at $MCP_CONFIG"
  exit 1
fi

export APCA_API_KEY_ID=$(python3 -c "
import json
with open('$MCP_CONFIG') as f: d = json.load(f)
servers = d.get('mcpServers', {})
for v in servers.values():
    env = v.get('env', {})
    if 'ALPACA_API_KEY' in env:
        print(env['ALPACA_API_KEY'])
        break
")

export APCA_API_SECRET_KEY=$(python3 -c "
import json
with open('$MCP_CONFIG') as f: d = json.load(f)
servers = d.get('mcpServers', {})
for v in servers.values():
    env = v.get('env', {})
    if 'ALPACA_SECRET_KEY' in env:
        print(env['ALPACA_SECRET_KEY'])
        break
")

export APCA_BASE_URL="https://paper-api.alpaca.markets"

if [ -z "$APCA_API_KEY_ID" ] || [ -z "$APCA_API_SECRET_KEY" ]; then
  echo "ERROR: Could not load Alpaca keys from MCP config"
  exit 1
fi
echo "  Keys loaded from MCP config (not logged)"

# ── Check if market is open today ─────────────────────────────────────────
DAY=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$DAY" -ge 6 ]; then
  echo "  Skipping — weekend (day $DAY)"
  exit 0
fi

cd "$AGENT_DIR"

echo ""
echo "--- [1/5] Intelligence scan (8:30 AM) ---"
python3 intelligence.py morning

echo ""
echo "--- [2/5] Social screener (9:00 AM) ---"
python3 reddit_screener.py scan

echo ""
echo "--- [3/5] Research (9:45 AM) ---"
python3 research.py full

echo ""
echo "--- [4/5] Judge (10:00 AM) ---"
python3 judge.py session

echo ""
echo "--- [5/5] Executor (10:05 AM) ---"
python3 trade.py session

echo ""
echo "=== Morning pipeline complete: $(date +%H:%M:%S) ==="
