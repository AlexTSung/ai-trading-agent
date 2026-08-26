#!/usr/bin/env python3
"""
journal.py — Write and maintain the structured trade journal.
Usage:
  python scripts/journal.py eod              # End-of-day journal entry
  python scripts/journal.py weekly           # Weekly review summary
  python scripts/journal.py csv-append       # Append today's trades to trade-log.csv
  python scripts/journal.py read 2026-04-18  # Print a past journal entry
"""

import os
import sys
import json
import csv
import requests
from datetime import datetime, date, timedelta

# ── Load .env ────────────────────────────────────────────────────────────────
# setup.sh writes a .env file, but nothing used to read it, so filling in keys
# there had no effect. Environment variables already set (GitHub Secrets, MCP
# config, an exporting shell) always win — load_dotenv does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars may be supplied by the shell

from version import get_version

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")

JOURNAL_DIR = os.path.join(os.path.dirname(__file__), "logs", "paper")
TRADE_LOG   = os.path.join(JOURNAL_DIR, "trade-log.csv")

def _headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_positions():
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_orders_today():
    today = date.today().isoformat()
    r = requests.get(f"{BASE_URL}/v2/orders", headers=_headers(), params={"status": "all", "after": today, "limit": 100})
    r.raise_for_status()
    return r.json()

def load_json_if_exists(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def load_if_exists(path):
    if os.path.exists(path):
        with open(path) as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def load_md_if_exists(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""

def write_eod_journal():
    today          = date.today().isoformat()
    research_path  = os.path.join(JOURNAL_DIR, f"{today}.json")
    session_path   = os.path.join(JOURNAL_DIR, f"{today}-session.json")
    judge_path     = os.path.join(JOURNAL_DIR, f"{today}-judge.json")
    judge_md_path  = os.path.join(JOURNAL_DIR, f"{today}-judge.md")
    review_path    = os.path.join(JOURNAL_DIR, f"{today}-review.json")
    review_md_path = os.path.join(JOURNAL_DIR, f"{today}-review.md")
    journal_path   = os.path.join(JOURNAL_DIR, f"{today}.md")

    research  = load_if_exists(research_path)
    session   = load_if_exists(session_path)
    judge     = load_if_exists(judge_path)
    review    = load_if_exists(review_path)
    judge_md  = load_md_if_exists(judge_md_path)
    review_md = load_md_if_exists(review_md_path)
    account   = get_account()
    positions = get_positions()
    orders    = get_orders_today()

    # ── Portfolio Status ─────────────────────────────────────────────────────
    cash           = float(account.get("cash", 0))
    portfolio_val  = float(account.get("portfolio_value", 0))
    equity         = float(account.get("equity", 0))

    positions_summary = ""
    if positions:
        for p in positions:
            pl_pct = float(p.get("unrealized_plpc", 0)) * 100
            positions_summary += f"- {p['symbol']}: {p['qty']} shares @ ${float(p['avg_entry_price']):.2f} | Current: ${float(p['current_price']):.2f} | P&L: {pl_pct:+.2f}%\n"
    else:
        positions_summary = "- No open positions\n"

    # ── Research Summary ─────────────────────────────────────────────────────
    research_section = ""
    symbols_data = research.get("symbols", {})
    for sym, data in symbols_data.items():
        if "error" in data:
            research_section += f"\n### {sym}\n- Error pulling data: {data['error']}\n"
            continue
        tech  = data.get("technicals", {})
        news  = data.get("news", [])
        research_section += f"\n### {sym} — {data.get('description', '')}\n"
        research_section += f"- 20-day MA: ${tech.get('ma20', 'N/A')} | 50-day MA: ${tech.get('ma50', 'N/A')} | Last: ${tech.get('last_close', 'N/A')}\n"
        research_section += f"- MA20 trending up: {tech.get('ma20_trending_up', 'N/A')}\n"
        research_section += f"- **Signal: {tech.get('signal', 'N/A')}**\n"
        if news:
            research_section += "- Recent news:\n"
            for n in news[:3]:
                research_section += f"  - [{n['headline']}]({n['url']}) — {n['source']} ({n['published'][:10]})\n"

    # ── Trades Table ─────────────────────────────────────────────────────────
    trades_table = "| Time | Symbol | Action | Qty | Price | Reason |\n|------|--------|--------|-----|-------|--------|\n"
    trades_exist = False

    for order in orders:
        if order.get("status") in ("filled", "partially_filled"):
            trades_exist = True
            t = order.get("filled_at", order.get("created_at", ""))[:19].replace("T", " ")
            sym   = order.get("symbol", "")
            side  = order.get("side", "").upper()
            qty   = order.get("filled_qty", order.get("qty", ""))
            price = order.get("filled_avg_price", order.get("limit_price", "N/A"))
            try:
                price_fmt = f"${float(price):.2f}"
            except:
                price_fmt = str(price)
            reason = order.get("client_order_id", "")
            trades_table += f"| {t} | {sym} | {side} | {qty} | {price_fmt} | {reason} |\n"

    if not trades_exist:
        trades_table += "| — | — | No trades executed today | — | — | — |\n"

    # ── Stop Losses ──────────────────────────────────────────────────────────
    stop_losses = session.get("stop_losses", [])
    stops_section = ""
    if stop_losses:
        stops_section = "## ⚠️ Stop-Losses Triggered\n"
        for sl in stop_losses:
            t = sl.get("trigger", {})
            stops_section += f"- **{sl['symbol']}**: closed at {float(t.get('unrealized_plpc', 0))*100:.1f}% loss from entry\n"
    else:
        stops_section = "## Stop-Losses\nNone triggered today.\n"

    # ── Judge Summary ────────────────────────────────────────────────────────
    judge_summary = ""
    if judge:
        approved = judge.get("approved", [])
        held     = judge.get("held", [])
        vetoed   = judge.get("vetoed", [])
        judge_summary = f"""## Judge Summary (Agent 2)
- **Approved** ({len(approved)}): {', '.join(approved) or 'None'}
- **Held** ({len(held)}): {', '.join(held) or 'None'}
- **Vetoed** ({len(vetoed)}): {', '.join(vetoed) or 'None'}

"""
        # Vetoed-by-judge from session
        veto_list = session.get("vetoed_by_judge", [])
        if veto_list:
            judge_summary += "### Vetoed Trades (not executed)\n"
            for v in veto_list:
                judge_summary += f"- **{v['symbol']}**: Score {v.get('judge_score', '?')}/10 — {v.get('rationale', '')}\n"
    else:
        judge_summary = "## Judge Summary\n*(Judge not run today — all trades bypassed Judge gate)*\n"

    # ── Reviewer Summary ─────────────────────────────────────────────────────
    reviewer_summary = ""
    if review and review.get("trades"):
        trades_reviewed = review["trades"]
        scores    = [t["score"] for t in trades_reviewed if t.get("score") is not None]
        avg_score = round(sum(scores) / len(scores), 2) if scores else None
        good      = [t for t in trades_reviewed if t.get("verdict") == "GOOD_TRADE"]
        poor      = [t for t in trades_reviewed if t.get("verdict") == "POOR_TRADE"]
        reviewer_summary = f"""## Reviewer Summary (Agent 4)
- **Trades reviewed**: {len(trades_reviewed)}
- **Avg post-trade score**: {avg_score}/10
- **Good trades**: {len(good)} | **Poor trades**: {len(poor)}

"""
        for t in trades_reviewed:
            pnl_str = f"{t.get('pnl_pct', 0):+.2f}%"
            reviewer_summary += f"- **{t['symbol']}**: {t.get('verdict')} | Score {t.get('score')}/10 | {pnl_str} intraday"
            if t.get("judge_score"):
                delta = round(t["score"] - t["judge_score"], 2)
                reviewer_summary += f" | Judge delta: {delta:+.2f}"
            reviewer_summary += "\n"

        # Calibration alert
        if scores:
            judge_scores_today = [t.get("judge_score") for t in trades_reviewed if t.get("judge_score")]
            if judge_scores_today:
                avg_judge = round(sum(judge_scores_today) / len(judge_scores_today), 2)
                avg_review = avg_score
                delta = round(avg_review - avg_judge, 2)
                if abs(delta) > 1.0:
                    direction = "underconfident" if delta > 0 else "overconfident"
                    reviewer_summary += f"\n⚠️ **Calibration alert**: Judge avg {avg_judge} vs Reviewer avg {avg_review} (delta {delta:+.2f}) — Judge may be {direction}\n"
    else:
        reviewer_summary = "## Reviewer Summary\n*(Reviewer not yet run — execute reviewer.py eod after 4:15 PM)*\n"

    # ── Assemble Markdown ────────────────────────────────────────────────────
    md = f"""# Trade Journal — {today}
*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}*

---

## Portfolio Status
- **Cash**: ${cash:,.2f}
- **Portfolio Value**: ${portfolio_val:,.2f}
- **Equity**: ${equity:,.2f}
- **Cash Reserve**: {(cash / portfolio_val * 100) if portfolio_val else 0:.1f}% (target: ≥20%)

### Open Positions
{positions_summary}
---

## Market Research (Agent 1)
{research_section}
---

{judge_summary}
---

## Trades Executed (Agent 3)
{trades_table}
---

{stops_section}
---

{reviewer_summary}
---

## Delta: Judge vs Actual
*(Where pre-trade scores diverged from post-trade outcomes — key learning signal)*
{_build_delta_section(review)}

---

## End-of-Day Reflection
*(Fill in: What worked? What did the Judge miss? What to tune?)*

### Decisions I'm Confident In
-

### Decisions I'm Uncertain About
-

### What To Watch Tomorrow
-

---
*Agent stack: Researcher → Judge → Executor → Reviewer → Journal*
*Next scheduled run: 9:45 AM ET tomorrow*
"""

    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(journal_path, "w") as f:
        f.write(md)

    # Append to CSV log
    append_to_csv_log(orders, today)

    return {"written": journal_path, "orders_logged": len(orders)}

def _build_delta_section(review: dict) -> str:
    if not review or not review.get("trades"):
        return "*(Run reviewer.py eod to populate this section)*\n"
    lines = []
    for t in review["trades"]:
        j = t.get("judge_score")
        r = t.get("score")
        if j is None or r is None:
            continue
        delta = round(r - j, 2)
        flag  = " ⚠️ calibration error" if abs(delta) > 2.0 else ""
        lines.append(f"- **{t['symbol']}**: Judge {j}/10 → Reviewer {r}/10 (delta {delta:+.2f}){flag}")
    return "\n".join(lines) if lines else "*(No scored trades today)*\n"

def append_to_csv_log(orders, today):
    """Append filled orders to the all-time trade log CSV."""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    write_header = not os.path.exists(TRADE_LOG)

    with open(TRADE_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "symbol", "side", "qty", "filled_price", "order_id", "status"])
        for order in orders:
            if order.get("status") in ("filled", "partially_filled"):
                writer.writerow([
                    today,
                    order.get("symbol"),
                    order.get("side"),
                    order.get("filled_qty", order.get("qty")),
                    order.get("filled_avg_price", ""),
                    order.get("id"),
                    order.get("status"),
                ])

def weekly_review():
    """Generate a weekly performance summary from the last 5 journal files."""
    today     = date.today()
    summaries = []

    for i in range(7):
        d = (today - timedelta(days=i)).isoformat()
        path = os.path.join(JOURNAL_DIR, f"{d}.md")
        if os.path.exists(path):
            with open(path) as f:
                summaries.append({"date": d, "content": f.read()[:500]})

    if not summaries:
        return {"error": "No journal files found in the last 7 days"}

    output_path = os.path.join(JOURNAL_DIR, f"weekly-review-{today.isoformat()}.md")
    with open(output_path, "w") as f:
        f.write(f"# Weekly Review — Week of {today.isoformat()}\n\n")
        for s in summaries:
            f.write(f"## {s['date']}\n{s['content']}\n\n---\n\n")

    return {"written": output_path, "days_covered": len(summaries)}

if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "eod"

    if action == "eod":
        print(json.dumps(write_eod_journal(), indent=2))
    elif action == "weekly":
        print(json.dumps(weekly_review(), indent=2))
    elif action == "csv-append":
        orders = get_orders_today()
        append_to_csv_log(orders, date.today().isoformat())
        print(json.dumps({"appended": len(orders)}))
    elif action == "read" and len(sys.argv) > 2:
        path = os.path.join(JOURNAL_DIR, f"{sys.argv[2]}.md")
        if os.path.exists(path):
            with open(path) as f:
                print(f.read())
        else:
            print(f"No journal entry for {sys.argv[2]}")
    else:
        print(json.dumps({"error": f"Unknown action: {action}"}))
