#!/usr/bin/env python3
"""
reviewer.py — Post-trade scoring agent. Runs at 4:30 PM after market close.
Scores each trade placed that day against what actually happened.
Builds the Judge calibration dataset over time.

Usage:
  python reviewer.py eod                  # Score all of today's trades
  python reviewer.py read 2026-06-15      # Read a past review
  python reviewer.py calibration          # Compare Judge vs Reviewer scores (last 30 days)
"""

import os
import sys
import json
import csv
import requests
from datetime import date, datetime, timedelta
from statistics import mean

# ── Load .env ────────────────────────────────────────────────────────────────
# setup.sh writes a .env file, but nothing used to read it, so filling in keys
# there had no effect. Environment variables already set (GitHub Secrets, MCP
# config, an exporting shell) always win — load_dotenv does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars may be supplied by the shell

# A single-trade P&L beyond this magnitude (in percent) is treated as a data
# fault rather than a result. Real equity trades do not move 1000% intraday;
# unadjusted reverse splits do.
IMPLAUSIBLE_PNL_PCT = 1000.0

# ── Phase 2B baseline (reset 2026-08-20) ──────────────────────────────────────
# Everything before this date was scored on the pre-clamp Judge scale, which
# summed criteria past their own weights and inflated every total by up to 2.0
# points. Those scores are not comparable to post-fix ones, so the graduation
# decision counts only trades from the baseline forward.
PHASE2B_BASELINE_DATE = "2026-08-20"
PHASE2B_DECISION_DATE = "2026-09-03"

GRADUATION_MIN_OCCURRENCES   = 15
GRADUATION_MIN_WIN_RATE      = 55.0
GRADUATION_MIN_JUDGE_AVG     = 7.0
GRADUATION_MIN_REVIEWER_AVG  = 6.0

# ── Auth ─────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL      = "https://data.alpaca.markets"

ROOT         = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR  = os.path.join(ROOT, "logs", "paper")
CALIBRATION_LOG = os.path.join(JOURNAL_DIR, "calibration-log.csv")

def _headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

def _check_auth():
    if not ALPACA_KEY or not ALPACA_SECRET:
        print(json.dumps({"error": "Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY env vars"}))
        sys.exit(1)

# ── Data Fetchers ─────────────────────────────────────────────────────────────

def get_orders_today():
    today = date.today().isoformat()
    r = requests.get(f"{BASE_URL}/v2/orders",
                     headers=_headers(),
                     params={"status": "all", "after": today, "limit": 100})
    r.raise_for_status()
    return r.json()

def get_bars_today(symbol):
    """Get today's intraday bars for post-trade movement scoring."""
    r = requests.get(f"{DATA_URL}/v2/stocks/{symbol}/bars",
                     headers=_headers(),
                     params={"timeframe": "1Hour", "limit": 8, "sort": "asc"})
    r.raise_for_status()
    return r.json().get("bars", [])

def get_latest_price(symbol):
    r = requests.get(f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest",
                     headers=_headers())
    r.raise_for_status()
    data = r.json()
    return float(data["quote"].get("ap", data["quote"].get("bp", 0)))

def get_positions():
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers())
    r.raise_for_status()
    return r.json()

# ── Post-Trade Scoring ────────────────────────────────────────────────────────

def score_trade(order: dict, judge_score: float | None) -> dict:
    """
    Score a filled trade post-market on 4 dimensions.
    Returns a score 1–10 and a verdict.
    """
    symbol      = order.get("symbol", "")
    fill_price  = float(order.get("filled_avg_price") or 0)
    side        = order.get("side", "buy")
    qty         = int(float(order.get("filled_qty", 0)))
    fill_time   = order.get("filled_at", "")

    if fill_price == 0 or qty == 0:
        return {"symbol": symbol, "verdict": "NO_FILL", "score": None, "note": "Order not filled"}

    # Get end-of-day price
    try:
        eod_price = get_latest_price(symbol)
    except Exception as e:
        eod_price = fill_price  # fallback: assume flat
        note_price = f"Could not fetch EOD price: {e}"
    else:
        note_price = f"EOD price: ${eod_price:.2f}"

    # Movement from fill to close
    if side == "buy":
        pnl_pct = (eod_price - fill_price) / fill_price * 100
    else:
        pnl_pct = (fill_price - eod_price) / fill_price * 100

    pnl_dollar = (eod_price - fill_price) * qty if side == "buy" else (fill_price - eod_price) * qty

    # ── Score Components (each 0–2.5, total 10) ──────────────────────────────

    # 1. Fill quality (did it fill? at what slippage?)
    fill_score = 2.5  # filled = max score for this component

    # 2. Intraday direction (did price move in our favor?)
    if pnl_pct > 1.0:
        direction_score = 2.5
        direction_note  = f"Strong follow-through +{pnl_pct:.2f}% intraday"
    elif pnl_pct > 0:
        direction_score = 1.5
        direction_note  = f"Mild positive move +{pnl_pct:.2f}% intraday"
    elif pnl_pct > -1.0:
        direction_score = 1.0
        direction_note  = f"Flat/minor decline {pnl_pct:.2f}% intraday"
    else:
        direction_score = 0.0
        direction_note  = f"Adverse move {pnl_pct:.2f}% intraday — thesis not confirmed"

    # 3. Signal confirmation (did the original signal hold?)
    try:
        bars = get_bars_today(symbol)
        closes = [b["c"] for b in bars]
        if len(closes) >= 2:
            # Check if price stayed above entry for majority of day
            above_entry = sum(1 for c in closes if c > fill_price)
            if above_entry >= len(closes) * 0.7:
                signal_score = 2.5
                signal_note  = f"Price held above entry {above_entry}/{len(closes)} hours"
            elif above_entry >= len(closes) * 0.4:
                signal_score = 1.25
                signal_note  = f"Price mixed — above entry {above_entry}/{len(closes)} hours"
            else:
                signal_score = 0.0
                signal_note  = f"Price mostly below entry {len(closes)-above_entry}/{len(closes)} hours"
        else:
            signal_score = 1.25
            signal_note  = "Insufficient intraday data"
    except Exception:
        signal_score = 1.25
        signal_note  = "Could not fetch intraday bars"

    # 4. Judge calibration (did Judge score predict outcome well?)
    if judge_score is None:
        calibration_score = 1.25
        calibration_note  = "No Judge score on record for this trade"
    else:
        # If judge said >=7 and trade was positive: good calibration
        if judge_score >= 7 and pnl_pct > 0:
            calibration_score = 2.5
            calibration_note  = f"Judge score {judge_score:.1f} correctly predicted positive outcome"
        elif judge_score >= 7 and pnl_pct <= 0:
            calibration_score = 0.5
            calibration_note  = f"Judge score {judge_score:.1f} overconfident — negative outcome"
        elif judge_score < 7 and pnl_pct > 0:
            calibration_score = 1.5
            calibration_note  = f"Judge score {judge_score:.1f} underconfident — positive outcome anyway"
        else:
            calibration_score = 1.25
            calibration_note  = f"Judge score {judge_score:.1f} — both predicted and actual were weak"

    total = round(fill_score + direction_score + signal_score + calibration_score, 2)

    if total >= 8:
        verdict = "GOOD_TRADE"
    elif total >= 5:
        verdict = "NEUTRAL"
    else:
        verdict = "POOR_TRADE"

    # A P&L this large is a data fault, not a trade outcome. LBGJ was restated
    # from $0.02 to $3.79 by an unadjusted reverse split and would otherwise be
    # logged as a +10,941% GOOD_TRADE — on its own enough to clear the 55%
    # graduation win rate and unlock real money on fictional performance.
    # DATA_SUSPECT keeps it in the record but out of the win/loss counts.
    if abs(pnl_pct) >= IMPLAUSIBLE_PNL_PCT:
        verdict = "DATA_SUSPECT"

    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "fill_price": fill_price,
        "eod_price": round(eod_price, 2),
        "pnl_pct": round(pnl_pct, 2),
        "pnl_dollar": round(pnl_dollar, 2),
        "score": total,
        "verdict": verdict,
        "judge_score": judge_score,
        "score_breakdown": {
            "fill_quality":   f"{fill_score}/2.5",
            "direction":      f"{direction_score}/2.5 — {direction_note}",
            "signal_confirm": f"{signal_score}/2.5 — {signal_note}",
            "judge_calib":    f"{calibration_score}/2.5 — {calibration_note}",
        },
        "price_note": note_price if 'note_price' in locals() else "",
    }

def format_review_report(result: dict) -> str:
    """Format a single trade's review as markdown."""
    verdict_icon = {"GOOD_TRADE": "✅", "NEUTRAL": "➖", "POOR_TRADE": "❌", "NO_FILL": "⏭️"}
    icon  = verdict_icon.get(result.get("verdict", ""), "?")
    sym   = result.get("symbol", "")
    score = result.get("score")

    lines = [f"## {sym} — Reviewer Post-Trade Assessment {icon}"]
    if result.get("verdict") == "NO_FILL":
        lines.append("- Order did not fill")
        return "\n".join(lines)

    lines.append(f"- Entry: ${result.get('fill_price', 0):.2f} | EOD: ${result.get('eod_price', 0):.2f}")
    lines.append(f"- Intraday P&L: {result.get('pnl_pct', 0):+.2f}% (${result.get('pnl_dollar', 0):+.2f})")

    judge_s = result.get("judge_score")
    if judge_s:
        lines.append(f"- Judge pre-trade score: {judge_s:.1f}/10")

    lines.append("\n**Score Breakdown:**")
    for k, v in result.get("score_breakdown", {}).items():
        lines.append(f"- {k.replace('_', ' ').title()}: {v}")

    lines.append(f"\n**Post-Trade Score: {score} / 10**")
    lines.append(f"**Verdict: {result.get('verdict')}**")
    return "\n".join(lines)

# ── Calibration Report ────────────────────────────────────────────────────────

def append_calibration_log(trade_results: list, today: str):
    """Append today's judge vs reviewer deltas to the calibration CSV."""
    write_header = not os.path.exists(CALIBRATION_LOG)
    with open(CALIBRATION_LOG, "a", newline="") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["date", "symbol", "side", "judge_score",
                             "reviewer_score", "pnl_pct", "verdict", "delta"])
        for r in trade_results:
            if r.get("verdict") == "NO_FILL":
                continue
            j = r.get("judge_score")
            rv = r.get("score")
            delta = round(rv - j, 2) if j is not None and rv is not None else ""
            writer.writerow([
                today, r.get("symbol"), r.get("side"),
                j, rv, r.get("pnl_pct"), r.get("verdict"), delta
            ])

def run_graduation_decision(since: str = PHASE2B_BASELINE_DATE) -> str:
    """
    Evaluate the Paper → Robinhood graduation criteria against trades logged on
    or after `since`, and render an explicit GRADUATE / DO NOT GRADUATE verdict.

    Only counts trades from the baseline forward. Pre-baseline scores came from
    the un-clamped Judge and are not comparable. DATA_SUSPECT rows (implausible
    P&L from unadjusted corporate actions) are excluded entirely — a broker data
    fault must never satisfy a real-money gate.
    """
    path = os.path.join(JOURNAL_DIR, "calibration-log.csv")
    if not os.path.exists(path):
        return "# Graduation Decision\n\nNo calibration log found."

    with open(path) as f:
        all_rows = list(csv.DictReader(f))

    rows    = [r for r in all_rows if r.get("date", "") >= since]
    suspect = [r for r in rows if r.get("verdict") == "DATA_SUSPECT"]
    scored  = [r for r in rows if r.get("verdict") not in ("DATA_SUSPECT", "NO_FILL")]

    wins  = [r for r in scored if r["verdict"] == "GOOD_TRADE"]
    js    = [float(r["judge_score"])    for r in scored if (r.get("judge_score") or "").strip()]
    rs    = [float(r["reviewer_score"]) for r in scored if (r.get("reviewer_score") or "").strip()]

    n         = len(scored)
    win_rate  = (len(wins) / n * 100) if n else 0.0
    judge_avg = mean(js) if js else 0.0
    rev_avg   = mean(rs) if rs else 0.0

    checks = [
        ("Occurrences",        n,         GRADUATION_MIN_OCCURRENCES,  f"{n}",              n >= GRADUATION_MIN_OCCURRENCES),
        ("Win rate",           win_rate,  GRADUATION_MIN_WIN_RATE,     f"{win_rate:.1f}%",  win_rate >= GRADUATION_MIN_WIN_RATE),
        ("Avg Judge score",    judge_avg, GRADUATION_MIN_JUDGE_AVG,    f"{judge_avg:.2f}",  bool(js) and judge_avg >= GRADUATION_MIN_JUDGE_AVG),
        ("Avg Reviewer score", rev_avg,   GRADUATION_MIN_REVIEWER_AVG, f"{rev_avg:.2f}",    bool(rs) and rev_avg >= GRADUATION_MIN_REVIEWER_AVG),
    ]
    passed = all(ok for *_, ok in checks)

    lines = [
        f"# Graduation Decision — {date.today().isoformat()}",
        "",
        f"**Baseline**: {since} (Phase 2B)",
        f"**Trades counted**: {n}"
        + (f"  ·  {len(suspect)} excluded as DATA_SUSPECT" if suspect else ""),
        "",
        "## Criteria",
        "",
        "| Criterion | Required | Actual | |",
        "|---|---|---|---|",
    ]
    for name, _, req, actual, ok in checks:
        req_s = f"≥{req:g}" + ("%" if name == "Win rate" else "")
        lines.append(f"| {name} | {req_s} | {actual} | {'✅' if ok else '❌'} |")

    lines += ["", "## Verdict", ""]
    if passed:
        lines += [
            "**ALL AUTOMATED CRITERIA MET.**",
            "",
            "This is *not* an approval. CLAUDE.md requires manual approval by Alex —"
            " no auto-graduation ever. Review the underlying trades before moving"
            " any real money to Robinhood.",
        ]
    else:
        failed = [name for name, *_ , ok in checks if not ok]
        lines += [
            "**DO NOT GRADUATE.** Phase 3 stays locked.",
            "",
            "Failing: " + ", ".join(failed) + ".",
        ]
        if n < GRADUATION_MIN_OCCURRENCES:
            lines.append(
                f"\nOnly {n} qualifying trade(s) since the baseline. Note that "
                f"reviewer.py scores buys only, so a period of net selling "
                f"produces no calibration rows at all."
            )

    if suspect:
        lines += [
            "",
            "## Excluded as DATA_SUSPECT",
            "",
            *(f"- {r['date']} {r['symbol']}: P&L {r['pnl_pct']}% — implausible, "
              f"likely an unadjusted corporate action" for r in suspect),
        ]

    return "\n".join(lines)


def run_calibration_report(days: int = 30) -> str:
    """Read calibration log and surface systematic biases."""
    if not os.path.exists(CALIBRATION_LOG):
        return "No calibration data yet. Run reviewer.py eod daily to build it."

    rows = []
    with open(CALIBRATION_LOG) as f:
        reader = csv.DictReader(f)
        rows   = list(reader)

    # Filter to last N days
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    rows   = [r for r in rows if r["date"] >= cutoff and r["delta"] != ""]

    if not rows:
        return f"No calibration data in the last {days} days."

    # delta is blank whenever the Judge score was unavailable; float("") raises,
    # which used to crash this whole report.
    deltas    = [float(r["delta"]) for r in rows if (r.get("delta") or "").strip()]
    avg_delta = mean(deltas) if deltas else 0.0

    # Data-suspect rows are excluded from the win rate entirely — they are
    # broker/corporate-action faults, not trade outcomes, and leaving them in
    # either direction distorts the graduation decision.
    suspect  = [r for r in rows if r["verdict"] == "DATA_SUSPECT"]
    scored   = [r for r in rows if r["verdict"] != "DATA_SUSPECT"]
    wins     = [r for r in scored if r["verdict"] == "GOOD_TRADE"]
    losses   = [r for r in scored if r["verdict"] == "POOR_TRADE"]
    win_rate = (len(wins) / len(scored) * 100) if scored else 0.0

    lines = [
        f"# Calibration Report — Last {days} Days",
        f"**Trades analyzed**: {len(scored)}"
        + (f" ({len(rows)} logged, {len(suspect)} excluded as DATA_SUSPECT)"
           if suspect else ""),
        f"**Win rate**: {win_rate:.1f}% ({len(wins)} good, {len(losses)} poor)"
        f" — graduation requires ≥55%",
        f"**Avg Judge→Reviewer delta**: {avg_delta:+.2f}"
        + ("" if deltas else " (no Judge scores on record)"),
        "",
        "## Interpretation",
    ]

    if suspect:
        lines.append(
            f"⚠️ **{len(suspect)} trade(s) excluded as DATA_SUSPECT**: "
            + ", ".join(sorted({r['symbol'] for r in suspect}))
            + ". Implausible P&L indicates an unadjusted corporate action rather "
              "than a real outcome. Reconcile with the broker before treating "
              "these as performance."
        )

    if avg_delta > 1.0:
        lines.append(f"Judge is **underconfident** by avg {avg_delta:.2f} pts — consider raising score weights")
    elif avg_delta < -1.0:
        lines.append(f"Judge is **overconfident** by avg {abs(avg_delta):.2f} pts — tighten scoring criteria")
    else:
        lines.append("Judge calibration is **well-tuned** (delta within ±1.0)")

    # Per-symbol breakdown
    syms = {}
    for r in rows:
        s = r["symbol"]
        syms.setdefault(s, []).append(float(r["delta"]))

    lines.append("\n## Per-Symbol Bias")
    for sym, deltas_sym in sorted(syms.items()):
        avg = mean(deltas_sym)
        lines.append(f"- **{sym}**: avg delta {avg:+.2f} over {len(deltas_sym)} trades")

    return "\n".join(lines)

# ── EOD Session ───────────────────────────────────────────────────────────────

def run_eod_review() -> dict:
    today     = date.today().isoformat()
    orders    = get_orders_today()

    # Load judge scores for today
    judge_path = os.path.join(JOURNAL_DIR, f"{today}-judge.json")
    judge_data = {}
    if os.path.exists(judge_path):
        with open(judge_path) as f:
            raw = json.load(f)
            for sym, scored in raw.get("scores", {}).items():
                judge_data[sym] = scored.get("score")

    results  = {"date": today, "timestamp": datetime.now().isoformat(), "trades": []}
    filled   = [o for o in orders if o.get("status") in ("filled", "partially_filled") and o.get("side") == "buy"]

    for order in filled:
        sym         = order.get("symbol", "")
        judge_score = judge_data.get(sym)
        review      = score_trade(order, judge_score)
        results["trades"].append(review)

    # Save JSON
    out_path = os.path.join(JOURNAL_DIR, f"{today}-review.json")
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    # Write markdown
    md_lines = [f"# Reviewer Report — {today}\n",
                f"**Trades reviewed**: {len(results['trades'])}\n",
                "---\n"]
    for trade in results["trades"]:
        md_lines.append(format_review_report(trade))
        md_lines.append("\n---\n")

    md_path = os.path.join(JOURNAL_DIR, f"{today}-review.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    # Update calibration log
    append_calibration_log(results["trades"], today)

    results["report_path"] = md_path
    return results

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    _check_auth()
    action = sys.argv[1] if len(sys.argv) > 1 else "eod"
    arg2   = sys.argv[2] if len(sys.argv) > 2 else None

    if action == "eod":
        print(json.dumps(run_eod_review(), indent=2))

    elif action == "calibration":
        days = int(arg2) if arg2 else 30
        print(run_calibration_report(days))

    elif action == "graduation":
        since = arg2 or PHASE2B_BASELINE_DATE
        print(run_graduation_decision(since))

    elif action == "read" and arg2:
        path = os.path.join(JOURNAL_DIR, f"{arg2}-review.md")
        if os.path.exists(path):
            with open(path) as f:
                print(f.read())
        else:
            print(f"No review for {arg2}")

    else:
        print(json.dumps({"error": f"Unknown action: {action}. Use: eod | "
                                   f"calibration [days] | graduation [since] | read DATE"}))
