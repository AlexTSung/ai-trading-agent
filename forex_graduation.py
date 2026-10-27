#!/usr/bin/env python3
"""
forex_graduation.py — Real-money graduation checker + post-trade learning loop.

Runs after every closed trade. Computes graduation criteria, detects what made
each trade win or lose, and writes concrete weight/threshold adjustments back
to forex_config.json so the judge makes smarter decisions next time.

Usage:
  python3 forex_graduation.py check       # Graduation criteria pass/fail
  python3 forex_graduation.py learn       # Post-trade learning from all closed trades
  python3 forex_graduation.py eod         # Both: learn then check (run after each session)
  python3 forex_graduation.py report      # Human-readable report, no writes
"""

import os, sys, json, math
from datetime import date, datetime, timezone
from pathlib import Path
import requests

ROOT       = Path(__file__).parent
JOURNAL    = ROOT / "logs" / "forex" / "trade_journal.json"
CONFIG     = ROOT / "forex_config.json"
LEARN_LOG  = ROOT / "logs" / "forex" / "learning_log.jsonl"
GRAD_REPORT = ROOT / "logs" / "forex" / f"graduation-{date.today().isoformat()}.md"

MODE = sys.argv[1] if len(sys.argv) > 1 else "eod"

# ── Graduation thresholds ─────────────────────────────────────────────────────
GRAD = {
    "min_trades":       15,
    "min_win_rate":     0.55,
    "min_profit_factor": 1.5,
    "min_expectancy":   0,        # $ per trade
    "min_return_pct":   3.0,      # % on starting balance
    "starting_balance": 100_000,
}

# ── Load data ─────────────────────────────────────────────────────────────────

def load_journal():
    if not JOURNAL.exists():
        return []
    data = json.loads(JOURNAL.read_text())
    return data if isinstance(data, list) else data.get("trades", [])


def load_config():
    if not CONFIG.exists():
        return {}
    return json.loads(CONFIG.read_text())


def save_config(cfg):
    cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
    CONFIG.write_text(json.dumps(cfg, indent=2))


def fetch_closed_trades_from_oanda():
    env      = os.getenv("OANDA_ENV", "practice")
    api_key  = os.getenv("OANDA_API_KEY", "")
    acct_id  = os.getenv("OANDA_ACCOUNT_ID", "")
    if not api_key or not acct_id:
        return []
    base = "https://api-fxpractice.oanda.com" if env == "practice" else "https://api-fxtrade.oanda.com"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(f"{base}/v3/accounts/{acct_id}/trades",
                         headers=headers, params={"state": "CLOSED", "count": 50}, timeout=10)
        return r.json().get("trades", [])
    except Exception as e:
        print(f"  [WARN] OANDA fetch failed: {e}")
        return []


def build_closed_set(journal_entries, oanda_trades):
    """Merge journal open/close pairs with OANDA fills for full trade records."""
    opens  = {e["trade_id"]: e for e in journal_entries if e.get("event") == "open"}
    closes = {e["trade_id"]: e for e in journal_entries if e.get("event") == "close"}

    records = []
    for tid, o in opens.items():
        c = closes.get(tid)
        oanda = next((t for t in oanda_trades if t["id"] == tid), None)

        if not c and not oanda:
            continue  # still open

        entry  = float(o.get("entry_price", 0))
        close_price = float(c["exit_price"]) if c else float(oanda.get("averageClosePrice", entry))
        pl     = float(c["pnl_usd"]) if c else float(oanda.get("realizedPL", 0))
        direction = o.get("direction", "LONG")
        pip    = 0.01 if "JPY" in o.get("pair", "") else 0.0001
        pips   = round((close_price - entry) / pip, 1) if direction == "LONG" else round((entry - close_price) / pip, 1)

        records.append({
            "trade_id":   tid,
            "pair":       o.get("pair"),
            "direction":  direction,
            "entry":      entry,
            "exit":       close_price,
            "pips":       pips,
            "pl":         pl,
            "win":        pl > 0,
            "score":      float(o.get("score") or 0),
            "factors":    o.get("factors", {}),
            "stop":       float(o.get("stop_price", 0)),
            "tp1":        float(o.get("tp1_price", 0)),
            "opened":     o.get("timestamp", "")[:10],
            "closed":     c.get("timestamp", oanda.get("closeTime", ""))[:10] if c else "",
        })
    return records


# ── Graduation criteria ───────────────────────────────────────────────────────

def check_graduation(records, balance):
    wins         = [r for r in records if r["win"]]
    losses       = [r for r in records if not r["win"]]
    n            = len(records)
    win_rate     = len(wins) / n if n else 0
    gross_wins   = sum(r["pl"] for r in wins)
    gross_losses = abs(sum(r["pl"] for r in losses))
    pf           = gross_wins / gross_losses if gross_losses else float("inf")
    total_pl     = sum(r["pl"] for r in records)
    avg_win      = gross_wins / len(wins) if wins else 0
    avg_loss     = gross_losses / len(losses) if losses else 0
    expectancy   = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    ret_pct      = total_pl / GRAD["starting_balance"] * 100

    criteria = {
        "trades":         (n,        GRAD["min_trades"],        n >= GRAD["min_trades"]),
        "win_rate":       (f"{win_rate:.0%}", f"{GRAD['min_win_rate']:.0%}", win_rate >= GRAD["min_win_rate"]),
        "profit_factor":  (f"{pf:.2f}", f"{GRAD['min_profit_factor']:.2f}", pf >= GRAD["min_profit_factor"]),
        "expectancy":     (f"${expectancy:.2f}", f">${GRAD['min_expectancy']}", expectancy > GRAD["min_expectancy"]),
        "net_return":     (f"{ret_pct:.2f}%", f"{GRAD['min_return_pct']:.1f}%", ret_pct >= GRAD["min_return_pct"]),
    }

    all_pass = all(v[2] for v in criteria.values())
    return criteria, all_pass, {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": win_rate, "pf": pf, "total_pl": total_pl,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "expectancy": expectancy, "ret_pct": ret_pct,
        "gross_wins": gross_wins, "gross_losses": gross_losses,
    }


# ── Post-trade learning ───────────────────────────────────────────────────────

def extract_factor_scores(record):
    """Pull the 0-1 raw scores for each judge factor from the trade record."""
    out = {}
    for fname, fdata in (record.get("factors") or {}).items():
        if isinstance(fdata, dict):
            out[fname] = float(fdata.get("score", 0))
    return out


def learn_from_trades(records, cfg):
    """
    Analyse which judge factors predicted wins vs losses.
    Adjust weights toward what actually worked. Write findings to learning log.
    """
    if len(records) < 3:
        print("  [LEARN] Need at least 3 closed trades to learn. Skipping.")
        return cfg, []

    # v1.4.0: weight lock — prevent overfitting on small sample (same lock as forex_optimizer.py)
    learning_lock = cfg.get("weight_learning_disabled_until_trades", 50)
    if len(records) < learning_lock:
        print(f"  [LEARN] Weight learning locked: need {learning_lock} closed trades, have {len(records)}. Skipping weight update.")
        return cfg, []

    wins   = [r for r in records if r["win"]]
    losses = [r for r in records if not r["win"]]

    # Average factor score for wins vs losses
    factor_names = ["carry", "momentum", "technical", "session", "volatility", "risk_reward"]
    win_avg  = {f: 0.0 for f in factor_names}
    loss_avg = {f: 0.0 for f in factor_names}

    for r in wins:
        fs = extract_factor_scores(r)
        for f in factor_names:
            win_avg[f] += fs.get(f, 0)
    for r in losses:
        fs = extract_factor_scores(r)
        for f in factor_names:
            loss_avg[f] += fs.get(f, 0)

    for f in factor_names:
        win_avg[f]  /= max(len(wins), 1)
        loss_avg[f] /= max(len(losses), 1)

    # Predictive power = how much higher the avg score is in wins vs losses
    predictive = {f: win_avg[f] - loss_avg[f] for f in factor_names}

    # Build new weights proportional to predictive power (clamped, sum = 10)
    current_weights = cfg.get("weights", {f: 10/len(factor_names) for f in factor_names})
    new_weights = dict(current_weights)

    lessons = []

    for f in factor_names:
        delta = predictive[f]
        current_w = current_weights.get(f, 1.0)

        if delta > 0.15:
            # This factor predicted wins well — increase weight by up to 10%
            bump = min(current_w * 0.10, 0.3)
            new_weights[f] = round(current_w + bump, 3)
            lessons.append({
                "factor": f,
                "action": "increase_weight",
                "old_weight": current_w,
                "new_weight": new_weights[f],
                "reason": f"avg score {win_avg[f]:.2f} in wins vs {loss_avg[f]:.2f} in losses (+{delta:.2f} predictive edge)",
            })
        elif delta < -0.10:
            # This factor was higher in losses than wins — reduce weight
            cut = min(current_w * 0.10, 0.2)
            new_weights[f] = round(max(current_w - cut, 0.5), 3)
            lessons.append({
                "factor": f,
                "action": "decrease_weight",
                "old_weight": current_w,
                "new_weight": new_weights[f],
                "reason": f"avg score {win_avg[f]:.2f} in wins vs {loss_avg[f]:.2f} in losses — not predictive (delta={delta:.2f})",
            })

    # Normalize weights to sum exactly to 10.0
    total = sum(new_weights.values())
    for f in factor_names:
        new_weights[f] = round(new_weights[f] / total * 10.0, 3)

    cfg["weights"] = new_weights
    cfg["trades_analyzed"] = len(records)

    # Pair-level stats — track win rate per pair
    pair_stats = {}
    for r in records:
        p = r["pair"]
        if p not in pair_stats:
            pair_stats[p] = {"wins": 0, "losses": 0, "total_pl": 0.0, "pips": 0.0}
        pair_stats[p]["wins"]     += 1 if r["win"] else 0
        pair_stats[p]["losses"]   += 0 if r["win"] else 1
        pair_stats[p]["total_pl"] += r["pl"]
        pair_stats[p]["pips"]     += r["pips"]
    cfg["pair_stats"] = pair_stats

    # Direction bias — do we win more going long or short?
    long_wins  = [r for r in records if r["direction"] == "LONG"  and r["win"]]
    long_total = [r for r in records if r["direction"] == "LONG"]
    short_wins = [r for r in records if r["direction"] == "SHORT" and r["win"]]
    short_total = [r for r in records if r["direction"] == "SHORT"]
    cfg["direction_bias"] = {
        "long_wr":  round(len(long_wins)/len(long_total), 2) if long_total else None,
        "short_wr": round(len(short_wins)/len(short_total), 2) if short_total else None,
        "sample":   len(records),
    }

    # Auto-deprioritize pairs with 0 wins and 2+ losses
    deprioritized = cfg.get("deprioritized_pairs", [])
    for pair, stats in pair_stats.items():
        if stats["wins"] == 0 and stats["losses"] >= 2 and pair not in deprioritized:
            deprioritized.append(pair)
            lessons.append({
                "factor":  "pair_filter",
                "action":  "deprioritize_pair",
                "pair":    pair,
                "reason":  f"0W/{stats['losses']}L, total P&L ${stats['total_pl']:.2f} — auto-deprioritized",
            })
    cfg["deprioritized_pairs"] = deprioritized

    # Write learning log
    entry = {
        "date": date.today().isoformat(),
        "trades_analyzed": len(records),
        "win_avg_factors":  {f: round(win_avg[f], 3) for f in factor_names},
        "loss_avg_factors": {f: round(loss_avg[f], 3) for f in factor_names},
        "predictive_power": {f: round(predictive[f], 3) for f in factor_names},
        "weight_changes":   lessons,
        "new_weights":      new_weights,
        "pair_stats":       pair_stats,
        "direction_bias":   cfg["direction_bias"],
    }
    with open(LEARN_LOG, "a") as fh:
        fh.write(json.dumps(entry) + "\n")

    return cfg, lessons


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(stats, criteria, all_pass, lessons, cfg):
    trades_needed = max(0, GRAD["min_trades"] - stats["n"])
    ret_needed    = max(0, GRAD["min_return_pct"] - stats["ret_pct"])

    lines = [
        f"# Forex Graduation Report — {date.today().isoformat()}",
        f"",
        f"## Verdict: {'✅ READY FOR REAL MONEY — pending Alex approval' if all_pass else '❌ NOT YET READY'}",
        f"",
        f"## Graduation Criteria",
        f"| Criterion | Required | Current | Status |",
        f"|-----------|----------|---------|--------|",
    ]
    icons = {"trades": "📊", "win_rate": "🎯", "profit_factor": "💰",
             "expectancy": "📈", "net_return": "🏦"}
    labels = {"trades": "Closed trades", "win_rate": "Win rate",
              "profit_factor": "Profit factor", "expectancy": "Expectancy/trade",
              "net_return": "Net return"}
    for key, (current, required, passed) in criteria.items():
        status = "✅" if passed else "❌"
        lines.append(f"| {icons[key]} {labels[key]} | {required} | {current} | {status} |")

    lines += [
        f"",
        f"## Performance Detail",
        f"| | |",
        f"|---|---|",
        f"| Total closed trades | {stats['n']} |",
        f"| Wins / Losses | {stats['wins']}W / {stats['losses']}L |",
        f"| Win rate | {stats['win_rate']:.0%} |",
        f"| Gross wins | ${stats['gross_wins']:,.2f} |",
        f"| Gross losses | ${stats['gross_losses']:,.2f} |",
        f"| Profit factor | {stats['pf']:.2f} |",
        f"| Avg win | ${stats['avg_win']:.2f} |",
        f"| Avg loss | ${stats['avg_loss']:.2f} |",
        f"| Expectancy/trade | ${stats['expectancy']:.2f} |",
        f"| Total realized P&L | ${stats['total_pl']:+,.2f} |",
        f"| Net return | {stats['ret_pct']:.2f}% |",
        f"",
        f"## Blockers to Graduation",
    ]

    if trades_needed > 0:
        lines.append(f"- Need **{trades_needed} more closed trades** (have {stats['n']}, need {GRAD['min_trades']})")
    if ret_needed > 0:
        lines.append(f"- Need **+{ret_needed:.2f}% more return** (have {stats['ret_pct']:.2f}%, need {GRAD['min_return_pct']:.1f}%)")
    if all_pass:
        lines.append("- All criteria met ✅ — awaiting manual approval from Alex")

    lines += ["", "## Per-Pair Stats"]
    pair_stats = cfg.get("pair_stats", {})
    if pair_stats:
        lines.append("| Pair | Wins | Losses | Total P&L | Total Pips | Status |")
        lines.append("|------|------|--------|-----------|------------|--------|")
        for pair, s in sorted(pair_stats.items(), key=lambda x: -x[1]["total_pl"]):
            deprio = "⛔ Deprioritized" if pair in cfg.get("deprioritized_pairs", []) else "✅ Active"
            lines.append(f"| {pair} | {s['wins']} | {s['losses']} | ${s['total_pl']:+.2f} | {s['pips']:+.1f} | {deprio} |")

    lines += [
        f"",
        f"## What the Agent Learned ({len(lessons)} adjustments)",
    ]
    if lessons:
        for l in lessons:
            if l["action"] in ("increase_weight", "decrease_weight"):
                arrow = "⬆️" if l["action"] == "increase_weight" else "⬇️"
                lines.append(f"- {arrow} **{l['factor']}** weight: {l['old_weight']} → {l['new_weight']} — {l['reason']}")
            elif l["action"] == "deprioritize_pair":
                lines.append(f"- ⛔ **{l['pair']}** auto-deprioritized: {l['reason']}")
    else:
        lines.append("- No weight changes this run (need more data or no strong signal)")

    db = cfg.get("direction_bias", {})
    lines += [
        f"",
        f"## Direction Bias",
        f"- Long win rate:  {db.get('long_wr', 'N/A')}",
        f"- Short win rate: {db.get('short_wr', 'N/A')}",
        f"- Sample: {db.get('sample', 0)} trades",
        f"",
        f"## Updated Judge Weights",
        f"```json",
        json.dumps(cfg.get("weights", {}), indent=2),
        f"```",
        f"",
        f"## Estimated Path to Graduation",
        f"- Trades needed: **{trades_needed}** more  ({'~{} weeks at 1-2/week'.format(math.ceil(trades_needed/1.5)) if trades_needed > 0 else 'done ✅'})",
        f"- Return needed: **+{ret_needed:.2f}%** more  ({'done ✅' if ret_needed <= 0 else f'~${ret_needed/100*100000:,.0f} more profit needed'})",
        f"- Manual approval required before any real money is deployed.",
    ]

    GRAD_REPORT.write_text("\n".join(lines))
    print(f"  [REPORT] → {GRAD_REPORT}")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{date.today()}] Forex graduation + learning — mode: {MODE}")

    journal   = load_journal()
    cfg       = load_config()
    oanda_trades = fetch_closed_trades_from_oanda()

    records = build_closed_set(journal, oanda_trades)
    print(f"  Closed trades found: {len(records)} (journal) + {len(oanda_trades)} (OANDA)")

    # Fall back to OANDA-only data if journal is sparse
    if not records and oanda_trades:
        records = []
        for t in oanda_trades:
            pl = float(t.get("realizedPL", 0))
            units = float(t.get("initialUnits", 0))
            direction = "LONG" if units > 0 else "SHORT"
            entry = float(t["price"])
            close = float(t.get("averageClosePrice", entry))
            pip = 0.01 if "JPY" in t.get("instrument", "") else 0.0001
            pips = round((close - entry) / pip, 1) if direction == "LONG" else round((entry - close) / pip, 1)
            records.append({
                "trade_id":  t["id"],
                "pair":      t["instrument"],
                "direction": direction,
                "entry":     entry,
                "exit":      close,
                "pips":      pips,
                "pl":        pl,
                "win":       pl > 0,
                "score":     0,
                "factors":   {},
                "stop":      0,
                "tp1":       0,
                "opened":    t.get("openTime", "")[:10],
                "closed":    t.get("closeTime", "")[:10],
            })
        print(f"  Using {len(records)} OANDA-only records (no journal factor data).")

    if not records:
        print("  No closed trades found. Nothing to learn from yet.")
        return

    lessons = []
    if MODE in ("learn", "eod"):
        cfg, lessons = learn_from_trades(records, cfg)
        save_config(cfg)
        print(f"  [LEARN] {len(lessons)} weight adjustment(s) applied → forex_config.json")
        for l in lessons:
            if l["action"] in ("increase_weight", "decrease_weight"):
                arrow = "▲" if l["action"] == "increase_weight" else "▼"
                print(f"    {arrow} {l['factor']}: {l['old_weight']} → {l['new_weight']}")
            elif l["action"] == "deprioritize_pair":
                print(f"    ⛔ {l['pair']} deprioritized")

    criteria, all_pass, stats = check_graduation(records, GRAD["starting_balance"])

    print()
    print("  === GRADUATION CHECK ===")
    for key, (current, required, passed) in criteria.items():
        icon = "✅" if passed else "❌"
        print(f"  {icon} {key:18} {current:>10}  (need {required})")
    print()
    print(f"  {'✅ READY — all criteria met (needs Alex approval)' if all_pass else '❌ NOT READY'}")

    if MODE in ("check", "eod", "report"):
        write_report(stats, criteria, all_pass, lessons, cfg)

    return all_pass


if __name__ == "__main__":
    main()
