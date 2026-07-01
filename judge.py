#!/usr/bin/env python3
"""
judge.py — Pre-trade scoring agent. Sits between Researcher and Executor.
Scores each proposed trade on 5 criteria and outputs APPROVE / HOLD / VETO.
The Executor only acts on APPROVEDs with score >= 7.0.

Usage:
  python judge.py score AAPL            # Score a single symbol from today's research
  python judge.py session               # Score all symbols in today's research output
  python judge.py read 2026-06-15       # Print a past judge report
"""

import os
import sys
import json
from datetime import date, datetime
from statistics import mean

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR  = os.path.join(ROOT, "logs", "paper")
JUDGE_MIN_SCORE = 7.0   # Executor gate

# ── Scoring Weights ───────────────────────────────────────────────────────────
# Total = 10 points. Weights sum to 10.
W_TECHNICAL   = 3.0   # MA signal strength
W_NEWS        = 2.0   # News sentiment
W_MACRO       = 1.5   # SPY regime
W_PORTFOLIO   = 2.0   # Cash reserve + correlation
W_VOLATILITY  = 1.5   # VIX proxy / market tone

# ── Earnings Proximity Veto ───────────────────────────────────────────────────
EARNINGS_VETO_DAYS = 3  # Auto-veto if earnings within this many days

def load_today_research():
    today = date.today().isoformat()
    path  = os.path.join(JOURNAL_DIR, f"{today}.json")
    if not os.path.exists(path):
        # Try the root-level journal dir for backwards compat
        path = os.path.join(ROOT, "journal", f"{today}.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)

def load_today_judge():
    today = date.today().isoformat()
    path  = os.path.join(JOURNAL_DIR, f"{today}-judge.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)

# ── Individual Criterion Scorers ──────────────────────────────────────────────

def score_technical(technicals: dict) -> tuple:
    """Score 0–3 based on MA signal strength."""
    signal = technicals.get("signal", "HOLD")
    ma20   = technicals.get("ma20", 0)
    ma50   = technicals.get("ma50", 0)
    last   = technicals.get("last_close", 0)
    trend_up = technicals.get("ma20_trending_up", False)

    if signal == "STRONG_BUY":
        score = 3.0 if trend_up else 2.5
        note  = f"STRONG_BUY — MA20 ${ma20:.2f} > MA50 ${ma50:.2f}, price ${last:.2f} above both"
    elif signal == "WEAK_BUY":
        score = 1.5
        note  = f"WEAK_BUY — MA20 ${ma20:.2f} > MA50 ${ma50:.2f}, price between MAs"
    elif signal == "HOLD":
        score = 0.5
        note  = "HOLD — no clear directional signal"
    elif signal == "WEAK_SELL":
        score = 0.0
        note  = f"WEAK_SELL — MA20 declining, price ${last:.2f} < MA20 ${ma20:.2f}"
    else:  # STRONG_SELL
        score = 0.0
        note  = f"STRONG_SELL — price ${last:.2f} below MA50 ${ma50:.2f}"

    return round(score, 2), note

def score_news(news_items: list, sym_data=None) -> tuple:
    """Score 0–2 based on news sentiment keywords. Penalized -0.5 if social pump flags present."""
    if not news_items:
        base_score = 1.0
        note = "No recent news — neutral"
    else:
        positive_kw = ["upgrade", "buy", "overweight", "beat", "raises", "record", "surge",
                       "breakout", "partnership", "deal", "approved", "profit", "growth"]
        negative_kw = ["downgrade", "sell", "underweight", "miss", "cut", "layoff", "lawsuit",
                       "recall", "investigation", "loss", "warning", "delay", "decline"]

        pos_count = neg_count = 0
        for item in news_items[:5]:
            headline = (item.get("headline", "") + " " + item.get("summary", "")).lower()
            pos_count += sum(1 for kw in positive_kw if kw in headline)
            neg_count += sum(1 for kw in negative_kw if kw in headline)

        if pos_count >= 2 and neg_count == 0:
            base_score, note = 2.0, f"POSITIVE — {pos_count} bullish signals in recent headlines"
        elif pos_count >= 1 and neg_count == 0:
            base_score, note = 1.5, f"MILDLY POSITIVE — {pos_count} bullish signal(s), no negatives"
        elif neg_count >= 2:
            base_score, note = 0.0, f"NEGATIVE — {neg_count} bearish signals in recent headlines"
        elif neg_count >= 1:
            base_score, note = 0.5, f"MIXED/NEGATIVE — {neg_count} bearish signal(s) present"
        else:
            base_score, note = 1.0, "NEUTRAL — no strong sentiment signals"

    # Social pump flag penalty — high social buzz without verified news is suspicious
    pump_flags = (sym_data or {}).get("pump_flags", [])
    if pump_flags:
        penalty = min(0.5, 0.25 * len(pump_flags))
        base_score = max(0.0, round(base_score - penalty, 2))
        note += f" | PUMP FLAG PENALTY -{penalty:.2f}: {pump_flags[0][:80]}"

    return base_score, note

def score_macro(research: dict) -> tuple:
    """Score 0–1.5 based on SPY macro regime from research data."""
    spy_data = research.get("symbols", {}).get("SPY", {})
    tech     = spy_data.get("technicals", {})

    if not tech or "error" in tech:
        return 0.75, "Macro data unavailable — neutral assumption"

    signal = tech.get("signal", "HOLD")
    ma20   = tech.get("ma20", 0)
    ma50   = tech.get("ma50", 0)

    if signal in ("STRONG_BUY", "WEAK_BUY") and ma20 > ma50:
        return 1.5, f"RISK-ON — SPY MA20 ${ma20:.2f} > MA50 ${ma50:.2f}"
    elif signal in ("STRONG_SELL", "WEAK_SELL") or ma20 < ma50:
        return 0.0, f"RISK-OFF — SPY MA20 ${ma20:.2f} < MA50 ${ma50:.2f} (half sizing applies)"
    else:
        return 0.75, "NEUTRAL macro regime"

def score_portfolio(research: dict, symbol: str) -> tuple:
    """Score 0–2 based on cash reserve % and existing position correlation."""
    account   = research.get("account", {})
    positions = research.get("positions", [])

    try:
        cash      = float(account.get("cash", 0))
        port_val  = float(account.get("portfolio_value", 1))
        cash_pct  = cash / port_val
    except (TypeError, ZeroDivisionError):
        return 1.0, "Portfolio data unavailable"

    open_syms = [p["symbol"] for p in positions]

    # Already in this position
    if symbol in open_syms:
        return 0.0, f"Already holding {symbol} — no new position"

    # Correlation penalty: tech-heavy basket
    tech_names  = {"NVDA", "AAPL", "MSFT", "GOOG", "GOOGL", "META", "AMZN", "AMD", "TSM", "AVGO", "QQQ"}
    defense_names = {"LMT", "RTX", "NOC", "GD", "LHX"}
    open_tech   = [s for s in open_syms if s in tech_names]
    corr_note   = ""
    corr_penalty = 0.0

    if symbol in tech_names and len(open_tech) >= 2:
        corr_penalty = 0.5
        corr_note    = f" | Correlation penalty: already holding {', '.join(open_tech)}"
    elif symbol in tech_names and len(open_tech) == 1:
        corr_penalty = 0.25
        corr_note    = f" | Mild correlation: 1 tech name already held ({open_tech[0]})"

    # Cash reserve score
    if cash_pct >= 0.40:
        cash_score = 2.0
        cash_note  = f"Strong cash reserve {cash_pct*100:.0f}%"
    elif cash_pct >= 0.25:
        cash_score = 1.5
        cash_note  = f"Adequate cash reserve {cash_pct*100:.0f}%"
    elif cash_pct >= 0.20:
        cash_score = 1.0
        cash_note  = f"Cash reserve at floor {cash_pct*100:.0f}% — proceed cautiously"
    else:
        cash_score = 0.0
        cash_note  = f"CASH BELOW 20% FLOOR ({cash_pct*100:.0f}%) — VETO"

    score = max(0.0, cash_score - corr_penalty)
    return round(score, 2), cash_note + corr_note

def score_volatility(research: dict) -> tuple:
    """
    Score 0–1.5 using VIXY as VIX proxy if available in research,
    otherwise infer from recent SPY range volatility.
    """
    # Try VIXY as VIX proxy
    vixy = research.get("symbols", {}).get("VIXY", {})
    tech  = vixy.get("technicals", {})

    if tech and "error" not in tech:
        signal = tech.get("signal", "HOLD")
        if signal in ("STRONG_SELL", "WEAK_SELL"):
            return 1.5, "Low volatility (VIXY falling) — favorable entry environment"
        elif signal == "HOLD":
            return 1.0, "Moderate volatility (VIXY flat)"
        else:
            return 0.25, "Elevated volatility (VIXY rising) — reduce size per Citadel rule"

    # Fallback: use SPY intraday range as vol proxy
    spy_data = research.get("symbols", {}).get("SPY", {})
    return 1.0, "VIX proxy unavailable — neutral volatility assumption (add VIXY to watchlist)"

def check_earnings_veto(news_items: list, symbol: str) -> tuple:
    """
    Veto if any headline suggests earnings are within 3 days.
    Heuristic: look for 'earnings' or 'reports' in recent news.
    """
    earnings_kw = ["earnings", "reports q", "quarterly results", "eps report",
                   "earnings report", "reports earnings"]
    for item in (news_items or [])[:10]:
        headline = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        if any(kw in headline for kw in earnings_kw):
            return True, f"Earnings proximity detected in recent news — auto-veto per hard rule #6"
    return False, ""

# ── Master Scoring Function ───────────────────────────────────────────────────

def score_symbol(symbol: str, research: dict) -> dict:
    """
    Run all criteria for one symbol. Returns full judge output dict.
    """
    sym_data   = research.get("symbols", {}).get(symbol, {})
    technicals = sym_data.get("technicals", {})
    news       = sym_data.get("news", [])

    if "error" in sym_data:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0, "rationale": f"Data error: {sym_data['error']}",
            "criteria": {}
        }

    # Social pump veto — load today's screener output and check pump_risk
    try:
        import json as _json
        _today = __import__("datetime").date.today().isoformat()
        _social_path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{_today}-social.json")
        if os.path.exists(_social_path):
            with open(_social_path) as _f:
                _social = _json.load(_f)
            for _t in _social.get("top_tickers", []):
                if _t.get("ticker") == symbol:
                    _pump = _t.get("pump_risk", {})
                    if _pump.get("is_pump"):
                        _reasons = "; ".join(_pump.get("vetoes", []))
                        return {
                            "symbol": symbol, "recommendation": "VETO",
                            "score": 0.0,
                            "rationale": f"PUMP_RISK detected by social screener — {_reasons}",
                            "criteria": {"pump_veto": True, "pump_details": _pump}
                        }
                    elif _pump.get("has_flags"):
                        # Flags don't veto — but they penalize the news score later
                        # by injecting a flag note into sym_data for score_news() to see
                        sym_data.setdefault("pump_flags", _pump.get("flags", []))
                    break
    except Exception:
        pass  # Screener not run yet — proceed normally

    # Earnings veto (hard rule — bypasses scoring)
    earnings_veto, earnings_reason = check_earnings_veto(news, symbol)
    if earnings_veto:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0, "rationale": earnings_reason,
            "criteria": {"earnings_veto": True}
        }

    # Score each criterion
    t_score, t_note = score_technical(technicals)
    n_score, n_note = score_news(news, sym_data)
    m_score, m_note = score_macro(research)
    p_score, p_note = score_portfolio(research, symbol)
    v_score, v_note = score_volatility(research)

    # Hard veto: cash below floor
    if "CASH BELOW 20%" in p_note:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0,
            "rationale": "Cash reserve below 20% floor — hard veto",
            "criteria": {
                "technical": {"score": t_score, "note": t_note},
                "news": {"score": n_score, "note": n_note},
                "macro": {"score": m_score, "note": m_note},
                "portfolio": {"score": p_score, "note": p_note},
                "volatility": {"score": v_score, "note": v_note},
            }
        }

    # Hard veto: signal is SELL
    if technicals.get("signal") in ("STRONG_SELL", "WEAK_SELL"):
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": t_score,
            "rationale": f"Sell signal present — not a buy candidate",
            "criteria": {
                "technical": {"score": t_score, "note": t_note},
                "news": {"score": n_score, "note": n_note},
                "macro": {"score": m_score, "note": m_note},
                "portfolio": {"score": p_score, "note": p_note},
                "volatility": {"score": v_score, "note": v_note},
            }
        }

    total = round(t_score + n_score + m_score + p_score + v_score, 2)

    if total >= JUDGE_MIN_SCORE:
        rec = "APPROVE"
    elif total >= 5.0:
        rec = "HOLD"
    else:
        rec = "VETO"

    rationale = (
        f"Technical: {t_note}. "
        f"News: {n_note}. "
        f"Macro: {m_note}. "
        f"Portfolio: {p_note}. "
        f"Volatility: {v_note}."
    )

    return {
        "symbol": symbol,
        "recommendation": rec,
        "score": total,
        "score_breakdown": {
            "technical":  f"{t_score}/{W_TECHNICAL}",
            "news":       f"{n_score}/{W_NEWS}",
            "macro":      f"{m_score}/{W_MACRO}",
            "portfolio":  f"{p_score}/{W_PORTFOLIO}",
            "volatility": f"{v_score}/{W_VOLATILITY}",
        },
        "criteria": {
            "technical":  {"score": t_score, "note": t_note},
            "news":       {"score": n_score, "note": n_note},
            "macro":      {"score": m_score, "note": m_note},
            "portfolio":  {"score": p_score, "note": p_note},
            "volatility": {"score": v_score, "note": v_note},
        },
        "rationale": rationale,
    }

def format_judge_report(result: dict) -> str:
    """Format a single symbol's judge output as markdown."""
    rec   = result["recommendation"]
    score = result["score"]
    sym   = result["symbol"]
    icon  = {"APPROVE": "✅", "HOLD": "⏸️", "VETO": "🚫"}.get(rec, "?")

    lines = [f"## {sym} — Judge Pre-Trade Assessment {icon}"]
    criteria = result.get("criteria", {})
    for key in ("technical", "news", "macro", "portfolio", "volatility"):
        c = criteria.get(key, {})
        if c:
            lines.append(f"- **{key.capitalize()}** ({c.get('score', '?')}pt): {c.get('note', '')}")

    breakdown = result.get("score_breakdown", {})
    if breakdown:
        parts = " | ".join(f"{k}: {v}" for k, v in breakdown.items())
        lines.append(f"\n**Score Breakdown**: {parts}")

    lines.append(f"\n**Pre-Trade Score: {score} / 10**")
    lines.append(f"**Recommendation: {rec}**")
    lines.append(f"**Rationale**: {result.get('rationale', '')}")
    return "\n".join(lines)

# ── Session Runner ────────────────────────────────────────────────────────────

def run_judge_session() -> dict:
    """Score all buy-candidate symbols in today's research."""
    research = load_today_research()
    if not research:
        return {"error": "No research data found for today. Run research.py full first."}

    symbols_data = research.get("symbols", {})
    today        = date.today().isoformat()
    results      = {
        "timestamp": datetime.now().isoformat(),
        "date": today,
        "approved": [],
        "held": [],
        "vetoed": [],
        "scores": {}
    }

    # Only judge buy candidates (STRONG_BUY or WEAK_BUY signal)
    for sym, data in symbols_data.items():
        if sym == "SPY":  # SPY is macro ref, not a trade candidate
            continue
        tech   = data.get("technicals", {})
        signal = tech.get("signal", "HOLD")
        if signal not in ("STRONG_BUY", "WEAK_BUY"):
            continue

        scored = score_symbol(sym, research)
        results["scores"][sym] = scored

        if scored["recommendation"] == "APPROVE":
            results["approved"].append(sym)
        elif scored["recommendation"] == "HOLD":
            results["held"].append(sym)
        else:
            results["vetoed"].append(sym)

    # Save judge output
    out_path = os.path.join(JOURNAL_DIR, f"{today}-judge.json")
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    # Write human-readable markdown report
    md_lines = [f"# Judge Report — {today}\n",
                f"**Approved**: {', '.join(results['approved']) or 'None'}",
                f"**Held**: {', '.join(results['held']) or 'None'}",
                f"**Vetoed**: {', '.join(results['vetoed']) or 'None'}\n",
                "---\n"]
    for sym, scored in results["scores"].items():
        md_lines.append(format_judge_report(scored))
        md_lines.append("\n---\n")

    md_path = os.path.join(JOURNAL_DIR, f"{today}-judge.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    results["report_path"] = md_path
    return results

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "session"
    symbol = sys.argv[2] if len(sys.argv) > 2 else None

    if action == "session":
        print(json.dumps(run_judge_session(), indent=2))

    elif action == "score" and symbol:
        research = load_today_research()
        if not research:
            print(json.dumps({"error": "No research data for today"}))
        else:
            result = score_symbol(symbol.upper(), research)
            print(format_judge_report(result))
            print(json.dumps(result, indent=2))

    elif action == "read" and symbol:
        path = os.path.join(JOURNAL_DIR, f"{symbol}-judge.md")
        if os.path.exists(path):
            with open(path) as f:
                print(f.read())
        else:
            print(f"No judge report for {symbol}")

    else:
        print(json.dumps({"error": f"Unknown action: {action}. Use: session | score SYMBOL | read DATE"}))
