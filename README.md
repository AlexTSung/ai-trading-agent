# 🤖 Claude Trading Agent — Beta (Paper Money)

An autonomous trading agent built on Claude Code + Alpaca paper trading. It wakes on a schedule, researches the market, makes reasoned trade decisions, and journals everything.

> **Status: Paper money only.** Real-money Robinhood integration is a future phase.

---

## How It Works

```
9:45 AM ET  →  research.py full    →  Pull bars + news, compute MA signals
10:00 AM ET →  trade.py session    →  Evaluate signals, place limit orders
12:00 PM ET →  trade.py check-stops →  Emergency stop-loss scan
4:15 PM ET  →  journal.py eod      →  Write structured journal entry
Friday EOD  →  journal.py weekly   →  Weekly review + CLAUDE.md update
```

All three jobs — research, trade, journal — run headlessly via Claude Code Routines.

---

## Setup

```bash
# 1. Clone / download this project
# 2. Run setup
chmod +x setup.sh && ./setup.sh

# 3. Add your Alpaca paper keys to .env
# 4. Test connection
python scripts/research.py account
```

Get paper trading API keys at [app.alpaca.markets](https://app.alpaca.markets) — toggle to **Paper Trading** before creating keys.

---

## Project Structure

```
trading-agent/
├── CLAUDE.md                    ← Agent brain: rules, framework, lessons
├── .env                         ← API keys (never commit this)
├── setup.sh                     ← One-time setup
│
├── scripts/
│   ├── research.py              ← Market data, news, technical signals
│   ├── trade.py                 ← Order execution with guardrails
│   └── journal.py               ← Journal writer + CSV log
│
├── config/
│   ├── watchlist.json           ← Symbols to track + allocation limits
│   └── journal-template.md     ← Markdown format reference
│
├── .claude/
│   └── routines.json            ← Claude Code schedule config
│
└── journal/
    ├── 2026-04-18.md            ← Daily journal entries
    ├── 2026-04-18.json          ← Raw research data
    ├── 2026-04-18-session.json  ← Trade session data
    ├── weekly-review-*.md       ← Weekly summaries
    └── trade-log.csv            ← All-time filled order log
```

---

## Hard Rules (from CLAUDE.md)

| Rule | Value |
|------|-------|
| Max position size | 5% of portfolio |
| Cash reserve | Always ≥ 20% |
| Order type | Limit only (within 0.2% of ask) |
| Stop-loss | Close at -8% from entry |
| Market orders | **Never** |
| Journal | Every market day, no exceptions |

---

## Signals

| Signal | Condition |
|--------|-----------|
| STRONG_BUY | MA20 > MA50 AND price > MA20 AND news catalyst |
| WEAK_BUY | MA20 > MA50 AND price between MAs |
| HOLD | Mixed or neutral signals |
| WEAK_SELL | Price < MA20 AND MA20 declining |
| STRONG_SELL | Price < MA50 OR stop-loss at -8% |

---

## Manual Commands

```bash
# Research
python scripts/research.py full          # Full watchlist research
python scripts/research.py signal NVDA   # Signal for one symbol
python scripts/research.py news AAPL     # News headlines
python scripts/research.py account       # Portfolio snapshot

# Trading
python scripts/trade.py status           # Is market open?
python scripts/trade.py session          # Run full trading session
python scripts/trade.py check-stops      # Check stop-losses now
python scripts/trade.py close NVDA       # Close a specific position
python scripts/trade.py cancel           # Cancel all open orders

# Journal
python scripts/journal.py eod            # Write end-of-day entry
python scripts/journal.py weekly         # Weekly review
python scripts/journal.py read 2026-04-18  # Read a past entry
```

---

## Roadmap

- [x] Beta: Paper trading via Alpaca
- [x] Research skill (bars, news, MAs, signals)
- [x] Trade execution with guardrails
- [x] Structured journal (markdown + CSV)
- [x] Claude Code Routines schedule
- [ ] Add RSI + MACD to signal stack
- [ ] Earnings calendar integration (avoid pre-earnings entries)
- [ ] Slack/email notification on stop-loss events
- [ ] Portfolio performance dashboard
- [ ] Phase 2: Robinhood MCP integration (real money)

---

## Robinhood Migration Path

When ready to go live:
1. Install Robinhood MCP server
2. Replace `trade.py`'s order functions with Robinhood MCP calls
3. Update `APCA_BASE_URL` and auth env vars
4. Paper trade for 30+ days first and review `trade-log.csv` before going live
5. Start with 10% of intended capital. Scale up as the journal shows consistent discipline.
