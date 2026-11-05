#!/usr/bin/env python3
"""
universe_review.py — weekly review of the core universe, so it does not go stale.

Reads local files only (daily research JSON, Judge JSON, round-trips.csv), so it
needs no broker credentials and runs in CI. It PROPOSES; it never edits
core_universe.json. Cutting or adding a name is a separate, explicit command
that Alex runs, the same "approving the idea is not implementing it" rule
self_review.py follows.

For every core name it reports growth and risk metrics and flags it for a
possible CUT. For every name outside the core it looks for names the Judge keeps
wanting to approve and proposes them as ADDs.

Usage:
  python3 universe_review.py                      # run the review, write proposals, notify
  python3 universe_review.py show                 # print the latest review
  python3 universe_review.py add SYM "why" [--speculative]
  python3 universe_review.py cut SYM "why"        # moves SYM to probation, still researched
"""

import csv
import glob
import json
import os
import re
import sys
from datetime import date, timedelta

import symbol_stats
import universe_policy

ROOT          = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR      = os.path.join(ROOT, "logs", "paper")
PROPOSAL_DIR  = os.path.join(ROOT, "logs", "proposals")
WATCHLIST     = os.path.join(ROOT, "watchlist.json")
CHANGES_CSV   = os.path.join(PROPOSAL_DIR, "universe-changes.csv")

JUDGE_WINDOW_DAYS     = 7     # recent Judge activity shown per core name
ADD_WINDOW_DAYS       = 14    # how far back to look for names worth adding
DORMANT_DAYS          = 28    # a core name the Judge has not scored this long is stale
MIN_DORMANT_FILES     = 10    # need this many Judge runs in the window to call it dormant
HISTORY_DAYS          = 14    # research sessions used for the trend check
GRACE_DAYS            = 21    # a new core name can only be flagged on hard evidence
HARD_MIN_TRADES       = 3
HARD_AVG_PNL_PCT      = -5.0
DOWNTREND_SHARE       = 0.7
DOWNTREND_MIN_DAYS    = 5
ADD_MIN_APPROVAL_DAYS = 2
ADD_MIN_SCORE         = 8.0
ADD_MIN_MARKET_CAP_B  = 2.0   # quality floor; an unknown cap is skipped, never guessed
MAX_ADD_SUGGESTIONS   = 5

SELL_SIGNALS = ("STRONG_SELL", "WEAK_SELL")


# ── Loading ───────────────────────────────────────────────────────────────────
def _dated(logs_dir, pattern, today, days):
    """{date: parsed json} for files named YYYY-MM-DD<pattern> within `days`."""
    rx = re.compile(r"^(\d{4}-\d{2}-\d{2})" + re.escape(pattern) + r"$")
    since = today - timedelta(days=days)
    out = {}
    for path in glob.glob(os.path.join(logs_dir, "*")):
        m = rx.match(os.path.basename(path))
        if not m:
            continue
        d = date.fromisoformat(m.group(1))
        if since <= d <= today:
            try:
                with open(path) as f:
                    out[d] = json.load(f)
            except (ValueError, OSError):
                continue
    return dict(sorted(out.items()))


def _pct(value):
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def latest_metrics(sym, research_hist):
    """Growth and risk metrics from the most recent session that has real data."""
    for d in sorted(research_hist, reverse=True):
        data = (research_hist[d].get("symbols") or {}).get(sym)
        if not data or "error" in data:
            continue
        t, f = data.get("technicals") or {}, data.get("fundamentals") or {}
        last, ma50 = t.get("last_close"), t.get("ma50")
        return {
            "as_of": d.isoformat(),
            "price": last,
            "signal": t.get("signal"),
            "rsi": t.get("rsi"),
            "atr_pct": t.get("atr_pct"),
            "vs_ma50_pct": round((last - ma50) / ma50 * 100, 1) if last and ma50 else None,
            "revenue_growth_pct": _pct(f.get("revenue_growth")),
            "market_cap_b": f.get("market_cap_b"),
            "profitable": f.get("is_profitable"),
            "analyst_buy": f.get("analyst_buy_count"),
            "analyst_sell": f.get("analyst_sell_count"),
            "upside_pct": f.get("upside_pct"),
            "short_float": f.get("short_float"),
        }
    return None


# ── Core review ───────────────────────────────────────────────────────────────
def evaluate_core(sym, entry, today, research_hist, judge_hist, stats):
    flags = []          # (severity, text)
    rec = stats.get(sym)
    age = (today - date.fromisoformat(entry["added"])).days
    speculative = bool(entry.get("speculative"))

    if (rec and rec["trades"] >= HARD_MIN_TRADES
            and rec["avg_pnl_pct"] is not None and rec["avg_pnl_pct"] <= HARD_AVG_PNL_PCT):
        flags.append(("hard", f"losing record: {rec['trades']} closed trades averaging "
                              f"{rec['avg_pnl_pct']:+.1f}%"))

    signals = [(((r.get("symbols") or {}).get(sym) or {}).get("technicals") or {}).get("signal")
               for r in research_hist.values()]
    signals = [s for s in signals if s]
    if len(signals) >= DOWNTREND_MIN_DAYS:
        share = sum(s in SELL_SIGNALS for s in signals) / len(signals)
        if share >= DOWNTREND_SHARE:
            flags.append(("soft", f"downtrend: sell signal in {round(share * len(signals))} of "
                                  f"the last {len(signals)} sessions"))

    if rec and rec["trades"] >= 1 and rec["wins"] == 0 and (rec["avg_pnl_pct"] or 0) < 0:
        flags.append(("soft", f"no winning trade yet ({rec['trades']} closed, "
                              f"{rec['avg_pnl_pct']:+.1f}% avg)"))

    metrics = latest_metrics(sym, research_hist)
    if metrics and (metrics["analyst_sell"] or 0) > (metrics["analyst_buy"] or 0):
        flags.append(("soft", f"analyst consensus is negative "
                              f"({metrics['analyst_buy']} buy / {metrics['analyst_sell']} sell)"))

    scored_days = [d for d, j in judge_hist.items() if sym in (j.get("scores") or {})]
    window = {d: j for d, j in judge_hist.items() if d >= today - timedelta(days=DORMANT_DAYS)}
    if len(window) >= MIN_DORMANT_FILES and not any(d in window for d in scored_days):
        flags.append(("soft", f"dormant: no buy signal reached the Judge in {DORMANT_DAYS} days"))

    recent = [j["scores"][sym] for d, j in judge_hist.items()
              if d >= today - timedelta(days=JUDGE_WINDOW_DAYS) and sym in (j.get("scores") or {})]
    hard = [t for s, t in flags if s == "hard"]
    soft = [t for s, t in flags if s == "soft"]
    needed = 1 if speculative else 2
    suggest_cut = bool(hard) or (age >= GRACE_DAYS and len(soft) >= needed)

    return {
        "symbol": sym,
        "speculative": speculative,
        "days_in_core": age,
        "in_grace": age < GRACE_DAYS,
        "record": rec,
        "metrics": metrics,
        "judge_7d": {"scored": len(recent),
                     "approved": sum(1 for r in recent if r.get("recommendation") == "APPROVE"),
                     "best_score": max((r.get("score", 0) for r in recent), default=None)},
        "flags": hard + soft,
        "suggest_cut": suggest_cut,
    }


# ── Add suggestions ───────────────────────────────────────────────────────────
def suggest_additions(cfg, today, research_hist, judge_hist, stats):
    skip = set(cfg["core"]) | set(cfg["hedge"])
    seen = {}
    for d, j in judge_hist.items():
        if d < today - timedelta(days=ADD_WINDOW_DAYS):
            continue
        for sym, s in (j.get("scores") or {}).items():
            if sym in skip:
                continue
            wanted = (s.get("recommendation") == "APPROVE"
                      or (s.get("universe_policy") or {}).get("would_change"))
            if wanted:
                seen.setdefault(sym, {})[d] = s.get("score", 0)

    out = []
    for sym, days in seen.items():
        best = max(days.values())
        rec = stats.get(sym)
        metrics = latest_metrics(sym, research_hist)
        if len(days) < ADD_MIN_APPROVAL_DAYS or best < ADD_MIN_SCORE:
            continue
        if not metrics or metrics["market_cap_b"] is None or metrics["market_cap_b"] < ADD_MIN_MARKET_CAP_B:
            continue
        if rec and rec["trades"] >= 1 and (rec["avg_pnl_pct"] or 0) < 0:
            continue
        out.append({
            "symbol": sym,
            "approval_days": len(days),
            "best_score": best,
            "on_probation": sym in cfg["probation"],
            "metrics": metrics,
            "record": rec,
        })
    out.sort(key=lambda c: (c["approval_days"], c["best_score"]), reverse=True)
    return out[:MAX_ADD_SUGGESTIONS]


# ── Review ────────────────────────────────────────────────────────────────────
def build_review(today=None, logs_dir=LOGS_DIR, config_path=None):
    today = today or date.fromisoformat(_today_iso())
    config_path = config_path or universe_policy.CONFIG_PATH
    cfg = universe_policy.load_config(config_path)
    if cfg is None:
        raise SystemExit("core_universe.json not found")

    research_hist = _dated(logs_dir, ".json", today, HISTORY_DAYS)
    research_hist = {d: r for d, r in research_hist.items() if "symbols" in r}
    judge_hist = _dated(logs_dir, "-judge.json", today, DORMANT_DAYS)
    stats = symbol_stats.load_stats(logs_dir)

    core = [evaluate_core(sym, entry, today, research_hist, judge_hist, stats)
            for sym, entry in cfg["entries"].items()]
    spec = [c["symbol"] for c in core if c["speculative"]]
    return {
        "date": today.isoformat(),
        "status": cfg["status"],
        "core_size": len(core),
        "speculative_sleeve": spec,
        "research_sessions": len(research_hist),
        "judge_runs": len(judge_hist),
        "suggested_cuts": [c for c in core if c["suggest_cut"]],
        "suggested_adds": suggest_additions(cfg, today, research_hist, judge_hist, stats),
        "core": core,
    }


def _today_iso():
    from market_time import et_today_iso
    return et_today_iso()


# ── Report ────────────────────────────────────────────────────────────────────
def _f(v, spec="", dash="-", suffix=""):
    return format(v, spec) + suffix if v is not None else dash


def render_markdown(r):
    L = [f"# Core universe review — {r['date']}",
         "",
         f"{r['core_size']} core names ({len(r['speculative_sleeve'])} speculative: "
         f"{', '.join(r['speculative_sleeve']) or 'none'}). Based on {r['research_sessions']} research "
         f"sessions and {r['judge_runs']} Judge runs. **Suggestions only: nothing here has been changed.**",
         ""]

    L.append("## Suggested cuts")
    if r["suggested_cuts"]:
        for c in r["suggested_cuts"]:
            L.append(f"- **{c['symbol']}**" + (" (speculative)" if c["speculative"] else "")
                     + ": " + "; ".join(c["flags"]))
        L.append("")
        L.append("To act: `python3 universe_review.py cut SYMBOL \"reason\"`. This stops new buys only. "
                 "An open position keeps its stop-loss and exit rules.")
    else:
        L.append("- None.")

    L += ["", "## Suggested additions"]
    if r["suggested_adds"]:
        for a in r["suggested_adds"]:
            m = a["metrics"]
            L.append(f"- **{a['symbol']}**: would have been approved on {a['approval_days']} days "
                     f"(best score {a['best_score']}), market cap ${m['market_cap_b']:,.1f}B, "
                     f"revenue growth {_f(m['revenue_growth_pct'], '+.1f', suffix='%')}, ATR {_f(m['atr_pct'], suffix='%')}"
                     + (" [on probation]" if a["on_probation"] else ""))
        L.append("")
        L.append("To act: `python3 universe_review.py add SYMBOL \"reason\" [--speculative]`.")
    else:
        L.append("- None met the bar "
                 f"({ADD_MIN_APPROVAL_DAYS}+ approval days, score >= {ADD_MIN_SCORE}, "
                 f"market cap >= ${ADD_MIN_MARKET_CAP_B:.0f}B, no losing record).")

    L += ["", "## Core names", "",
          "| Symbol | Days | Signal | vs MA50 | RSI | ATR% | Rev growth | Cap $B | Trades | Win% | Avg P&L | 7d Judge (appr/scored) | Flags |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in sorted(r["core"], key=lambda c: c["symbol"]):
        m, rec, j = c["metrics"] or {}, c["record"] or {}, c["judge_7d"]
        wr = None if rec.get("win_rate") is None else rec["win_rate"] * 100
        L.append(f"| {c['symbol']}{'*' if c['speculative'] else ''} | {c['days_in_core']}"
                 f"{' (new)' if c['in_grace'] else ''} | {_f(m.get('signal'))} | "
                 f"{_f(m.get('vs_ma50_pct'), '+.1f', suffix='%')} | {_f(m.get('rsi'), '.0f')} | {_f(m.get('atr_pct'), '.1f')} | "
                 f"{_f(m.get('revenue_growth_pct'), '+.1f', suffix='%')} | {_f(m.get('market_cap_b'), ',.1f')} | "
                 f"{rec.get('trades', 0)} | {_f(wr, '.0f')} | {_f(rec.get('avg_pnl_pct'), '+.1f', suffix='%')} | "
                 f"{j['approved']}/{j['scored']} | {'; '.join(c['flags']) or 'ok'} |")
    L += ["", "`*` = speculative sleeve. A new name is only flagged on a losing record until it has been in the core 21 days."]
    return "\n".join(L) + "\n"


def write_review(r, proposal_dir=PROPOSAL_DIR):
    os.makedirs(proposal_dir, exist_ok=True)
    base = os.path.join(proposal_dir, f"universe-review-{r['date']}")
    with open(base + ".json", "w") as f:
        json.dump(r, f, indent=2)
    with open(base + ".md", "w") as f:
        f.write(render_markdown(r))
    return base + ".md"


def notify_review(r):
    import notify
    cuts = [c["symbol"] for c in r["suggested_cuts"]]
    adds = [a["symbol"] for a in r["suggested_adds"]]
    if not cuts and not adds:
        return False
    parts = []
    if cuts:
        parts.append("Consider cutting: " + ", ".join(cuts))
    if adds:
        parts.append("Consider adding: " + ", ".join(adds))
    return notify.send("Weekly core-universe review", "\n".join(parts), tags="chart_with_upwards_trend")


# ── Add / cut (explicit human actions) ────────────────────────────────────────
def _log_change(action, sym, reason, today, changes_csv):
    os.makedirs(os.path.dirname(changes_csv), exist_ok=True)
    new = not os.path.exists(changes_csv)
    with open(changes_csv, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "action", "symbol", "reason"])
        w.writerow([today, action, sym, reason])


def _save_config(config_path, cfg, previous_text):
    with open(config_path, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        universe_policy.load_config(config_path)
    except ValueError:
        with open(config_path, "w") as f:
            f.write(previous_text)
        raise


def add_symbol(sym, why, speculative=False, today=None, config_path=None,
               watchlist_path=WATCHLIST, changes_csv=CHANGES_CSV):
    sym, today = sym.upper(), today or _today_iso()
    config_path = config_path or universe_policy.CONFIG_PATH
    with open(config_path) as f:
        text = f.read()
    cfg = json.loads(text)
    if sym in cfg["core"]:
        raise ValueError(f"{sym} is already in the core")
    if sym in cfg.get("hedge", {}):
        raise ValueError(f"{sym} is a hedge instrument, not a core name")
    cfg.get("probation", {}).pop(sym, None)
    entry = {"added": today, "why": why}
    if speculative:
        entry["speculative"] = True
    cfg["core"][sym] = entry
    _save_config(config_path, cfg, text)

    with open(watchlist_path) as f:
        wl = json.load(f)
    if sym not in {w["symbol"] for w in wl["watchlist"]}:
        wl["watchlist"].append({
            "symbol": sym, "description": why,
            "max_allocation_pct": 2 if speculative else 3,
            "notes": f"Added {today} to the core universe. {why}",
        })
        with open(watchlist_path, "w") as f:
            json.dump(wl, f, indent=2, ensure_ascii=False)
            f.write("\n")
    _log_change("add", sym, why, today, changes_csv)


def cut_symbol(sym, why, today=None, config_path=None, changes_csv=CHANGES_CSV):
    sym, today = sym.upper(), today or _today_iso()
    config_path = config_path or universe_policy.CONFIG_PATH
    with open(config_path) as f:
        text = f.read()
    cfg = json.loads(text)
    if sym not in cfg["core"]:
        raise ValueError(f"{sym} is not in the core")
    del cfg["core"][sym]
    cfg.setdefault("probation", {})[sym] = f"Cut {today}: {why}"
    _save_config(config_path, cfg, text)
    _log_change("cut", sym, why, today, changes_csv)


# ── CLI ───────────────────────────────────────────────────────────────────────
def _latest_report():
    files = sorted(glob.glob(os.path.join(PROPOSAL_DIR, "universe-review-*.md")))
    return files[-1] if files else None


if __name__ == "__main__":
    args = sys.argv[1:]
    action = args[0] if args else "review"

    if action == "review":
        review = build_review()
        path = write_review(review)
        print(render_markdown(review))
        print(f"[universe_review] wrote {path}", file=sys.stderr)
        notify_review(review)

    elif action == "show":
        p = _latest_report()
        print(open(p).read() if p else "No review has been run yet.")

    elif action == "add" and len(args) >= 3:
        add_symbol(args[1], args[2], speculative="--speculative" in args[3:])
        print(f"Added {args[1].upper()} to the core.")

    elif action == "cut" and len(args) >= 3:
        cut_symbol(args[1], args[2])
        print(f"Cut {args[1].upper()} from the core; it is on probation and still researched.")

    else:
        print(__doc__)
        sys.exit(2)
