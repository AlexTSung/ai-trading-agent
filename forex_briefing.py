#!/usr/bin/env python3
"""
forex_briefing.py — Daily performance dashboard for the Forex trading agent.
Run this any time to see what the agent has learned, P&L, and Phase 3 readiness.
After 30+ trades, prints a full equity curve chart.

Usage:
  python3 forex_briefing.py          # Full daily briefing
  python3 forex_briefing.py chart    # Equity curve chart only
  python3 forex_briefing.py phase3   # Phase 3 readiness scorecard only
"""

import os
import sys
import json
import math
from datetime import date, datetime, timezone, timedelta

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "forex")
CONFIG_FILE = os.path.join(ROOT, "forex_config.json")
LEARNINGS   = os.path.join(ROOT, "strategies", "forex_learnings.md")


def load_journal():
    path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE) as f:
        return json.load(f)


def load_todays_verdicts():
    today = date.today().isoformat()
    path  = os.path.join(JOURNAL_DIR, f"{today}_verdicts.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def load_todays_scan():
    today = date.today().isoformat()
    path  = os.path.join(JOURNAL_DIR, f"{today}_scan.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


# ── Compute all stats ──────────────────────────────────────────────────────────
def compute_full_stats(journal):
    closed = [t for t in journal if t.get("status") == "closed" and t.get("realized_pl") is not None]
    open_  = [t for t in journal if t.get("status") == "open"]

    unrealized_pl = sum(float(t.get("unrealized_pl", 0)) for t in open_)

    if not closed:
        return {
            "closed_trades": 0, "open_trades": len(open_),
            "unrealized_pl": round(unrealized_pl, 2),
            "wins": 0, "losses": 0, "win_rate": 0,
            "total_realized_pl": 0, "profit_factor": 0,
            "avg_win": 0, "avg_loss": 0, "avg_rr": 0,
            "max_drawdown_pct": 0, "max_consec_losses": 0,
            "equity_curve": [], "daily_pl": {},
            "by_pair": {}, "by_reason": {},
            "streak": 0, "streak_type": None,
        }

    wins   = [t for t in closed if t["realized_pl"] > 0]
    losses = [t for t in closed if t["realized_pl"] <= 0]
    win_rate = len(wins) / len(closed)
    total_profit = sum(t["realized_pl"] for t in wins)
    total_loss   = abs(sum(t["realized_pl"] for t in losses))
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else 999
    total_pl = sum(t["realized_pl"] for t in closed)
    avg_win  = total_profit / len(wins) if wins else 0
    avg_loss = total_loss / len(losses) if losses else 0
    avg_rr   = avg_win / avg_loss if avg_loss > 0 else 0

    # Equity curve (cumulative P&L over time)
    sorted_closed = sorted(closed, key=lambda x: x.get("closed_at", ""))
    equity_curve = []
    running = 0.0
    for t in sorted_closed:
        running += t["realized_pl"]
        equity_curve.append({
            "date": (t.get("closed_at") or "")[:10],
            "pl": round(t["realized_pl"], 2),
            "cumulative": round(running, 2),
            "pair": t.get("pair", "?"),
            "won": t["realized_pl"] > 0,
        })

    # Daily P&L
    daily_pl = {}
    for t in sorted_closed:
        day = (t.get("closed_at") or "")[:10]
        if day:
            daily_pl[day] = round(daily_pl.get(day, 0) + t["realized_pl"], 2)

    # Max drawdown
    peak = 0
    max_dd = 0
    for pt in equity_curve:
        peak = max(peak, pt["cumulative"])
        if peak > 0:
            dd = (peak - pt["cumulative"]) / peak
            max_dd = max(max_dd, dd)

    # Max consecutive losses
    max_consec = cur_consec = 0
    for t in sorted_closed:
        if t["realized_pl"] <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0

    # Current streak
    streak = 0
    streak_type = None
    for t in reversed(sorted_closed):
        if streak == 0:
            streak_type = "WIN" if t["realized_pl"] > 0 else "LOSS"
            streak = 1
        elif (t["realized_pl"] > 0) == (streak_type == "WIN"):
            streak += 1
        else:
            break

    # By pair
    by_pair = {}
    for t in closed:
        p = t.get("pair", "?")
        if p not in by_pair:
            by_pair[p] = {"wins": 0, "losses": 0, "pl": 0.0, "trades": 0}
        by_pair[p]["trades"] += 1
        by_pair[p]["pl"] = round(by_pair[p]["pl"] + t["realized_pl"], 2)
        if t["realized_pl"] > 0:
            by_pair[p]["wins"] += 1
        else:
            by_pair[p]["losses"] += 1

    # By close reason
    by_reason = {}
    for t in closed:
        r = t.get("close_reason", "unknown")
        if r not in by_reason:
            by_reason[r] = {"count": 0, "pl": 0.0}
        by_reason[r]["count"] += 1
        by_reason[r]["pl"] = round(by_reason[r]["pl"] + t["realized_pl"], 2)

    return {
        "closed_trades": len(closed),
        "open_trades": len(open_),
        "unrealized_pl": round(unrealized_pl, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 3),
        "total_realized_pl": round(total_pl, 2),
        "profit_factor": profit_factor,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "avg_rr": round(avg_rr, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_consec_losses": max_consec,
        "equity_curve": equity_curve,
        "daily_pl": daily_pl,
        "by_pair": by_pair,
        "by_reason": by_reason,
        "streak": streak,
        "streak_type": streak_type,
    }


# ── ASCII equity curve chart ───────────────────────────────────────────────────
def render_equity_chart(equity_curve, width=60, height=14):
    if len(equity_curve) < 2:
        return "  [Chart available after 2+ closed trades]"

    values = [pt["cumulative"] for pt in equity_curve]
    dates  = [pt["date"] for pt in equity_curve]
    min_v  = min(values)
    max_v  = max(values)
    span   = max_v - min_v if max_v != min_v else 1

    # Scale values to chart height
    def scale(v):
        return int((v - min_v) / span * (height - 1))

    scaled = [scale(v) for v in values]

    # Build grid
    grid = [[" " for _ in range(width)] for _ in range(height)]

    # Plot zero line
    zero_row = scale(0) if min_v <= 0 <= max_v else None
    if zero_row is not None:
        for x in range(width):
            grid[height - 1 - zero_row][x] = "─"

    # Plot equity line
    step = max(1, len(scaled) // width)
    prev_y = None
    for i, y in enumerate(scaled):
        x = min(width - 1, int(i * width / len(scaled)))
        row = height - 1 - y
        col = x
        if 0 <= row < height and 0 <= col < width:
            won = equity_curve[i]["won"]
            grid[row][col] = "●" if won else "○"
        if prev_y is not None:
            lo, hi = (min(prev_y, y), max(prev_y, y))
            for fill_y in range(lo + 1, hi):
                fill_row = height - 1 - fill_y
                if 0 <= fill_row < height:
                    grid[fill_row][x] = "│"
        prev_y = y

    # Y-axis labels
    lines = []
    for r in range(height):
        val = min_v + (height - 1 - r) * span / (height - 1)
        label = f"${val:+.0f}".rjust(7)
        row_str = "".join(grid[r])
        lines.append(f"  {label} │{row_str}")

    # X-axis
    lines.append(f"  {'':7s} └{'─'*width}")
    if dates:
        first = dates[0][:7]
        last  = dates[-1][:7]
        lines.append(f"  {'':8s}{first}{' '*(width-14)}{last}")

    return "\n".join(lines)


# ── Phase 3 readiness bar ──────────────────────────────────────────────────────
def phase3_bar(value, target, reverse=False, width=20):
    if reverse:
        pct = max(0, min(1, 1 - value / target)) if target > 0 else 1
    else:
        pct = max(0, min(1, value / target)) if target != 0 else 0
    filled = int(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {int(pct*100)}%"


def phase3_scorecard(stats):
    PHASE3 = {
        "closed_trades":   (stats["closed_trades"],       30,    False),
        "win_rate":        (stats["win_rate"],             0.55,  False),
        "profit_factor":   (stats["profit_factor"],        1.3,   False),
        "positive_pl":     (stats["total_realized_pl"],    0.01,  False),
        "max_drawdown":    (stats["max_drawdown_pct"],     8.0,   True),
        "consec_losses":   (stats["max_consec_losses"],    5,     True),
    }
    lines = []
    all_met = True
    met_count = 0
    for name, (val, target, reverse) in PHASE3.items():
        if reverse:
            met = val <= target
        else:
            met = val >= target
        if met:
            met_count += 1
        else:
            all_met = False
        icon = "✅" if met else "❌"
        if name == "win_rate":
            display_val = f"{val*100:.1f}%"
            display_tgt = f"≥{target*100:.0f}%"
        elif name == "max_drawdown":
            display_val = f"{val:.1f}%"
            display_tgt = f"≤{target:.0f}%"
        elif name == "positive_pl":
            display_val = f"${val:+.2f}"
            display_tgt = "> $0"
        else:
            display_val = str(val)
            display_tgt = f"{'≤' if reverse else '≥'}{target}"
        bar = phase3_bar(val, target, reverse)
        lines.append(f"  {icon}  {name:<20} {display_val:>10}  {display_tgt:<8}  {bar}")

    return lines, met_count, all_met


# ── What the agent learned today ──────────────────────────────────────────────
def todays_learning_summary(config):
    lines = []
    weights = config.get("weights", {})
    if weights:
        top = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        lines.append(f"  Top weighted factors: {top[0][0]} ({top[0][1]:.1f}), {top[1][0]} ({top[1][1]:.1f}), {top[2][0]} ({top[2][1]:.1f})")
    trades_analyzed = config.get("trades_analyzed", 0)
    if trades_analyzed >= 10:
        lines.append(f"  Optimizer has tuned weights from {trades_analyzed} trades")
    else:
        lines.append(f"  Using default weights ({trades_analyzed} trades analyzed so far — need 10 to tune)")
    learnings = config.get("learnings_count", 0)
    if learnings > 0:
        lines.append(f"  Weekly learner has run {learnings} session(s) — see strategies/forex_learnings.md")
    stop_mult = config.get("stop_atr_mult", 1.5)
    lines.append(f"  Current stop: {stop_mult}× ATR  |  TP1: {config.get('tp1_rr', 2.0)}×R  |  Risk: {config.get('max_risk_pct', 0.02)*100:.0f}%/trade")
    return lines


# ── Main briefing ──────────────────────────────────────────────────────────────
def load_lessons():
    path = os.path.join(JOURNAL_DIR, "lessons.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def print_daily_briefing():
    today    = date.today().isoformat()
    journal  = load_journal()
    config   = load_config()
    stats    = compute_full_stats(journal)
    verdicts = load_todays_verdicts()
    scan     = load_todays_scan()
    lessons  = load_lessons()

    W = 65

    print(f"\n{'═'*W}")
    print(f"  📊 FOREX AGENT DAILY BRIEFING  —  {today}")
    print(f"{'═'*W}")

    # ── Account snapshot ───────────────────────────────────────────────────
    realized   = stats["total_realized_pl"]
    unrealized = stats["unrealized_pl"]
    total      = realized + unrealized
    sign_r = "+" if realized >= 0 else ""
    sign_u = "+" if unrealized >= 0 else ""
    sign_t = "+" if total >= 0 else ""

    print(f"\n  💰 P&L SNAPSHOT")
    print(f"  {'Realized P&L:':<22} ${sign_r}{realized:.2f}")
    print(f"  {'Unrealized P&L:':<22} ${sign_u}{unrealized:.2f}  ({stats['open_trades']} open positions)")
    print(f"  {'Total P&L:':<22} ${sign_t}{total:.2f}")

    # ── Today's activity ───────────────────────────────────────────────────
    if verdicts:
        tradeable = [v for v in verdicts.get("verdicts", []) if v.get("verdict") not in ("SKIP", "DISQUALIFIED")]
        print(f"\n  📡 TODAY'S SIGNALS  ({verdicts.get('total_reviewed',0)} scanned, {len(tradeable)} tradeable)")
        for v in sorted(verdicts.get("verdicts", []), key=lambda x: x.get("score",0), reverse=True)[:6]:
            icon = "🟢" if v.get("verdict") not in ("SKIP","DISQUALIFIED") else "⚪"
            print(f"  {icon} {v['pair']:12s} {v.get('direction','?'):6s}  score={v.get('score',0):.2f}  {v.get('verdict','?')}")
    else:
        print(f"\n  📡 TODAY'S SIGNALS  (no scan data yet — run: python3 run_forex.py --judge-only)")

    # ── Trade performance ──────────────────────────────────────────────────
    print(f"\n  📈 TRADE PERFORMANCE  ({stats['closed_trades']} closed trades)")
    if stats["closed_trades"] > 0:
        wr_bar = "█" * int(stats["win_rate"] * 20) + "░" * (20 - int(stats["win_rate"] * 20))
        print(f"  {'Win Rate:':<22} {stats['win_rate']*100:.1f}%  [{wr_bar}]")
        print(f"  {'Wins / Losses:':<22} {stats['wins']}W  /  {stats['losses']}L")
        print(f"  {'Profit Factor:':<22} {stats['profit_factor']:.2f}  (target ≥1.3)")
        print(f"  {'Avg Win:':<22} ${stats['avg_win']:.2f}  |  Avg Loss: ${stats['avg_loss']:.2f}")
        print(f"  {'Avg R:R:':<22} {stats['avg_rr']:.2f}:1  (target ≥1.5:1)")
        print(f"  {'Max Drawdown:':<22} {stats['max_drawdown_pct']:.1f}%  (max allowed 8%)")
        if stats["streak"] and stats["streak_type"]:
            icon = "🔥" if stats["streak_type"] == "WIN" else "❄️"
            print(f"  {'Current streak:':<22} {icon} {stats['streak']} {stats['streak_type']}s in a row")

        # By pair
        if stats["by_pair"]:
            print(f"\n  {'Pair':<14} {'Trades':>6} {'Win%':>6} {'P&L':>10}")
            print(f"  {'─'*14} {'─'*6} {'─'*6} {'─'*10}")
            for pair, d in sorted(stats["by_pair"].items(), key=lambda x: x[1]["pl"], reverse=True):
                wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
                pl_sign = "+" if d["pl"] >= 0 else ""
                print(f"  {pair:<14} {d['trades']:>6} {wr:>5.0f}% ${pl_sign}{d['pl']:>8.2f}")
    else:
        print(f"  No closed trades yet — agent is paper trading. Check back after first closures.")

    # ── Daily outcome limit status ─────────────────────────────────────────
    closed_today = [
        t for t in journal
        if t.get("status") == "closed"
        and (t.get("closed_at") or "").startswith(today)
        and abs(t.get("realized_pl", 0)) > 0.01
    ]
    wins_today   = sum(1 for t in closed_today if t.get("realized_pl", 0) > 0)
    losses_today = sum(1 for t in closed_today if t.get("realized_pl", 0) < 0)
    daily_blocked = wins_today >= 2 or losses_today >= 2

    print(f"\n  🚦 TODAY'S TRADING STATUS")
    if daily_blocked:
        reason = "2 wins — protecting profits" if wins_today >= 2 else "2 losses — preventing revenge trading"
        print(f"  🔴 DONE FOR TODAY  ({wins_today}W / {losses_today}L — {reason})")
    else:
        slots_w = 2 - wins_today
        slots_l = 2 - losses_today
        print(f"  🟢 ACTIVE  ({wins_today}W / {losses_today}L today — {min(slots_w, slots_l)} outcome(s) before daily limit)")
    print(f"  Rule: 2 wins OR 2 losses = stop for the day (break-evens excluded)")

    # ── What the agent learned ─────────────────────────────────────────────
    print(f"\n  🧠 WHAT THE AGENT LEARNED")
    for line in todays_learning_summary(config):
        print(line)

    # ── Recent lessons ─────────────────────────────────────────────────────
    todays_lessons = [l for l in lessons if l.get("date") == today]
    recent_lessons = sorted(lessons, key=lambda x: x.get("logged_at", ""), reverse=True)[:5]
    if todays_lessons:
        print(f"\n  📓 TODAY'S LESSONS  ({len(todays_lessons)} logged)")
        for l in todays_lessons:
            print(f"  [{l['category'].upper():10s}] {l['lesson']}")
    elif recent_lessons:
        print(f"\n  📓 RECENT LESSONS  (last {len(recent_lessons)})")
        for l in recent_lessons:
            print(f"  [{l['date']}] [{l['category'].upper():10s}] {l['lesson'][:70]}")

    # ── Phase 3 readiness ──────────────────────────────────────────────────
    print(f"\n  🎯 PHASE 3 READINESS  (30 trades required for real money unlock)")
    p3_lines, met_count, all_met = phase3_scorecard(stats)
    for line in p3_lines:
        print(line)

    trades_left = max(0, 30 - stats["closed_trades"])
    if all_met:
        print(f"\n  ✅ ALL CRITERIA MET — Ready for Phase 4 ($10K real money soft launch)!")
    else:
        print(f"\n  {met_count}/6 criteria met  |  {trades_left} more closed trades needed for minimum count")

    # ── Equity chart (shows after 2+ trades, full chart after 30) ─────────
    if stats["closed_trades"] >= 2:
        print(f"\n  📉 EQUITY CURVE  ({'●=win  ○=loss' if stats['closed_trades'] >= 5 else 'building...'})")
        print(render_equity_chart(stats["equity_curve"]))

    # ── Tax tracker (50/50 rule) ───────────────────────────────────────────
    if realized > 0:
        tax_reserve = realized * 0.50
        take_home   = realized * 0.50
        print(f"\n  💼 TAX TRACKER  (50/50 Rule)")
        print(f"  Total realized:     ${realized:+.2f}")
        print(f"  → Set aside (50%):  ${tax_reserve:.2f}  → Move to money market fund")
        print(f"  → Keep/compound:    ${take_home:.2f}")
        print(f"  Tip: Park tax portion in Vanguard money market — earns ~4-5% APY while waiting")

    # ── Principles reminder (rotates daily) ───────────────────────────────
    principles = [
        "Individual trade results don't matter. Focus on process — the edge wins over time.",
        "Give trades room to breathe. ATR × 2–3. Tight stops are the #1 mistake.",
        "Top-down analysis: Weekly/Daily for direction → 4H/1H for entry. Never trade against the higher TF.",
        "Ranging market: wait for price to reach the opposite end of the range before entering.",
        "Trending market: wait for a pullback. Never chase momentum.",
        "A loss is feedback, not failure. Would you take the same setup again next week? If yes, it was correct.",
        "2 wins OR 2 losses = done for the day. Protect profits. Prevent revenge trading.",
        "Move to breakeven when up 2× your stop distance. A breakeven is a win.",
        "50% of realized profits → money market fund for taxes. Do it every time.",
        "Don't try too hard. 20–30 min/day. The market does the heavy lifting.",
    ]
    day_of_year = date.today().timetuple().tm_yday
    tip = principles[day_of_year % len(principles)]
    print(f"\n  💡 TODAY'S REMINDER")
    print(f"  {tip}")

    # ── Days since start ───────────────────────────────────────────────────
    if stats["equity_curve"]:
        start_date = stats["equity_curve"][0]["date"]
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            days_running = (date.today() - start).days
            print(f"\n  ⏱  Agent running for {days_running} day(s)  |  Started: {start_date}")
            days_to_30 = max(0, 30 - days_running)
            if days_to_30 > 0:
                print(f"  📅 Full 30-day performance review available in {days_to_30} day(s)")
        except Exception:
            pass

    print(f"\n{'═'*W}\n")


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "full"

    if cmd == "chart":
        journal = load_journal()
        stats   = compute_full_stats(journal)
        if stats["closed_trades"] >= 2:
            print(render_equity_chart(stats["equity_curve"]))
        else:
            print("Need 2+ closed trades for chart.")

    elif cmd == "phase3":
        journal = load_journal()
        stats   = compute_full_stats(journal)
        lines, met, all_met = phase3_scorecard(stats)
        print(f"\nPhase 3 Readiness — {met}/6 criteria met\n")
        for line in lines:
            print(line)
        print()

    else:
        print_daily_briefing()
