#!/usr/bin/env python3
"""
symbol_stats.py — per-symbol track record for the equities Judge.

Reads closed round trips (round-trips.csv) and the Judge-vs-Reviewer calibration
log, and returns one record per symbol: trades, win rate, average P&L, and the
Judge's average pre-trade score for winners vs losers.

Measurement rules mirror reviewer.py: quarantined symbols, options contracts,
and data faults (single-trade P&L beyond +/-1000%, or the -100% placeholder the
calibration log carries for unpriced fills) are excluded, never counted as a
win or a loss.

Usage:
  python3 symbol_stats.py            # table for every symbol with a record
  python3 symbol_stats.py AAPL MSFT  # just these symbols
"""

import csv
import os
import sys
from collections import defaultdict
from statistics import mean

from reviewer import QUARANTINED_SYMBOLS, is_options_symbol

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "paper")

DATA_SUSPECT_PCT = 1000.0
PLACEHOLDER_LOSS_PCT = -99.9


def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _excluded(symbol):
    return (not symbol or symbol in QUARANTINED_SYMBOLS or is_options_symbol(symbol))


def load_stats(logs_dir: str = JOURNAL_DIR) -> dict:
    """Return {symbol: record}. A symbol with no valid closed trade or calibration
    row is simply absent -- callers treat absence as "no record"."""
    trades = defaultdict(list)
    for r in _read_csv(os.path.join(logs_dir, "round-trips.csv")):
        sym, pct, usd = r.get("symbol"), _num(r.get("pnl_pct")), _num(r.get("pnl_dollar"))
        if _excluded(sym) or pct is None or abs(pct) > DATA_SUSPECT_PCT:
            continue
        trades[sym].append((pct, usd or 0.0, r.get("exit_date") or ""))

    calib = defaultdict(list)
    seen = set()
    for r in _read_csv(os.path.join(logs_dir, "calibration-log.csv")):
        sym = r.get("symbol")
        pct, score = _num(r.get("pnl_pct")), _num(r.get("judge_score"))
        if _excluded(sym) or pct is None or score is None:
            continue
        if pct <= PLACEHOLDER_LOSS_PCT or abs(pct) > DATA_SUSPECT_PCT:
            continue
        key = (r.get("date"), sym, r.get("judge_score"), r.get("reviewer_score"), r.get("pnl_pct"))
        if key in seen:
            continue
        seen.add(key)
        calib[sym].append((score, pct))

    stats = {}
    for sym in set(trades) | set(calib):
        t, c = trades.get(sym, []), calib.get(sym, [])
        pcts = [p for p, _, _ in t]
        win_scores  = [s for s, p in c if p > 0]
        loss_scores = [s for s, p in c if p <= 0]
        stats[sym] = {
            "trades":          len(t),
            "wins":            sum(p > 0 for p in pcts),
            "win_rate":        (sum(p > 0 for p in pcts) / len(pcts)) if pcts else None,
            "avg_pnl_pct":     round(mean(pcts), 2) if pcts else None,
            "total_pnl_usd":   round(sum(u for _, u, _ in t), 2),
            "last_exit":       max((d for _, _, d in t), default=""),
            "judge_n":         len(c),
            "avg_judge_score": round(mean(s for s, _ in c), 2) if c else None,
            "avg_judge_winners": round(mean(win_scores), 2) if win_scores else None,
            "avg_judge_losers":  round(mean(loss_scores), 2) if loss_scores else None,
        }
    return stats


def _fmt(v, spec):
    return format(v, spec) if v is not None else "-"


if __name__ == "__main__":
    stats = load_stats()
    wanted = [s.upper() for s in sys.argv[1:]] or sorted(stats, key=lambda s: -stats[s]["trades"])
    print(f"{'sym':6}{'trades':>7}{'win%':>6}{'avg%':>8}{'total$':>10}{'judge_n':>8}{'avgJ':>6}{'J(win)':>8}{'J(loss)':>8}")
    for s in wanted:
        r = stats.get(s)
        if not r:
            print(f"{s:6}{'no record':>7}")
            continue
        wr = None if r["win_rate"] is None else r["win_rate"] * 100
        print(f"{s:6}{r['trades']:>7}{_fmt(wr, '.0f'):>6}{_fmt(r['avg_pnl_pct'], '.2f'):>8}"
              f"{r['total_pnl_usd']:>10,.0f}{r['judge_n']:>8}{_fmt(r['avg_judge_score'], '.2f'):>6}"
              f"{_fmt(r['avg_judge_winners'], '.2f'):>8}{_fmt(r['avg_judge_losers'], '.2f'):>8}")
