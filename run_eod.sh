#!/usr/bin/env bash
# run_eod.sh — End-of-day reviewer + journal pipeline (4:15 PM ET)

set -e
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$AGENT_DIR/logs/paper"
DATE=$(date +%Y-%m-%d)

mkdir -p "$LOG_DIR"
exec >> "$LOG_DIR/run-$DATE.log" 2>&1

echo ""
echo "=== EOD Run: $DATE $(date +%H:%M:%S) ET ==="

MCP_CONFIG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
export APCA_API_KEY_ID=$(python3 -c "
import json
with open('$MCP_CONFIG') as f: d = json.load(f)
for v in d.get('mcpServers', {}).values():
    env = v.get('env', {})
    if 'ALPACA_API_KEY' in env:
        print(env['ALPACA_API_KEY']); break
")
export APCA_API_SECRET_KEY=$(python3 -c "
import json
with open('$MCP_CONFIG') as f: d = json.load(f)
for v in d.get('mcpServers', {}).values():
    env = v.get('env', {})
    if 'ALPACA_SECRET_KEY' in env:
        print(env['ALPACA_SECRET_KEY']); break
")
export APCA_BASE_URL="https://paper-api.alpaca.markets"

DAY=$(date +%u)
[ "$DAY" -ge 6 ] && echo "  Weekend — skipping" && exit 0

cd "$AGENT_DIR"

echo "--- [1/2] Reviewer ---"
python3 reviewer.py eod

echo "--- [2/2] Journal ---"
python3 journal.py eod

echo "=== EOD complete: $(date +%H:%M:%S) ==="
