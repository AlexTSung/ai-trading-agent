#!/usr/bin/env python3
"""
learning_agent.py — Continuous improvement agent for the trading system.

Reads calibration-log.csv + all judge/review .md files after each session,
detects recurring patterns, auto-tunes judge weight hints, and appends
dated lessons to CLAUDE.md.

Usage:
  python3 learning_agent.py eod        # Run after reviewer.py eod
  python3 learning_agent.py weekly     # Deeper weekly pattern pass
  python3 learning_agent.py report     # Print current learnings, no writes
"""

import os, sys, csv, json, re
from datetime import date, datetime
from collections import defaultdict
from pathlib import Path

ROOT      = Path(__file__).parent
LOG_DIR   = ROOT / "logs" / "paper"
CLAUDE_MD = ROOT / "CLAUDE.md"
LEARN_DIR = ROOT / "logs" / "learnings"
LEARN_DIR.mkdir(parents=True, exist_ok=True)

MONTHLY_LOG = LEARN_DIR / f"learnings-{date.today().strftime('%Y-%m')}.md"
CAL_LOG     = LOG_DIR / "calibration-log.csv"
WEIGHTS_LOG = LEARN_DIR / "judge_weight_hints.json"

MODE = sys.argv[1] if len(sys.argv) > 1 else "eod"

# ── helpers ──────────────────────────────────────────────────────────────────

def load_calibration():
    trades = []
    if not CAL_LOG.exists():
        return trades
    with open(CAL_LOG) as f:
        for row in csv.DictReader(f):
            try:
                trades.append({
                    "date":           row["date"],
                    "symbol":         row["symbol"],
                    "side":           row["side"],
                    "judge_score":    float(row["judge_score"]) if row["judge_score"] else None,
                    "reviewer_score": float(row["reviewer_score"]) if row["reviewer_score"] else None,
                    "pnl_pct":        float(row["pnl_pct"]) if row["pnl_pct"] else None,
                    "verdict":        row["verdict"],
                    "delta":          float(row["delta"]) if row["delta"] else None,
                })
            except (ValueError, KeyError):
                continue
    return trades


def load_judge_lines():
    """Pull score-component lines from all judge .md files."""
    entries = []
    for f in sorted(LOG_DIR.glob("*-judge.md")):
        text = f.read_text()
        # Each block: symbol + scores per component
        for block in re.split(r'\n## ', text):
            sym_match = re.match(r'(\w+)\s+—', block)
            if not sym_match:
                continue
            sym = sym_match.group(1)
            scores = {}
            for component in ("Technical", "News", "Macro", "Portfolio", "Volatility", "Quality"):
                m = re.search(rf'\*\*{component}\*\*\s+\(([0-9.]+)pt\)', block)
                if m:
                    scores[component.lower()] = float(m.group(1))
            verdict_m = re.search(r'\*\*Recommendation:\s*(APPROVE|HOLD|VETO)\*\*', block)
            score_m   = re.search(r'\*\*Pre-Trade Score:\s*([0-9.]+)', block)
            entries.append({
                "file":    f.name,
                "symbol":  sym,
                "scores":  scores,
                "verdict": verdict_m.group(1) if verdict_m else None,
                "total":   float(score_m.group(1)) if score_m else None,
            })
    return entries


def win_rate(trades):
    scored = [t for t in trades if t["verdict"] in ("GOOD_TRADE", "POOR_TRADE", "NEUTRAL")]
    if not scored:
        return 0, 0, 0
    wins    = sum(1 for t in scored if t["verdict"] == "GOOD_TRADE")
    losses  = sum(1 for t in scored if t["verdict"] == "POOR_TRADE")
    return wins, losses, len(scored)


# ── pattern detection ─────────────────────────────────────────────────────────

def detect_patterns(trades, judge_lines):
    patterns = []

    # 1. High judge score → poor outcome
    high_score_poor = [t for t in trades
                       if t["judge_score"] and t["judge_score"] >= 8.0
                       and t["verdict"] == "POOR_TRADE"]
    if len(high_score_poor) >= 2:
        syms = ", ".join(t["symbol"] for t in high_score_poor)
        patterns.append({
            "id":    "high_score_poor_outcome",
            "count": len(high_score_poor),
            "desc":  f"Judge score ≥8.0 but outcome POOR_TRADE ({len(high_score_poor)}x: {syms}). "
                     "Judge may be overweighting news sentiment. Consider raising APPROVE threshold to 8.0.",
            "action": "raise_approve_threshold",
        })

    # 2. Risk-off + APPROVE → bad outcome
    risk_off_trades = []
    for jl in judge_lines:
        macro_score = jl["scores"].get("macro", None)
        if macro_score is not None and macro_score == 0.0 and jl["verdict"] == "APPROVE":
            sym = jl["symbol"]
            matching = [t for t in trades if t["symbol"] == sym]
            risk_off_trades.extend(matching)
    poor_in_risk_off = [t for t in risk_off_trades if t["verdict"] == "POOR_TRADE"]
    if len(poor_in_risk_off) >= 2:
        patterns.append({
            "id":    "risk_off_approvals_lose",
            "count": len(poor_in_risk_off),
            "desc":  f"APPROVE in risk-off macro regime led to POOR_TRADE {len(poor_in_risk_off)}x. "
                     "In risk-off, require score ≥8.5 (not 7.0) or STRONG_BUY technical only.",
            "action": "tighten_risk_off_gate",
        })

    # 3. Low reviewer score despite positive P&L (judge timing issues)
    timing_miss = [t for t in trades
                   if t["pnl_pct"] and t["pnl_pct"] > 5
                   and t["reviewer_score"] and t["reviewer_score"] < 5.0]
    if len(timing_miss) >= 2:
        patterns.append({
            "id":    "good_pnl_poor_review",
            "count": len(timing_miss),
            "desc":  f"P&L >5% but reviewer scored <5.0 ({len(timing_miss)}x). "
                     "Likely entered at wrong timing — made money despite bad entry. "
                     "Entry discipline needs improvement even when outcome is positive.",
            "action": "tighten_entry_checklist",
        })

    # 4. Negative delta (judge over-confident vs reviewer)
    neg_delta = [t for t in trades if t["delta"] and t["delta"] < -3.0]
    if len(neg_delta) >= 3:
        avg_delta = sum(t["delta"] for t in neg_delta) / len(neg_delta)
        patterns.append({
            "id":    "persistent_judge_overconfidence",
            "count": len(neg_delta),
            "desc":  f"Judge consistently scores {avg_delta:.1f} pts above reviewer ({len(neg_delta)} trades). "
                     "Judge is overconfident. Recommend reducing news component weight by 10%.",
            "action": "reduce_news_weight",
        })

    # 5. Sub-$5 stocks always underperform
    penny_trades = [t for t in trades if t["symbol"] in ("LBGJ", "RKTO", "NOK", "CIFR")]
    penny_poor   = [t for t in penny_trades if t["verdict"] == "POOR_TRADE"]
    if len(penny_poor) >= 2:
        patterns.append({
            "id":    "sub5_stocks_underperform",
            "count": len(penny_poor),
            "desc":  f"Speculative/penny stocks ({', '.join(t['symbol'] for t in penny_poor)}) "
                     f"led to POOR outcomes {len(penny_poor)}x. Tier 3 sizing (10% stop) may be too loose. "
                     "Consider 7% stop or avoiding stocks with avg volume <1M.",
            "action": "tighten_tier3_stop",
        })

    # 6. Win rate summary
    wins, losses, total = win_rate(trades)
    if total >= 5:
        wr = wins / total * 100
        patterns.append({
            "id":    "win_rate_summary",
            "count": total,
            "desc":  f"Overall win rate: {wr:.0f}% ({wins}W / {losses}L / {total-wins-losses}N of {total} trades). "
                     f"{'Above 55% graduation threshold ✅' if wr >= 55 else 'Below 55% graduation threshold ❌ — not ready for real money.'}",
            "action": "graduation_check",
        })

    return patterns


# ── weight hint update ────────────────────────────────────────────────────────

def update_weight_hints(patterns):
    hints = {}
    if WEIGHTS_LOG.exists():
        hints = json.loads(WEIGHTS_LOG.read_text())

    for p in patterns:
        if p["action"] == "reduce_news_weight":
            hints["news_weight_adj"] = hints.get("news_weight_adj", 1.0) * 0.90
            hints["news_weight_adj"] = max(0.6, hints["news_weight_adj"])
        if p["action"] == "tighten_risk_off_gate":
            hints["risk_off_min_score"] = max(hints.get("risk_off_min_score", 7.0), 8.5)
        if p["action"] == "raise_approve_threshold":
            hints["approve_threshold"] = max(hints.get("approve_threshold", 7.0), 8.0)

    hints["last_updated"] = date.today().isoformat()
    hints["total_trades"]  = sum(1 for p in patterns if p["id"] == "win_rate_summary" for _ in range(p["count"]))
    WEIGHTS_LOG.write_text(json.dumps(hints, indent=2))
    return hints


# ── CLAUDE.md lesson updater ──────────────────────────────────────────────────

def update_claude_lessons(patterns, new_lessons_only=True):
    if not CLAUDE_MD.exists():
        return

    text = CLAUDE_MD.read_text()
    lessons_marker = "## Lessons Learned"
    if lessons_marker not in text:
        return

    today = date.today().isoformat()
    new_entries = []

    for p in patterns:
        if p["id"] == "win_rate_summary":
            continue
        # Only append if this lesson isn't already logged
        pattern_id = p["id"]
        if new_lessons_only and pattern_id in text:
            continue
        new_entries.append(f"- [{today}] **{pattern_id}** (seen {p['count']}x): {p['desc']}")

    if not new_entries:
        return

    insert_after = text.index(lessons_marker)
    # Find the first blank line or end of section header
    insert_pos = text.find("\n", insert_after + len(lessons_marker)) + 1
    # Find end of existing italic hint line if present
    italic_end = text.find("\n", insert_pos)
    if "*(Agent updates" in text[insert_pos:italic_end]:
        insert_pos = italic_end + 1

    lesson_block = "\n".join(new_entries) + "\n"
    updated = text[:insert_pos] + lesson_block + text[insert_pos:]
    CLAUDE_MD.write_text(updated)
    print(f"  [CLAUDE.md] Appended {len(new_entries)} new lesson(s).")


# ── monthly learnings report ──────────────────────────────────────────────────

def write_monthly_report(patterns, trades, hints):
    wins, losses, total = win_rate(trades)
    wr = (wins / total * 100) if total else 0

    lines = [
        f"# Learning Agent Report — {date.today().isoformat()}",
        f"",
        f"## Performance Summary",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total closed trades | {total} |",
        f"| Win rate | {wr:.1f}% |",
        f"| Wins | {wins} |",
        f"| Losses | {losses} |",
        f"| Graduation threshold | {'✅ MET' if wr >= 55 else '❌ NOT MET'} (need ≥55%) |",
        f"",
        f"## Detected Patterns ({len(patterns)})",
    ]
    for i, p in enumerate(patterns, 1):
        lines.append(f"\n### {i}. {p['id']} (seen {p['count']}x)")
        lines.append(p["desc"])
        lines.append(f"**Recommended action**: `{p['action']}`")

    lines += [
        f"",
        f"## Judge Weight Hints (auto-tuned)",
        f"```json",
        json.dumps(hints, indent=2),
        f"```",
        f"",
        f"## Next Steps",
        f"- Review patterns above and confirm which actions to apply",
        f"- Run `python3 judge.py session` — it reads `judge_weight_hints.json` for adjustments",
        f"- Run `python3 reviewer.py calibration 30` for graduation check",
    ]

    MONTHLY_LOG.write_text("\n".join(lines))
    print(f"  [REPORT] Written → {MONTHLY_LOG}")


# ── pre-trade context (called by judge.py) ────────────────────────────────────

def get_pretrade_context(symbol: str, n: int = 3) -> str:
    """Return the N most similar past setups for this symbol as a warning string."""
    trades = load_calibration()
    past = [t for t in trades if t["symbol"] == symbol]
    if not past:
        return ""

    lines = [f"  [LEARNING] Past {symbol} trades:"]
    for t in past[-n:]:
        verdict_emoji = {"GOOD_TRADE": "✅", "POOR_TRADE": "❌", "NEUTRAL": "⚠️"}.get(t["verdict"], "?")
        pnl_str = f"{t['pnl_pct']:+.1f}%" if t["pnl_pct"] is not None else "open"
        score_str = f"judge={t['judge_score']}" if t["judge_score"] else "judge=?"
        lines.append(f"    {t['date']} {verdict_emoji} {t['verdict']} | {pnl_str} | {score_str}")

    poor = sum(1 for t in past if t["verdict"] == "POOR_TRADE")
    if poor >= 2:
        lines.append(f"  ⚠️  WARNING: {symbol} has {poor} POOR_TRADE outcomes — apply extra scrutiny.")

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{date.today()}] Learning agent — mode: {MODE}")

    trades      = load_calibration()
    judge_lines = load_judge_lines()

    if not trades:
        print("  No calibration data yet. Run reviewer.py first.")
        return

    print(f"  Loaded {len(trades)} trade records, {len(judge_lines)} judge entries.")

    patterns = detect_patterns(trades, judge_lines)
    hints    = update_weight_hints(patterns)

    print(f"  Detected {len(patterns)} pattern(s):")
    for p in patterns:
        print(f"    [{p['id']}] (x{p['count']}) {p['desc'][:100]}...")

    if MODE in ("eod", "weekly"):
        update_claude_lessons(patterns)
        write_monthly_report(patterns, trades, hints)

    elif MODE == "report":
        write_monthly_report(patterns, trades, hints)
        print(f"\n  Weight hints: {json.dumps(hints, indent=2)}")

    print(f"  Done.")


if __name__ == "__main__":
    main()
