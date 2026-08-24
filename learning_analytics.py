#!/usr/bin/env python3
"""
learning_analytics.py — What the trade record actually says.

The learning loop previously proposed exactly two adjustments: an approve
threshold bump and a news-weight nudge. Faster cadence on a two-parameter search
hits its ceiling immediately. The round-trip log plus the daily judge and
research artefacts can answer far more consequential questions:

  - Are discovery stocks losing money relative to the watchlist?
  - Is a 20% Tier 1 stop right, or are we stopping out of trades that recover?
  - Are we exiting winners too early?
  - Does a higher Judge score actually predict a better outcome?
  - Which of the six criteria carry real predictive power?
  - What did the trades we VETOED go on to do?

That last one matters most right now: with almost everything being vetoed, the
only way to know whether the gates are too tight is to measure what the rejected
candidates did next.

Every finding carries its sample size and a confidence label. Nothing here
changes a parameter — it produces evidence. judge.py decides how much of it is
allowed to affect scoring, via the LEARNING_TIERS ladder.

Usage:
  python3 learning_analytics.py            # full report
  python3 learning_analytics.py json       # machine-readable
"""

import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from statistics import mean

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "paper")
ROUND_TRIPS = os.path.join(JOURNAL_DIR, "round-trips.csv")
STATE_PATH  = os.path.join(JOURNAL_DIR, "agent-state.json")
OUT_JSON    = os.path.join(ROOT, "logs", "learnings", "analytics.json")

# Below this many samples a difference is an observation, not a finding. Stated
# on every result so a 2-trade "pattern" is never read as a conclusion.
MIN_N_SUGGESTIVE = 5
MIN_N_ACTIONABLE = 10

TIER1 = {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"}
TIER2 = {"DDOG", "TSLA", "QCOM"}
INVERSE = {"SQQQ", "SPXS", "SOXS"}


# ── loading ───────────────────────────────────────────────────────────────────
def tier_of(symbol: str) -> str:
    if symbol in INVERSE:
        return "INVERSE"
    if symbol in TIER1:
        return "TIER1"
    if symbol in TIER2:
        return "TIER2"
    return "TIER3"


def load_round_trips() -> list:
    if not os.path.exists(ROUND_TRIPS):
        return []
    trips = []
    with open(ROUND_TRIPS) as f:
        for row in csv.DictReader(f):
            try:
                trips.append({
                    "symbol":     row["symbol"],
                    "entry_date": row["entry_date"],
                    "exit_date":  row["exit_date"],
                    "entry":      float(row["entry"]),
                    "exit":       float(row["exit"]),
                    "pnl_pct":    float(row["pnl_pct"]),
                    "pnl_dollar": float(row["pnl_dollar"]),
                    "hold_days":  int(float(row["hold_days"])),
                    "tier":       tier_of(row["symbol"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return trips


def _daily_files(suffix: str) -> dict:
    """{date: parsed json} for logs/paper/YYYY-MM-DD<suffix>.json"""
    out = {}
    pattern = os.path.join(JOURNAL_DIR, f"20??-??-??{suffix}.json")
    for path in sorted(glob.glob(pattern)):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
        if not m:
            continue
        try:
            with open(path) as f:
                out[m.group(1)] = json.load(f)
        except (OSError, ValueError):
            continue
    return out


def load_judge_history() -> dict:
    """{(date, symbol): judge result}"""
    out = {}
    for d, doc in _daily_files("-judge").items():
        for sym, scored in (doc.get("scores") or {}).items():
            out[(d, sym)] = scored
    return out


def load_research_history() -> tuple:
    """
    Returns (technicals, prices, regime):
      technicals {(date, symbol): technicals dict}
      prices     {symbol: {date: last_close}}
      regime     {date: "RISK_ON"|"RISK_OFF"|"UNKNOWN"}
    """
    technicals, prices, regime = {}, defaultdict(dict), {}
    for d, doc in _daily_files("").items():
        symbols = doc.get("symbols") or {}
        for sym, data in symbols.items():
            t = data.get("technicals") or {}
            if "error" in t or not t:
                continue
            technicals[(d, sym)] = dict(t, source=data.get("source", "watchlist"))
            close = t.get("last_close")
            if isinstance(close, (int, float)) and close > 0:
                prices[sym][d] = float(close)
        spy = (symbols.get("SPY") or {}).get("technicals") or {}
        ma20, ma50 = spy.get("ma20"), spy.get("ma50")
        if isinstance(ma20, (int, float)) and isinstance(ma50, (int, float)):
            regime[d] = "RISK_ON" if ma20 > ma50 else "RISK_OFF"
        else:
            regime[d] = "UNKNOWN"
    return technicals, dict(prices), regime


def load_vetoes() -> list:
    """Every Judge veto, with its reason, from the session records."""
    out = []
    for d, doc in _daily_files("-session").items():
        for v in (doc.get("vetoed_by_judge") or []):
            out.append({
                "date": d, "symbol": v.get("symbol"),
                "signal": v.get("signal"), "score": v.get("judge_score"),
                "is_discovery": bool(v.get("is_discovery")),
                "reason": _veto_class(v.get("rationale", "")),
            })
        for b in (doc.get("blocked_by_rules") or []):
            out.append({
                "date": d, "symbol": b.get("symbol"), "signal": b.get("signal"),
                "score": None, "is_discovery": False,
                "reason": _veto_class(b.get("reason", "")),
            })
    return out


def _veto_class(text: str) -> str:
    t = (text or "").upper()
    for key, label in (
        ("CHASE VETO", "chase"), ("EARNINGS VETO", "earnings"),
        ("DISCOVERY QUALITY", "discovery_quality"), ("MICRO_CAP", "micro_cap"),
        ("CASH BELOW", "cash_floor"), ("VOLATILITY VETO", "volatility"),
        ("HOLIDAY_WEEK", "holiday"), ("SECTOR_CONCENTRATION", "concentration"),
        ("FALLING KNIFE", "falling_knife"), ("PRICE_FLOOR", "price_floor"),
        ("PRICE_MISMATCH", "price_mismatch"), ("48H", "cooldown"),
        ("MINIMUM HOLD", "min_hold"), ("TIER 1", "tier1_protection"),
    ):
        if key in t:
            return label
    return "other"


def enrich(trips: list, judges: dict, technicals: dict) -> list:
    """Attach the entry-day Judge score, criteria and technicals to each trip."""
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
        except (OSError, ValueError):
            state = {}
    stopped = set(state.get("stop_outs", {}).keys())

    for t in trips:
        key = (t["entry_date"], t["symbol"])
        j = judges.get(key) or {}
        tech = technicals.get(key) or {}
        t["judge_score"] = j.get("score")
        t["criteria"] = {k: (v or {}).get("score") if isinstance(v, dict) else None
                         for k, v in (j.get("criteria") or {}).items()}
        t["source"] = tech.get("source", "unknown")
        t["is_discovery"] = t["source"] not in ("watchlist", "unknown")
        for f in ("rsi", "atr_pct", "entry_score", "entry_quality", "bb_pct",
                  "volume_ratio", "consec_down_days", "signal"):
            t[f] = tech.get(f)
        # Approximate exit classification. A stop-out is recorded in
        # agent-state; otherwise infer from how close P&L sits to the tier stop.
        tier_stop = {"TIER1": -20.0, "TIER2": -12.0, "TIER3": -10.0,
                     "INVERSE": -5.0}[t["tier"]]
        t["stopped_out"] = (t["symbol"] in stopped
                            or t["pnl_pct"] <= tier_stop + 1.5)
    return trips


# ── statistics helpers ────────────────────────────────────────────────────────
def confidence(n: int) -> str:
    if n >= MIN_N_ACTIONABLE:
        return "actionable"
    if n >= MIN_N_SUGGESTIVE:
        return "suggestive"
    return "anecdote"


def summarize(trips: list) -> dict:
    if not trips:
        return {"n": 0, "win_rate": None, "expectancy": None,
                "profit_factor": None, "net_dollar": 0.0,
                "confidence": "anecdote"}
    pnls = [t["pnl_pct"] for t in trips]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_l = abs(sum(losses))
    pf = (sum(wins) / gross_l) if gross_l else (float("inf") if wins else 0.0)
    return {
        "n": len(trips),
        "win_rate": round(len(wins) / len(pnls) * 100, 1),
        "expectancy": round(mean(pnls), 2),
        "profit_factor": (None if pf == float("inf") else round(pf, 2)),
        "net_dollar": round(sum(t["pnl_dollar"] for t in trips), 2),
        "avg_hold_days": round(mean([t["hold_days"] for t in trips]), 1),
        "confidence": confidence(len(trips)),
    }


def pearson(xs, ys):
    """Correlation without a numpy dependency. None when undefined."""
    pairs = [(x, y) for x, y in zip(xs, ys)
             if isinstance(x, (int, float)) and isinstance(y, (int, float))]
    n = len(pairs)
    if n < 3:
        return None, n
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    mx, my = mean(xs2), mean(ys2)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = sum((x - mx) ** 2 for x in xs2) ** 0.5
    dy = sum((y - my) ** 2 for y in ys2) ** 0.5
    if dx == 0 or dy == 0:
        return None, n
    return round(num / (dx * dy), 3), n


def group(trips, keyfn) -> dict:
    buckets = defaultdict(list)
    for t in trips:
        k = keyfn(t)
        if k is not None:
            buckets[str(k)].append(t)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


# ── the analyses ──────────────────────────────────────────────────────────────
def analyse_source(trips):
    """Are auto-discovered names actually losing money against the watchlist?"""
    return group(trips, lambda t: "discovery" if t["is_discovery"] else "watchlist")


def analyse_tier_stops(trips):
    """
    Stop effectiveness per tier. `stopped_share` above ~0.4 with a positive
    expectancy on the survivors suggests the stop is too tight for that tier.
    """
    out = {}
    for tier, group_trips in _by(trips, lambda t: t["tier"]).items():
        stopped = [t for t in group_trips if t["stopped_out"]]
        survived = [t for t in group_trips if not t["stopped_out"]]
        out[tier] = {
            "all": summarize(group_trips),
            "stopped_out": summarize(stopped),
            "not_stopped": summarize(survived),
            "stopped_share": (round(len(stopped) / len(group_trips), 2)
                              if group_trips else None),
        }
    return out


def analyse_hold_time(trips):
    """Are winners cut short? Compare outcome across hold-length buckets."""
    def bucket(t):
        d = t["hold_days"]
        if d <= 1:
            return "0-1d"
        if d <= 5:
            return "2-5d"
        if d <= 15:
            return "6-15d"
        if d <= 30:
            return "16-30d"
        return "30d+"
    by_bucket = group(trips, bucket)
    winners = [t for t in trips if t["pnl_pct"] > 0]
    losers = [t for t in trips if t["pnl_pct"] < 0]
    return {
        "by_bucket": by_bucket,
        "avg_hold_winners": round(mean([t["hold_days"] for t in winners]), 1) if winners else None,
        "avg_hold_losers": round(mean([t["hold_days"] for t in losers]), 1) if losers else None,
        "note": ("Winners held markedly shorter than losers is the classic "
                 "cut-flowers-water-weeds pattern — take profit later, cut "
                 "losers sooner."),
    }


def analyse_judge_calibration(trips):
    """Does a higher pre-trade score actually predict a better realized result?"""
    r, n = pearson([t.get("judge_score") for t in trips],
                   [t["pnl_pct"] for t in trips])
    def bucket(t):
        s = t.get("judge_score")
        if not isinstance(s, (int, float)):
            return None
        if s >= 9:
            return "9.0+"
        if s >= 8:
            return "8.0-8.9"
        if s >= 7:
            return "7.0-7.9"
        return "<7.0"
    return {"correlation": r, "n": n, "by_score_band": group(trips, bucket),
            "note": ("A correlation near zero means the Judge score carries no "
                     "predictive power over outcomes and the weights are "
                     "decorative.")}


def analyse_criteria(trips):
    """Which individual criteria correlate with realized P&L."""
    names = set()
    for t in trips:
        names.update(k for k, v in (t.get("criteria") or {}).items()
                     if isinstance(v, (int, float)))
    out = {}
    for name in sorted(names):
        r, n = pearson([(t.get("criteria") or {}).get(name) for t in trips],
                       [t["pnl_pct"] for t in trips])
        if r is not None:
            out[name] = {"correlation": r, "n": n, "confidence": confidence(n)}
    return out


def analyse_entry_conditions(trips):
    """Which entry-day technical conditions preceded good outcomes."""
    def rsi_band(t):
        v = t.get("rsi")
        if not isinstance(v, (int, float)):
            return None
        if v < 30:
            return "oversold <30"
        if v < 45:
            return "45 and under"
        if v < 60:
            return "45-60"
        return "overbought 60+"

    def atr_band(t):
        v = t.get("atr_pct")
        if not isinstance(v, (int, float)):
            return None
        return "low vol <2%" if v < 2 else ("mid 2-4%" if v < 4 else "high 4%+")

    return {
        "by_rsi": group(trips, rsi_band),
        "by_atr": group(trips, atr_band),
        "by_entry_quality": group(trips, lambda t: t.get("entry_quality")),
        "by_signal": group(trips, lambda t: t.get("signal")),
        "rsi_correlation": dict(zip(("r", "n"),
                                    pearson([t.get("rsi") for t in trips],
                                            [t["pnl_pct"] for t in trips]))),
        "entry_score_correlation": dict(zip(("r", "n"),
                                            pearson([t.get("entry_score") for t in trips],
                                                    [t["pnl_pct"] for t in trips]))),
    }


def analyse_regime(trips, regime):
    return group(trips, lambda t: regime.get(t["entry_date"], "UNKNOWN"))


def analyse_veto_cost(vetoes, prices, horizons=(5, 10, 20)):
    """
    What did the trades we rejected go on to do?

    With almost every candidate vetoed, this is the only evidence about whether
    the gates are calibrated or simply too tight. A veto class whose rejected
    names reliably rose was costing money; one whose names fell was earning its
    keep.
    """
    by_reason = defaultdict(lambda: defaultdict(list))
    for v in vetoes:
        sym, d = v.get("symbol"), v.get("date")
        series = prices.get(sym) or {}
        if not series or d not in series:
            continue
        base = series[d]
        if base <= 0:
            continue
        for h in horizons:
            fwd_date = _nearest_date(series, d, h)
            if fwd_date is None:
                continue
            by_reason[v["reason"]][h].append(
                ((series[fwd_date] / base - 1) * 100, _gap(d, fwd_date)))

    out = {}
    for reason, horizon_map in by_reason.items():
        entry = {}
        for h, samples in sorted(horizon_map.items()):
            rets = [r for r, _ in samples]
            entry[f"{h}d"] = {
                "n": len(rets),
                "avg_forward_return": round(mean(rets), 2),
                "share_positive": round(sum(1 for r in rets if r > 0) / len(rets), 2),
                "avg_actual_gap_days": round(mean([g for _, g in samples]), 1),
                "confidence": confidence(len(rets)),
            }
        out[reason] = entry
    return out


def _gap(a: str, b: str) -> int:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return 0


def _nearest_date(series: dict, start: str, horizon_days: int,
                  tolerance_ratio: float = 0.6):
    """
    The observation closest to start + horizon_days.

    Research snapshots are not a dense daily series — they only exist for days
    the pipeline ran and committed, and those files were gitignored until
    recently. Indexing "the 5th later observation" silently compared a 5-day
    horizon against a 50-day gap. Match on calendar distance instead, and reject
    anything outside a proportional tolerance so a label always means roughly
    what it says.
    """
    later = [d for d in series if d > start]
    if not later:
        return None
    tol = max(2, int(horizon_days * tolerance_ratio))
    best, best_err = None, None
    for d in later:
        err = abs(_gap(start, d) - horizon_days)
        if best_err is None or err < best_err:
            best, best_err = d, err
    return best if best_err is not None and best_err <= tol else None


def _by(trips, keyfn):
    buckets = defaultdict(list)
    for t in trips:
        k = keyfn(t)
        if k is not None:
            buckets[str(k)].append(t)
    return dict(sorted(buckets.items()))


# ── report ────────────────────────────────────────────────────────────────────
def build(round_trips=None) -> dict:
    trips = round_trips if round_trips is not None else load_round_trips()
    judges = load_judge_history()
    technicals, prices, regime = load_research_history()
    trips = enrich(trips, judges, technicals)
    vetoes = load_vetoes()

    return {
        "generated": date.today().isoformat(),
        "overall": summarize(trips),
        "by_source": analyse_source(trips),
        "tier_stops": analyse_tier_stops(trips),
        "hold_time": analyse_hold_time(trips),
        "judge_calibration": analyse_judge_calibration(trips),
        "criteria_power": analyse_criteria(trips),
        "entry_conditions": analyse_entry_conditions(trips),
        "by_regime": analyse_regime(trips, regime),
        "veto_cost": analyse_veto_cost(vetoes, prices),
        "counts": {"round_trips": len(trips), "vetoes": len(vetoes),
                   "judge_days": len({d for d, _ in judges}),
                   "priced_symbols": len(prices)},
    }


def _row(label, s, width=26):
    if not s or not s.get("n"):
        return f"  {label:<{width}} —"
    pf = "∞" if s.get("profit_factor") is None else f"{s['profit_factor']:.2f}"
    return (f"  {label:<{width}} n={s['n']:<4} win {s['win_rate']:>5.1f}%  "
            f"exp {s['expectancy']:>+6.2f}%  pf {pf:>5}  "
            f"${s['net_dollar']:>+10,.0f}  [{s['confidence']}]")


def render(a: dict) -> str:
    L = [f"# Trade Learnings — {a['generated']}", ""]
    c = a["counts"]
    L.append(f"Round trips: {c['round_trips']}  ·  vetoes analysed: {c['vetoes']}"
             f"  ·  judge days: {c['judge_days']}")
    L.append("")
    L.append("## Overall")
    L.append(_row("all closed trades", a["overall"]))

    L += ["", "## Discovery vs watchlist"]
    for k, v in a["by_source"].items():
        L.append(_row(k, v))

    L += ["", "## Stop effectiveness by tier"]
    for tier, d in a["tier_stops"].items():
        share = d["stopped_share"]
        L.append(f"  {tier}  stopped-out share: "
                 f"{'—' if share is None else f'{share:.0%}'}")
        L.append(_row("  ├─ stopped out", d["stopped_out"]))
        L.append(_row("  └─ ran to a real exit", d["not_stopped"]))

    ht = a["hold_time"]
    L += ["", "## Hold time vs outcome"]
    for k, v in ht["by_bucket"].items():
        L.append(_row(k, v))
    _w, _l = ht["avg_hold_winners"], ht["avg_hold_losers"]
    L.append(f"  avg hold — winners: {'—' if _w is None else f'{_w}d'}   "
             f"losers: {'—' if _l is None else f'{_l}d'}")
    if _w is not None and _l is not None and _w < _l:
        L.append(f"  ⚠  winners held {_l - _w:.1f}d SHORTER than losers — "
                 f"{ht['note']}")

    jc = a["judge_calibration"]
    L += ["", "## Does the Judge score predict the outcome?",
          f"  correlation {jc['correlation']} on n={jc['n']}"]
    for k, v in jc["by_score_band"].items():
        L.append(_row(k, v))

    if a["criteria_power"]:
        L += ["", "## Predictive power per criterion"]
        for k, v in sorted(a["criteria_power"].items(),
                           key=lambda kv: -abs(kv[1]["correlation"])):
            L.append(f"  {k:<26} r={v['correlation']:>+6.3f}  n={v['n']:<4} "
                     f"[{v['confidence']}]")

    ec = a["entry_conditions"]
    L += ["", "## Entry conditions"]
    for title, key in (("by RSI band", "by_rsi"), ("by ATR band", "by_atr"),
                       ("by entry quality", "by_entry_quality"),
                       ("by signal", "by_signal")):
        if ec[key]:
            L.append(f"  {title}:")
            for k, v in ec[key].items():
                L.append(_row("  " + k, v))

    L += ["", "## By macro regime"]
    for k, v in a["by_regime"].items():
        L.append(_row(k, v))

    L += ["", "## What the vetoed trades did next",
          "  (positive forward return = the veto cost money;"
          " negative = it earned its keep)"]
    if not a["veto_cost"]:
        L.append("  — no priced veto history yet")
    for reason, hz in sorted(a["veto_cost"].items()):
        parts = [f"{h} {d['avg_forward_return']:+.1f}% "
                 f"({d['share_positive']:.0%} up, n={d['n']})"
                 for h, d in hz.items()]
        L.append(f"  {reason:<20} " + "   ".join(parts))

    L += ["", "---",
          f"Confidence: anecdote <{MIN_N_SUGGESTIVE}, "
          f"suggestive <{MIN_N_ACTIONABLE}, actionable {MIN_N_ACTIONABLE}+. "
          "Nothing here changes a parameter; judge.py's LEARNING_TIERS decides "
          "how much may affect scoring."]
    return "\n".join(L)


def main():
    a = build()
    if len(sys.argv) > 1 and sys.argv[1] == "json":
        print(json.dumps(a, indent=2, default=str))
        return
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(a, f, indent=2, default=str)
    print(render(a))
    print(f"\n[analytics] machine-readable copy: {OUT_JSON}")


if __name__ == "__main__":
    main()
