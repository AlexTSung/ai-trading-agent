#!/usr/bin/env python3
"""
forex_journal.py — Trade logging and performance reporting for the Forex system.
Tracks open/close events, pip P&L, carry earned, and Phase 3 graduation progress.

Usage:
  python forex_journal.py sync          # Sync closed trades from OANDA, update journal
  python forex_journal.py summary       # Print trade summary + Phase 3 progress
  python forex_journal.py log           # Print full trade log
  python forex_journal.py stats         # Phase 3 graduation criteria check
"""

import os
import sys
import json
from datetime import date, datetime, timezone

ROOT         = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR  = os.path.join(ROOT, "logs", "forex")
JOURNAL_FILE = os.path.join(JOURNAL_DIR, "trade_journal.json")
LESSONS_FILE = os.path.join(JOURNAL_DIR, "lessons.json")
os.makedirs(JOURNAL_DIR, exist_ok=True)

from forex_screener import (
    _check_auth, get_open_trades, get_pip_size,
    OANDA_ENV, OANDA_API_KEY, OANDA_ACCOUNT_ID, PRACTICE_URL, LIVE_URL,
)

BASE_URL = PRACTICE_URL if OANDA_ENV == "practice" else LIVE_URL

# ── Phase 3 graduation criteria ───────────────────────────────────────────────
PHASE3_MIN_TRADES      = 30
PHASE3_MIN_WIN_RATE    = 0.55
PHASE3_MIN_PROFIT_FACTOR = 1.3
PHASE3_MAX_DRAWDOWN    = 0.08
PHASE3_MIN_RR          = 1.5
PHASE3_MAX_CONSEC_LOSS = 5


def _headers():
    return {"Authorization": f"Bearer {OANDA_API_KEY}", "Content-Type": "application/json"}


def _request(method, path, body=None):
    import urllib.request, urllib.error, ssl
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method.upper())
    try:
        ctx = ssl.create_default_context()
        for cert_path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            if os.path.exists(cert_path):
                ctx.load_verify_locations(cert_path)
                break
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP Error {e.code}: {e.reason}", "detail": e.read().decode()}
    except Exception as e:
        return {"error": str(e)}


def load_journal() -> list:
    if not os.path.exists(JOURNAL_FILE):
        return []
    with open(JOURNAL_FILE) as f:
        return json.load(f)


def save_journal(trades: list):
    with open(JOURNAL_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def get_closed_transactions(count=500):
    """
    Fetch recent transactions from OANDA.
    The /transactions endpoint returns paginated page URLs — we follow each page to get the actual data.
    """
    import urllib.request, urllib.error, ssl as _ssl

    # Build SSL context once
    ctx = _ssl.create_default_context()
    for cert in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
        if os.path.exists(cert):
            ctx.load_verify_locations(cert)
            break

    def _fetch_url(url):
        hdrs = _headers()
        req = urllib.request.Request(url, headers=hdrs, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                return json.load(resp)
        except Exception as e:
            return {"error": str(e)}

    # Step 1: get page list
    meta = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/transactions?count={count}")
    if "error" in meta:
        return []

    pages = meta.get("pages", [])
    if not pages:
        return meta.get("transactions", [])

    # Step 2: fetch each page and collect transactions
    all_txs = []
    for page_url in pages:
        data = _fetch_url(page_url)
        if "error" not in data:
            all_txs.extend(data.get("transactions", []))

    return all_txs


def get_trade_detail(trade_id: str) -> dict:
    """Fetch a specific closed trade from OANDA."""
    result = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}")
    return result.get("trade", {})


def sync_closed_trades():
    """
    Pull closed trades from OANDA and merge into local journal.
    Updates existing entries with close data; adds new entries for unknown trades.
    """
    _check_auth()
    journal = load_journal()
    existing_ids = {str(t.get("trade_id")) for t in journal if t.get("status") == "closed"}
    open_ids     = {str(t.get("trade_id")) for t in journal if t.get("status") == "open"}

    # Fetch closed trades via transactions
    txs = get_closed_transactions(count=200)
    closed_txs = [t for t in txs if t.get("type") == "ORDER_FILL" and float(t.get("pl", 0)) != 0]

    new_closes = 0
    for tx in closed_txs:
        trade_id = str(tx.get("tradesClosed", [{}])[0].get("tradeID", "") if tx.get("tradesClosed") else "")
        if not trade_id or trade_id in existing_ids:
            continue

        pair = tx.get("instrument", "")
        pl   = float(tx.get("pl", 0))
        pip  = get_pip_size(pair)
        entry_price = float(tx.get("price", 0))
        units = int(tx.get("units", 0))
        direction = "LONG" if units > 0 else "SHORT"
        closed_at = tx.get("time", "")

        # Compute pip P&L
        pip_pl = pl / (abs(units) * pip) if pip and units else None

        # Find matching open entry (if any)
        matching = next((t for t in journal if str(t.get("trade_id")) == trade_id), None)

        if matching:
            matching.update({
                "status": "closed",
                "realized_pl": round(pl, 2),
                "pip_pl": round(pip_pl, 1) if pip_pl else None,
                "close_price": entry_price,
                "close_reason": _infer_close_reason(tx, matching),
                "closed_at": closed_at,
                # preserve fields needed for optimizer learning
                "direction": matching.get("direction", direction),
                "confluence": matching.get("confluence"),
                "factors": matching.get("factors"),
                "score": matching.get("score"),
            })
        else:
            journal.append({
                "status": "closed",
                "trade_id": trade_id,
                "pair": pair,
                "direction": direction,
                "units": abs(units),
                "realized_pl": round(pl, 2),
                "pip_pl": round(pip_pl, 1) if pip_pl else None,
                "close_price": entry_price,
                "close_reason": _infer_close_reason(tx, {}),
                "closed_at": closed_at,
                "environment": OANDA_ENV,
                "source": "oanda_sync",
            })

        existing_ids.add(trade_id)
        new_closes += 1

    # Update open positions with current unrealized P&L
    _ot_result = get_open_trades()
    open_trades = _ot_result if isinstance(_ot_result, list) else _ot_result.get("trades", [])
    for ot in open_trades:
        trade_id = str(ot.get("id", ""))
        match = next((t for t in journal if str(t.get("trade_id")) == trade_id), None)
        if match:
            match["unrealized_pl"] = float(ot.get("unrealizedPL", 0))
            match["current_price"]  = float(ot.get("price", 0))

    save_journal(journal)
    print(f"Synced: {new_closes} new closes. Total journal entries: {len(journal)}")
    return journal


def _infer_close_reason(tx: dict, original: dict) -> str:
    reason = tx.get("reason", "")
    if "STOP" in reason.upper():
        return "stop_loss"
    if "TAKE_PROFIT" in reason.upper() or "TARGET" in reason.upper():
        return "take_profit"
    pl = float(tx.get("pl", 0))
    if original.get("stop_price"):
        stop = original["stop_price"]
        close = float(tx.get("price", 0))
        if original.get("direction") == "LONG" and abs(close - stop) / stop < 0.002:
            return "stop_loss"
        if original.get("direction") == "SHORT" and abs(close - stop) / stop < 0.002:
            return "stop_loss"
    return "manual" if pl > 0 else "stop_loss"


# ── Performance stats ──────────────────────────────────────────────────────────
def compute_stats(journal: list) -> dict:
    closed = [t for t in journal if t.get("status") == "closed" and t.get("realized_pl") is not None]
    open_  = [t for t in journal if t.get("status") == "open"]

    if not closed:
        return {
            "closed_trades": 0,
            "open_trades": len(open_),
            "wins": 0, "losses": 0, "win_rate": 0,
            "profit_factor": 0, "total_pl": 0,
            "avg_win": 0, "avg_loss": 0, "avg_rr": 0,
            "max_drawdown_pct": 0,
            "max_consecutive_losses": 0,
            "by_pair": {}, "by_strategy": {},
            "phase3_progress": _phase3_progress(0, 0, 0, 0, 0, 0),
        }

    wins   = [t for t in closed if t.get("realized_pl", 0) > 0]
    losses = [t for t in closed if t.get("realized_pl", 0) <= 0]

    win_rate     = len(wins) / len(closed)
    total_profit = sum(t["realized_pl"] for t in wins)
    total_loss   = abs(sum(t["realized_pl"] for t in losses))
    profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")
    total_pl     = sum(t.get("realized_pl", 0) for t in closed)
    avg_win      = total_profit / len(wins) if wins else 0
    avg_loss     = total_loss / len(losses) if losses else 0
    avg_rr       = avg_win / avg_loss if avg_loss > 0 else 0

    # Max drawdown (sequential P&L)
    equity_curve = []
    running = 0
    for t in sorted(closed, key=lambda x: x.get("closed_at", "")):
        running += t.get("realized_pl", 0)
        equity_curve.append(running)
    peak = 0
    max_dd = 0
    for val in equity_curve:
        peak = max(peak, val)
        dd = (peak - val) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    # Consecutive losses
    max_consec = 0
    cur_consec = 0
    for t in sorted(closed, key=lambda x: x.get("closed_at", "")):
        if t.get("realized_pl", 0) <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    # By pair
    by_pair = {}
    for t in closed:
        p = t.get("pair", "?")
        if p not in by_pair:
            by_pair[p] = {"wins": 0, "losses": 0, "pl": 0}
        if t["realized_pl"] > 0:
            by_pair[p]["wins"] += 1
        else:
            by_pair[p]["losses"] += 1
        by_pair[p]["pl"] = round(by_pair[p]["pl"] + t["realized_pl"], 2)

    # By strategy tag
    by_strategy = {}
    for t in closed:
        tag = t.get("strategy_tag", "untagged")
        if tag not in by_strategy:
            by_strategy[tag] = {"count": 0, "pl": 0}
        by_strategy[tag]["count"] += 1
        by_strategy[tag]["pl"] = round(by_strategy[tag]["pl"] + t["realized_pl"], 2)

    return {
        "closed_trades": len(closed),
        "open_trades": len(open_),
        "win_rate": round(win_rate, 3),
        "wins": len(wins),
        "losses": len(losses),
        "total_pl": round(total_pl, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_rr": round(avg_rr, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_consecutive_losses": max_consec,
        "by_pair": by_pair,
        "by_strategy": by_strategy,
        "phase3_progress": _phase3_progress(
            len(closed), win_rate, profit_factor, total_pl, max_dd, max_consec
        ),
    }


def _phase3_progress(n, win_rate, pf, pl, max_dd, max_consec) -> dict:
    checks = {
        "closed_trades":        {"value": n,              "target": PHASE3_MIN_TRADES,         "met": n >= PHASE3_MIN_TRADES},
        "win_rate":             {"value": round(win_rate, 3), "target": PHASE3_MIN_WIN_RATE,  "met": win_rate >= PHASE3_MIN_WIN_RATE},
        "profit_factor":        {"value": round(pf, 2),   "target": PHASE3_MIN_PROFIT_FACTOR, "met": pf >= PHASE3_MIN_PROFIT_FACTOR},
        "positive_pl":          {"value": round(pl, 2),   "target": "> 0",                    "met": pl > 0},
        "max_drawdown":         {"value": f"{round(max_dd*100,1)}%", "target": f"≤ {PHASE3_MAX_DRAWDOWN*100}%", "met": max_dd <= PHASE3_MAX_DRAWDOWN},
        "consec_losses":        {"value": max_consec,     "target": f"≤ {PHASE3_MAX_CONSEC_LOSS}", "met": max_consec <= PHASE3_MAX_CONSEC_LOSS},
    }
    all_met = all(c["met"] for c in checks.values())
    met_count = sum(1 for c in checks.values() if c["met"])
    return {"criteria": checks, "all_met": all_met, "met": met_count, "total": len(checks)}


def print_summary(journal=None):
    if journal is None:
        journal = load_journal()
    stats = compute_stats(journal)
    p3    = stats["phase3_progress"]
    today = date.today().isoformat()

    print(f"\n{'='*65}")
    print(f"  FOREX JOURNAL SUMMARY  —  {today}  ({OANDA_ENV.upper()})")
    print(f"{'='*65}")
    print(f"  Closed Trades:   {stats['closed_trades']:>4}   |   Open: {stats['open_trades']}")
    if stats["closed_trades"] > 0:
        print(f"  Win Rate:        {stats['win_rate']*100:.1f}%  ({stats['wins']}W / {stats['losses']}L)")
        print(f"  Total P&L:       ${stats['total_pl']:+.2f}")
        print(f"  Profit Factor:   {stats['profit_factor']:.2f}")
        print(f"  Avg Win/Loss:    ${stats['avg_win']:.2f} / ${stats['avg_loss']:.2f}  (R:R {stats['avg_rr']:.2f})")
        print(f"  Max Drawdown:    {stats['max_drawdown_pct']:.1f}%")
        print(f"  Max Consec Loss: {stats['max_consecutive_losses']}")

    print(f"\n  --- Phase 3 Graduation Progress ({p3['met']}/{p3['total']} criteria met) ---")
    for name, c in p3["criteria"].items():
        icon = "✓" if c["met"] else "✗"
        print(f"  {icon}  {name:<25}  {str(c['value']):>10}  (target: {c['target']})")

    if p3["all_met"]:
        print(f"\n  🎉 ALL PHASE 3 CRITERIA MET — Ready for Phase 4 soft launch!")
    else:
        remaining = p3["total"] - p3["met"]
        print(f"\n  {remaining} criteria remaining before Phase 4 unlock.")

    if stats.get("by_pair"):
        print(f"\n  --- P&L by Pair ---")
        for pair, d in sorted(stats["by_pair"].items(), key=lambda x: x[1]["pl"], reverse=True):
            wr = d["wins"] / (d["wins"] + d["losses"]) * 100 if (d["wins"] + d["losses"]) > 0 else 0
            print(f"  {pair:12s}  {d['wins']}W/{d['losses']}L  WR={wr:.0f}%  P&L=${d['pl']:+.2f}")
    print()


def print_log(limit=50):
    journal = load_journal()
    closed = sorted(
        [t for t in journal if t.get("status") == "closed"],
        key=lambda x: x.get("closed_at", ""),
        reverse=True
    )[:limit]

    print(f"\n{'='*80}")
    print(f"  TRADE LOG  —  Last {min(limit, len(closed))} closed trades  ({OANDA_ENV.upper()})")
    print(f"{'='*80}")
    print(f"  {'#':>3}  {'Pair':12s}  {'Dir':6s}  {'P&L':>8}  {'Pips':>6}  {'Reason':15s}  {'Closed':16s}")
    print(f"  {'-'*3}  {'-'*12}  {'-'*6}  {'-'*8}  {'-'*6}  {'-'*15}  {'-'*16}")
    for i, t in enumerate(reversed(closed), 1):
        pl     = t.get("realized_pl", 0)
        pips   = t.get("pip_pl", "–")
        pair   = t.get("pair", "–")
        dir_   = t.get("direction", "–")
        reason = t.get("close_reason", "–")
        closed_at = (t.get("closed_at") or "")[:16]
        sign = "+" if pl > 0 else ""
        print(f"  {i:>3}  {pair:12s}  {dir_:6s}  ${sign}{pl:>7.2f}  {str(pips):>6}  {reason:15s}  {closed_at}")
    print()


# ── Lessons & daily reflection ────────────────────────────────────────────────

def load_lessons() -> list:
    if not os.path.exists(LESSONS_FILE):
        return []
    with open(LESSONS_FILE) as f:
        return json.load(f)


def save_lessons(lessons: list):
    with open(LESSONS_FILE, "w") as f:
        json.dump(lessons, f, indent=2)


def log_lesson(lesson: str, category: str = "general"):
    """
    Append a lesson learned to the lessons log.
    Categories: strategy, risk, psychology, pattern, market
    """
    lessons = load_lessons()
    lessons.append({
        "date": date.today().isoformat(),
        "category": category,
        "lesson": lesson,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    })
    save_lessons(lessons)
    print(f"  Lesson logged [{category}]: {lesson}")


def auto_log_lessons_from_journal():
    """
    Auto-detect lessons from recent closed trades and log them.
    Runs after each sync. Avoids duplicating lessons already logged today.
    """
    journal = load_journal()
    lessons = load_lessons()
    today   = date.today().isoformat()

    # Don't re-log lessons we already added today
    already_today = {l["lesson"] for l in lessons if l.get("date") == today}
    new_lessons = []

    closed_today = [
        t for t in journal
        if t.get("status") == "closed"
        and (t.get("closed_at") or "").startswith(today)
    ]

    wins_today   = [t for t in closed_today if t.get("realized_pl", 0) > 0]
    losses_today = [t for t in closed_today if t.get("realized_pl", 0) < 0]

    # Lesson: stop-loss hit — did we follow strategy or fight it?
    for t in losses_today:
        if t.get("close_reason") == "stop_loss":
            lesson = f"Stop-loss respected on {t.get('pair')} — loss of ${t.get('realized_pl',0):.2f} accepted. Strategy followed."
            if lesson not in already_today:
                new_lessons.append({"category": "risk", "lesson": lesson})

    # Lesson: TP hit — good execution
    for t in wins_today:
        if t.get("close_reason") == "take_profit":
            lesson = f"TP1 hit on {t.get('pair')} — ${t.get('realized_pl',0):.2f} realized. Let the plan work."
            if lesson not in already_today:
                new_lessons.append({"category": "strategy", "lesson": lesson})

    # Lesson: daily outcome limit triggered
    wins_count   = len(wins_today)
    losses_count = len(losses_today)
    if wins_count >= 2:
        lesson = f"Daily win limit reached ({wins_count}W today) — stopped trading to protect profits."
        if lesson not in already_today:
            new_lessons.append({"category": "psychology", "lesson": lesson})
    if losses_count >= 2:
        lesson = f"Daily loss limit reached ({losses_count}L today) — stopped trading to prevent revenge trading."
        if lesson not in already_today:
            new_lessons.append({"category": "psychology", "lesson": lesson})

    # Screen time accumulation note
    all_closed = [t for t in journal if t.get("status") == "closed"]
    milestones = [10, 20, 30, 50, 100]
    for m in milestones:
        if len(all_closed) == m:
            lesson = f"Milestone: {m} closed trades completed. Experience accumulates — each trade builds pattern recognition."
            if lesson not in already_today:
                new_lessons.append({"category": "growth", "lesson": lesson})

    # Append new lessons
    for nl in new_lessons:
        lessons.append({
            "date": today,
            "category": nl["category"],
            "lesson": nl["lesson"],
            "logged_at": datetime.now(timezone.utc).isoformat(),
        })

    if new_lessons:
        save_lessons(lessons)
        print(f"  Auto-logged {len(new_lessons)} lesson(s) from today's trades")

    return new_lessons


def print_lessons(limit=20):
    lessons = load_lessons()
    if not lessons:
        print("No lessons logged yet.")
        return
    recent = sorted(lessons, key=lambda x: x.get("logged_at", ""), reverse=True)[:limit]
    print(f"\n{'='*70}")
    print(f"  TRADING LESSONS LOG  —  Last {len(recent)} entries")
    print(f"{'='*70}")
    for l in reversed(recent):
        print(f"  [{l['date']}] [{l['category'].upper():10s}] {l['lesson']}")
    print()


def write_daily_reflection():
    """
    Write a structured daily reflection to logs/forex/YYYY-MM-DD_reflection.md.
    Called at end of each run. Answers the key questions every trader should review.
    """
    today   = date.today().isoformat()
    journal = load_journal()
    lessons = load_lessons()
    stats   = compute_stats(journal)

    closed_today = [
        t for t in journal
        if t.get("status") == "closed"
        and (t.get("closed_at") or "").startswith(today)
    ]
    todays_lessons = [l for l in lessons if l.get("date") == today]

    reflection_path = os.path.join(JOURNAL_DIR, f"{today}_reflection.md")

    lines = [
        f"# Daily Trading Reflection — {today}",
        f"",
        f"## P&L",
        f"- Realized today: ${sum(t.get('realized_pl',0) for t in closed_today):+.2f}",
        f"- Trades closed: {len(closed_today)}",
        f"- Wins: {sum(1 for t in closed_today if t.get('realized_pl',0)>0)}  Losses: {sum(1 for t in closed_today if t.get('realized_pl',0)<0)}",
        f"",
        f"## Did I follow the strategy exactly?",
        f"- [ ] Only took trades with judge score ≥ 7.0",
        f"- [ ] Every trade had a stop-loss set before entry",
        f"- [ ] Did not move stop-loss to avoid being stopped out",
        f"- [ ] Did not overtrade after wins or losses",
        f"- [ ] Respected the 2-outcome daily limit",
        f"- [ ] Traded in the direction of the higher timeframe trend",
        f"",
        f"## What happened vs. what I expected?",
        f"_(Review each trade: was the outcome different from what you anticipated? Why?)_",
        f"",
    ]

    for t in closed_today:
        pl_sign = "+" if t.get("realized_pl", 0) > 0 else ""
        lines.append(f"- **{t.get('pair')}** {t.get('direction')} → {t.get('close_reason')} → ${pl_sign}{t.get('realized_pl',0):.2f}")
        lines.append(f"  - Expected: ___  Actual: ___  Lesson: ___")
        lines.append(f"")

    lines += [
        f"## Lessons logged today",
    ]
    for l in todays_lessons:
        lines.append(f"- [{l['category']}] {l['lesson']}")

    lines += [
        f"",
        f"## One improvement for tomorrow",
        f"_(Even a tiny improvement compounds over time — what ONE thing will you do better?)_",
        f"",
        f"> ___",
        f"",
        f"## Overall performance — {stats['closed_trades']} trades lifetime",
        f"- Win rate: {stats['win_rate']*100:.1f}%  (target ≥55%)",
        f"- Profit factor: {stats['profit_factor']:.2f}  (target ≥1.3)",
        f"- Max drawdown: {stats['max_drawdown_pct']:.1f}%  (max 8%)",
        f"",
        f"---",
        f"_Remember: Individual trade results don't matter. Focus on process, not P&L._",
        f"_Every trade is just a probability. Do everything right and you'll win over time._",
    ]

    with open(reflection_path, "w") as f:
        f.write("\n".join(lines))

    print(f"  Daily reflection written: logs/forex/{today}_reflection.md")
    return reflection_path


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "summary"

    if cmd == "sync":
        sync_closed_trades()
        auto_log_lessons_from_journal()
        write_daily_reflection()
        print_summary()

    elif cmd == "summary":
        print_summary()

    elif cmd == "log":
        print_log(limit=100)

    elif cmd == "stats":
        journal = load_journal()
        stats   = compute_stats(journal)
        print(json.dumps(stats, indent=2))

    elif cmd == "lessons":
        print_lessons(limit=50)

    elif cmd == "reflect":
        write_daily_reflection()
        print_lessons(limit=10)

    elif cmd == "lesson":
        # python3 forex_journal.py lesson "your lesson here" psychology
        text     = sys.argv[2] if len(sys.argv) > 2 else ""
        category = sys.argv[3] if len(sys.argv) > 3 else "general"
        if text:
            log_lesson(text, category)
        else:
            print("Usage: forex_journal.py lesson 'your lesson text' [category]")

    else:
        print(f"Unknown command. Usage: forex_journal.py [sync | summary | log | stats | lessons | reflect | lesson]")
        sys.exit(1)
