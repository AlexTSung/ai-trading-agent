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
import re
from datetime import date, datetime, timedelta
from statistics import mean

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT         = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR  = os.path.join(ROOT, "logs", "paper")
JUDGE_MIN_SCORE = 7.0   # Executor gate

# ── Scoring Weights ───────────────────────────────────────────────────────────
# Total = 10 points. Weights sum to 10.
# Lesson from portfolio analysis: small-cap discovery stocks with good MA signals
# were losing trades. Quality (market cap, profitability, analyst coverage) is
# the single strongest predictor of a winning stock. Reduced technical weight
# accordingly and added explicit quality criterion.
W_TECHNICAL   = 2.0   # MA signal + RSI/BB entry quality
W_NEWS        = 1.5   # News sentiment
W_MACRO       = 1.0   # SPY regime
W_PORTFOLIO   = 2.0   # Cash reserve + correlation
W_VOLATILITY  = 1.0   # VIX proxy / market tone
W_QUALITY     = 2.5   # NEW: market cap, profitability, analyst coverage

# ── Source-aware approval thresholds ──────────────────────────────────────────
# Discovery stocks (social, screener, most-active) have NOT been manually vetted.
# They require a higher bar than watchlist picks.
SCORE_GATE_WATCHLIST  = 7.0   # manually curated, known thesis
SCORE_GATE_DISCOVERY  = 8.5   # auto-discovered, unknown quality
DISCOVERY_SOURCES = {"social_screener", "alpaca_most_active", "alpaca_gainers",
                     "market_screener", "news_firehose", "reddit", "congressional"}

MAX_TOTAL_SCORE = 10.0
assert (W_TECHNICAL + W_NEWS + W_MACRO + W_PORTFOLIO + W_VOLATILITY
        + W_QUALITY) == MAX_TOTAL_SCORE, "Criterion weights must sum to 10"

# ── Earnings Proximity Veto ───────────────────────────────────────────────────
EARNINGS_VETO_DAYS = 3  # Auto-veto if earnings within this many days

# Discovery stocks that score zero on quality have no verifiable market cap,
# profitability, or analyst coverage. A strong chart is not enough on its own.
DISCOVERY_MIN_QUALITY = 0.5


# ── Learned hints (Alpaca equities only) ──────────────────────────────────────
# learning_agent.py writes logs/learnings/alpaca_judge_hints.json after each EOD.
# Those hints were being computed and never read — the same fate as the VIX and
# earnings data before them. They are consumed here, under four hard rails:
#
#   1. SCOPE — a hint file not tagged alpaca_equities is refused outright. The
#      OANDA judge scores carry/momentum/session; applying its lessons here
#      would be meaningless, and vice versa.
#   2. SAMPLE — the allowed adjustment scales with the number of CLOSED round
#      trips (see LEARNING_TIERS). Patterns are recorded after every single
#      round trip, advisory from 5, small nudges apply from 10, and the full
#      band only at 30. A flat on/off gate meant no learning for months.
#   3. TIGHTEN-ONLY — a learned gate may rise above the documented floor but
#      never fall below it. Otherwise the loop's cheapest way to book more
#      "wins" is to lower its own bar, and risk limits would erode silently.
#   4. CLAMP + LOG — weight adjustments are bounded to a narrow band and every
#      applied hint is reported in the judge output, so a change to trading
#      behaviour is always visible in the artefact rather than buried in JSON.
HINTS_PATH        = os.path.join(ROOT, "logs", "learnings", "alpaca_judge_hints.json")
HINTS_SCOPE       = "alpaca_equities"
ROUND_TRIPS_PATH  = os.path.join(JOURNAL_DIR, "round-trips.csv")
# ── Graduated learning tiers ──────────────────────────────────────────────────
# A single 30-trip on/off gate meant no learning at all for months at the
# current trade rate. Instead the ALLOWED ADJUSTMENT scales with the sample, so
# iteration is fast while the size of any single change stays proportional to
# the evidence behind it.
#
#   (min_round_trips, max_weight_deviation, tier_name)
#
# Why not act hard at 5: with 5 trips a 60% observed win rate carries a 95%
# confidence interval of roughly 23%-88%. That interval contains both "excellent"
# and "losing", so a large parameter move fitted on it is fitting noise. Small
# nudges are fine; betting the strategy is not.
LEARNING_TIERS = (
    (5,  0.03, "advisory"),    # observe + report; weights recorded, not applied
    (10, 0.06, "active"),      # small nudges take effect
    (20, 0.10, "active"),
    (30, 0.15, "confident"),   # full band
)
HINTS_WEIGHT_BAND_MAX = LEARNING_TIERS[-1][1]   # widest band any tier may use


def learning_tier(sample: int) -> tuple:
    """Return (tier_name, max_weight_deviation) for a given round-trip count."""
    tier, band = "observe", 0.0
    for threshold, allowed, name in LEARNING_TIERS:
        if sample >= threshold:
            tier, band = name, allowed
    return tier, band


def wilson_interval(wins, n, z: float = 1.96) -> tuple:
    """
    95% Wilson score interval for a win rate. Reported alongside every applied
    hint so the width of the uncertainty is visible, not just the point estimate.
    """
    try:
        wins, n = int(wins), int(n)
    except (TypeError, ValueError):
        return None, None
    if n <= 0 or wins < 0 or wins > n:
        return None, None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)



def _closed_round_trip_count() -> int:
    """Completed entry->exit pairs on record. The honest sample size."""
    try:
        with open(ROUND_TRIPS_PATH) as f:
            return max(0, sum(1 for _ in f) - 1)   # minus header
    except OSError:
        return 0


def load_learned_hints() -> dict:
    """
    Return {"applied": {...}, "notes": [...]} — never raises.
    `applied` is empty unless every rail is satisfied.
    """
    out = {"applied": {}, "notes": []}
    try:
        with open(HINTS_PATH) as f:
            hints = json.load(f)
    except (OSError, ValueError):
        return out

    scope = hints.get("scope")
    if scope != HINTS_SCOPE:
        out["notes"].append(
            f"Hints refused: scope {scope!r} is not {HINTS_SCOPE!r}")
        return out

    sample = _closed_round_trip_count()
    tier, band = learning_tier(sample)
    out["tier"], out["sample"], out["band"] = tier, sample, band

    if tier == "observe":
        out["notes"].append(
            f"OBSERVE tier: {sample} closed round trip(s), need "
            f"{LEARNING_TIERS[0][0]} before any adjustment. Patterns are still "
            f"recorded every round trip.")
        return out

    # ── Risk-reducing changes may act on a small sample; risk-increasing ones
    # may not. Reacting fast to losses is prudent; reacting fast to wins is how
    # a run of luck becomes a policy. The gate can only ever tighten.
    thr = hints.get("approve_threshold")
    if isinstance(thr, (int, float)):
        if thr > SCORE_GATE_WATCHLIST:
            out["applied"]["watchlist_gate"] = round(float(thr), 2)
            out["notes"].append(
                f"Gate tightened to {thr} — risk-reducing, allowed at {tier} tier")
        else:
            out["notes"].append(
                f"Gate hint {thr} ignored — would loosen the "
                f"{SCORE_GATE_WATCHLIST} floor")

    # ── Weight nudges scale with sample size instead of switching on at 30.
    adj = hints.get("news_weight_adj")
    if isinstance(adj, (int, float)):
        if tier == "advisory":
            out["notes"].append(
                f"News weight hint {adj:.2f} is ADVISORY at {sample} round trips "
                f"— recorded, not applied. Needs "
                f"{LEARNING_TIERS[1][0]} to take effect.")
        else:
            lo, hi = 1 - band, 1 + band
            clamped = max(lo, min(float(adj), hi))
            if abs(clamped - adj) > 1e-9:
                out["notes"].append(
                    f"News weight hint {adj:.2f} clamped to {clamped:.2f} "
                    f"(±{band:.0%} band at {sample} round trips)")
            out["applied"]["news_weight_mult"] = round(clamped, 3)

    lo_ci, hi_ci = wilson_interval(hints.get("wins"), sample)
    if lo_ci is not None:
        out["notes"].append(
            f"Win rate 95% CI on {sample} trips: {lo_ci:.0%}–{hi_ci:.0%}"
            + ("  (too wide to distinguish skill from luck)"
               if hi_ci - lo_ci > 0.35 else ""))
    out["notes"].append(f"{tier.upper()} tier — fitted on {sample} closed round trips")
    return out


def honest_portfolio_value(account: dict, positions: list) -> float:
    """
    Portfolio value with phantom equity marked back to cost basis.

    The cash-reserve percentage divides by portfolio value, so an unadjusted
    corporate action collapses it: LBGJ marked at $478,650 against a $4,461
    basis made cash read 12.6% instead of its true 78%, tripping the hard
    CASH BELOW 20% FLOOR veto on every candidate and freezing all buying from
    2026-08-03 onward. Reuses the executor's detector so both agents agree on
    what counts as real.
    """
    reported = float(account.get("portfolio_value", 1) or 1)
    try:
        from trade import detect_phantom_equity
        excess = sum(f.get("phantom_amount", 0.0)
                     for f in detect_phantom_equity(positions or []))
    except Exception:
        return reported
    return max(1.0, reported - excess)


# ── Speculative exposure + holiday-week gates (from the options/forex work) ──

SPECULATIVE_CAP_THRESHOLD = 0.5  # < $500M market cap = speculative

def _nth_weekday(year, month, weekday, n):
    """Return the date of the nth occurrence of weekday (0=Mon) in given month."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (n - 1))

def _last_weekday(year, month, weekday):
    """Return the date of the last occurrence of weekday in given month."""
    last = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    delta = (last.weekday() - weekday) % 7
    return last - timedelta(days=delta)

def is_holiday_week() -> tuple:
    """
    Return (True, holiday_name) if current trading week (Mon-Fri) contains a major US holiday.
    Skips speculative trades in holiday weeks due to low liquidity and erratic moves.
    """
    today = date.today()
    week_mon = today - timedelta(days=today.weekday())
    week_fri = week_mon + timedelta(days=4)
    y = today.year

    holidays = {
        "New Year's Day":   date(y, 1, 1),
        "MLK Day":          _nth_weekday(y, 1, 0, 3),
        "Presidents Day":   _nth_weekday(y, 2, 0, 3),
        "Memorial Day":     _last_weekday(y, 5, 0),
        "Independence Day": date(y, 7, 4),
        "Labor Day":        _nth_weekday(y, 9, 0, 1),
        "Thanksgiving":     _nth_weekday(y, 11, 3, 4),
        "Christmas":        date(y, 12, 25),
    }

    for name, hday in holidays.items():
        if week_mon <= hday <= week_fri:
            return True, name
    return False, ""

def count_speculative_positions(research: dict) -> int:
    """Count open positions that are speculative (discovery source or micro/small-cap)."""
    positions = research.get("positions", [])
    symbols_data = research.get("symbols", {})
    count = 0
    for pos in positions:
        sym = pos.get("symbol")
        sym_data = symbols_data.get(sym, {})
        source = sym_data.get("source", "watchlist")
        cap_b = (sym_data.get("fundamentals") or {}).get("market_cap_b")
        is_spec = (source in DISCOVERY_SOURCES) or (cap_b is not None and cap_b < SPECULATIVE_CAP_THRESHOLD)
        if is_spec:
            count += 1
    return count


def _clamp(scored: tuple, weight: float) -> tuple:
    """Clamp a (score, note) pair from a scorer into [0, weight]."""
    score, note = scored
    capped = max(0.0, min(float(score), weight))
    if capped != score:
        note = f"{note} [capped {score}→{capped} at weight {weight}]"
    return round(capped, 2), note

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

AVG_DOWN_MIN_DROP = 0.03   # Must be ≥3% below avg entry price to qualify
AVG_DOWN_MAX_POS  = 0.08   # Max total position size (% of portfolio) before avg-down blocked

def score_portfolio(research: dict, symbol: str) -> tuple:
    """Score 0–2 based on cash reserve % and existing position correlation.
    Existing positions score 1.5 as avg-down candidates when ≥3% below entry and under max size.
    """
    account   = research.get("account", {})
    positions = research.get("positions", [])

    try:
        cash      = float(account.get("cash", 0))
        port_val  = honest_portfolio_value(account, positions)
        cash_pct  = cash / port_val
    except (TypeError, ZeroDivisionError):
        return 1.0, "Portfolio data unavailable"

    open_pos = {p["symbol"]: p for p in positions}

    # Already holding — check avg-down eligibility
    if symbol in open_pos:
        pos = open_pos[symbol]
        try:
            current_price = float(pos.get("current_price", 0))
            avg_entry     = float(pos.get("avg_entry_price", 0))
            mkt_val       = float(pos.get("market_value", 0))
            pct_down      = (current_price - avg_entry) / avg_entry if avg_entry else 0
            pos_pct       = mkt_val / port_val if port_val else 0
        except (TypeError, ValueError):
            return 0.0, f"Already holding {symbol} — data error, skipping avg-down"

        if pos_pct >= AVG_DOWN_MAX_POS:
            return 0.0, f"Already holding {symbol} — at max size ({pos_pct*100:.1f}% of portfolio, cap 8%)"
        if pct_down >= 0:
            return 0.0, f"Already holding {symbol} — price above entry, avg-down not warranted"
        if abs(pct_down) < AVG_DOWN_MIN_DROP:
            return 0.0, f"Already holding {symbol} — only {abs(pct_down)*100:.1f}% below entry (need ≥3%)"

        return 1.5, (
            f"AVG_DOWN candidate: {symbol} is {abs(pct_down)*100:.1f}% below entry "
            f"(entry ${avg_entry:.2f} → now ${current_price:.2f}), "
            f"position {pos_pct*100:.1f}% of portfolio (max 8%)"
        )

    open_syms = list(open_pos.keys())

    # Correlation penalty: tech-heavy basket
    tech_names   = {"NVDA", "AAPL", "MSFT", "GOOG", "GOOGL", "META", "AMZN", "AMD", "TSM", "AVGO", "QQQ"}
    open_tech    = [s for s in open_syms if s in tech_names]
    corr_note    = ""
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
    Score 0–1.5 from the real VIX level when available, then VIXY trend,
    then SPY range as a last resort.
    """
    # Authoritative source: the VIX close carried through from intelligence.py
    # (FRED VIXCLS). Previously this data was fetched every morning and thrown
    # away, so this branch never ran and every candidate banked a free point.
    intel = research.get("intelligence") or {}
    vix   = intel.get("vix")
    if isinstance(vix, (int, float)):
        regime = intel.get("vix_regime", "UNKNOWN")
        if vix > VIX_NO_NEW_POSITIONS:
            return 0.0, (f"VIX {vix:.1f} > {VIX_NO_NEW_POSITIONS:.0f} — no new "
                         f"positions, manage existing only ({regime})")
        if vix > VIX_HALF_SIZE:
            return 0.25, (f"VIX {vix:.1f} > {VIX_HALF_SIZE:.0f} — elevated "
                          f"volatility, half sizing applies ({regime})")
        if vix >= 20:
            return 0.75, f"VIX {vix:.1f} — moderate volatility ({regime})"
        return 1.5, f"VIX {vix:.1f} — calm tape, favorable entry ({regime})"

    # Fallback: VIXY trend from the watchlist
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

def score_quality(sym_data: dict, is_discovery: bool, symbol: str = "",
                  insider_buy_symbols: list = None) -> tuple:
    """
    Score 0–2.5 based on business quality signals.
    Market cap is the primary filter — every significant loser was small/micro cap.
    Profitability and analyst coverage are secondary filters.

    Hard veto for discovery stocks: market cap < $1B (micro/nano cap)
    Returns: (score, note, hard_veto: bool)
    """
    funds     = sym_data.get("fundamentals", {})
    cap_b     = funds.get("market_cap_b")       # float in billions, or None
    is_prof   = funds.get("is_profitable")      # bool or None
    buy_count = funds.get("analyst_buy_count") or 0
    short_fl  = funds.get("short_float")

    score = 0.0
    notes = []

    # ── Market cap (0–1.5 pts) ────────────────────────────────────────────────
    if cap_b is not None:
        if cap_b >= 100:
            score += 1.5
            notes.append(f"Mega-cap ${cap_b:.0f}B")
        elif cap_b >= 10:
            score += 1.25
            notes.append(f"Large-cap ${cap_b:.0f}B")
        elif cap_b >= 2:
            score += 0.75
            notes.append(f"Mid-cap ${cap_b:.1f}B")
        elif cap_b >= 0.5:
            score += 0.25
            notes.append(f"Small-cap ${cap_b:.1f}B — limited institutional support")
        else:
            # Micro-cap: hard veto for discovery stocks
            if is_discovery:
                return 0.0, f"MICRO_CAP VETO: ${cap_b:.2f}B market cap — too small for discovery buys", True
            score += 0.0
            notes.append(f"Micro-cap ${cap_b:.2f}B — very high risk")
    else:
        if is_discovery:
            # No cap data on a discovery stock = can't verify quality
            score += 0.0
            notes.append("Market cap unavailable — capped at 0 for discovery stock")
        else:
            score += 0.5   # watchlist stock, give benefit of the doubt
            notes.append("Market cap data unavailable")

    # ── Profitability (0–0.5 pts) ─────────────────────────────────────────────
    if is_prof is True:
        score += 0.5
        notes.append("Profitable")
    elif is_prof is False:
        score -= 0.25
        notes.append("Unprofitable — burning cash")

    # ── Analyst coverage (0–0.5 pts) ─────────────────────────────────────────
    if buy_count >= 20:
        score += 0.5
        notes.append(f"{buy_count} analyst buy ratings — strong institutional coverage")
    elif buy_count >= 10:
        score += 0.35
        notes.append(f"{buy_count} analyst buy ratings")
    elif buy_count >= 5:
        score += 0.15
        notes.append(f"{buy_count} analyst buy ratings")
    elif buy_count == 0 and is_discovery:
        score -= 0.25
        notes.append("Zero analyst buy coverage on a discovery stock")

    # ── High short float penalty ──────────────────────────────────────────────
    try:
        if short_fl:
            sf = float(str(short_fl).replace("%", ""))
            if sf > 20:
                score -= 0.5
                notes.append(f"Short float {short_fl} — heavily shorted, risk of squeeze or continued decline")
            elif sf > 10:
                score -= 0.25
                notes.append(f"Short float {short_fl} — elevated short interest")
    except (ValueError, TypeError):
        pass

    # Insider buying (SEC EDGAR Form 4), carried through from intelligence.py.
    # Officers and directors buying their own stock with their own money is one
    # of the few genuinely informative quality signals available for free. This
    # was fetched every morning and discarded before now.
    if symbol and symbol in (insider_buy_symbols or []):
        score += 0.5
        notes.append(f"INSIDER BUYING: recent Form 4 purchase filed for {symbol}")

    score = round(max(0.0, min(score, W_QUALITY)), 2)
    return score, "; ".join(notes) if notes else "No quality data", False


# Citadel volatility sizing thresholds, evaluated against the real VIX index.
VIX_HALF_SIZE        = 25.0
VIX_NO_NEW_POSITIONS = 35.0

RSI_OVERBOUGHT_GATE   = 60.0   # CLAUDE.md Entry Checklist: RSI > 60 means wait
MAX_EXTENSION_ABOVE_MA20 = 0.03  # ...and do not chase more than 3% above MA20


def check_chase_veto(technicals: dict, symbol: str) -> tuple:
    """
    Refuse entries that chase strength, per CLAUDE.md Entry Checklist:
      1. More than 3% above MA20 → wait for a pullback.
      2. RSI(14) above 60 → overbought, wait.

    Returns (veto: bool, reason: str).
    """
    if not technicals or "error" in technicals:
        return False, ""

    rsi  = technicals.get("rsi")
    ma20 = technicals.get("ma20") or 0
    last = technicals.get("last_close") or 0

    if rsi is not None and rsi > RSI_OVERBOUGHT_GATE:
        return True, (f"CHASE VETO: RSI(14) {rsi:.0f} is above "
                      f"{RSI_OVERBOUGHT_GATE:.0f} — overbought, wait for a pullback")

    if ma20 > 0 and last > 0:
        extension = (last - ma20) / ma20
        if extension > MAX_EXTENSION_ABOVE_MA20:
            return True, (f"CHASE VETO: price ${last:.2f} is {extension:.1%} above "
                          f"MA20 ${ma20:.2f} (limit "
                          f"{MAX_EXTENSION_ABOVE_MA20:.0%}) — do not chase")

    return False, ""


# Backward-looking phrasing: a recap of a past report is not an upcoming one.
BACKWARD_EARNINGS_KW = ("reported", "posted", "released", "beat", "missed", "last quarter",
                        "results showed", "after reporting", "following its")
FORWARD_EARNINGS_KW  = ("will report", "to report", "is scheduled", "ahead of",
                        "upcoming", "next week", "due to report", "expected to report",
                        "previews", "preview", "before the bell", "after the bell")


# Fund-holdings language. "Q2" in a 13F story is a reporting period, not an
# earnings date, and these headlines are the dominant false-positive source.
PORTFOLIO_NEWS_KW = ("portfolio", "13f", "stake", "stakes", "holdings",
                     "bets on", "bets big", "exits", "trimmed", "boosted",
                     "revamps", "ditching")

EARNINGS_PHRASE_KW = ("earnings", "eps", "quarterly results", "reports q",
                      "quarterly report", "guidance")


def _has_phrase(text: str, phrases) -> bool:
    """
    Word-boundary match. Plain substring matching is unsafe for short tokens:
    "eps" matches inside "Steps up", which vetoed AAPL and NVDA on a Broadcom
    headline containing no earnings content at all.
    """
    return any(re.search(rf"\b{re.escape(p)}", text) for p in phrases)


def _is_upcoming_earnings(text: str) -> bool:
    """
    True only when text plausibly describes an UPCOMING earnings report.

    Rejects fund-holdings stories outright, then requires earnings phrasing that
    is not purely a recap of a report already published.
    """
    t = (text or "").lower()
    if not t:
        return False
    if _has_phrase(t, PORTFOLIO_NEWS_KW):
        return False
    if not _has_phrase(t, EARNINGS_PHRASE_KW):
        return False
    forward  = _has_phrase(t, FORWARD_EARNINGS_KW)
    backward = _has_phrase(t, BACKWARD_EARNINGS_KW)
    return not (backward and not forward)


def check_earnings_veto(news_items: list, symbol: str, research: dict = None) -> tuple:
    """
    Enforce the 3-day earnings blackout (CLAUDE.md Hard Rule 9).

    Two sources are consulted:
      1. intelligence.py's earnings watch, which scans 10 articles per symbol
         each morning. Previously computed and discarded.
      2. This symbol's own recent news.

    Neither is a true earnings calendar — Alpaca has no earnings endpoint on the
    free tier, so both are keyword scans over news. Recall is improved by taking
    the union, and false positives are reduced by ignoring purely backward-looking
    recaps ("reported Q2 results") unless forward-looking language is also
    present. A real calendar feed would be strictly better; see CLAUDE.md.
    """
    intel = (research or {}).get("intelligence") or {}
    if symbol in (intel.get("earnings_watch_symbols") or []):
        # Re-check the triggering headline rather than trusting the flag. The
        # upstream scan is a loose keyword match: during 13F season it flagged
        # MSFT, GOOGL, NOK and QCOM on "Q2 Portfolio" headlines and DDOG on a
        # SpaceX earnings story, vetoing most of the watchlist for no reason.
        headline = (intel.get("earnings_watch_detail") or {}).get(symbol, "")
        if _is_upcoming_earnings(headline):
            return True, (f"EARNINGS VETO: {symbol} flagged by the morning "
                          f"earnings watch — no entry within "
                          f"{EARNINGS_VETO_DAYS} trading days of a report")

    earnings_kw = ["earnings", "reports q", "quarterly results", "eps report",
                   "earnings report", "reports earnings"]
    for item in (news_items or [])[:10]:
        text = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        if not any(kw in text for kw in earnings_kw):
            continue
        forward  = any(kw in text for kw in FORWARD_EARNINGS_KW)
        backward = any(kw in text for kw in BACKWARD_EARNINGS_KW)
        if backward and not forward:
            continue  # recap of a report already out — not a blackout trigger
        return True, (f"EARNINGS VETO: upcoming-earnings language in recent news "
                      f"for {symbol} — auto-veto per Hard Rule 9")
    return False, ""

DIP_BUY_MIN_SCORE  = 8.0   # Strict gate — dip buys require highest conviction

DIP_BUY_STRUCTURAL_KW = [
    "bankruptcy", "fraud", "sec investigation", "accounting irregularit",
    "restatement", "going concern", "delisted", "chapter 11", "insolvency",
    "ponzi", "class action", "criminal", "federal investigation",
]
DIP_BUY_TEMPORARY_KW = [
    "miss", "guidance cut", "disappoints", "below expectations",
    "lowered guidance", "earnings miss", "revenue miss",
]


def check_falling_knife(technicals: dict, dip_pct: float) -> tuple:
    """
    Distinguishes a true dip (quality stock hit by a shock) from a falling knife
    (already-weak stock continuing to collapse).

    Returns: (is_falling_knife: bool, hard_veto: bool, penalty: float, reason: str)

    Hard veto conditions (do not trade under any circumstances):
      - Stock was already below MA50 yesterday — was weak BEFORE today's drop
      - 3+ consecutive down days before today — sustained distribution

    Penalty conditions (reduce score but don't veto):
      - Was below MA20 yesterday (mild downtrend entering the drop): -1.5 pts
      - 2 consecutive down days: -1.0 pt

    Strength signals (reduce penalty or boost confidence):
      - RSI < 35 (oversold/capitulation): signals panic bottom, not a falling knife
      - Volume spike ≥ 2x average: capitulation volume, sellers exhausting
    """
    was_above_ma50 = technicals.get("was_above_ma50_yest", True)
    was_above_ma20 = technicals.get("was_above_ma20_yest", True)
    consec_down    = technicals.get("consec_down_days", 0)
    rsi            = technicals.get("rsi", 50)
    vol_ratio      = technicals.get("volume_ratio", 1.0)
    oversold       = rsi < 35
    capitulation   = vol_ratio >= 2.0

    reasons = []

    # ── Hard veto ─────────────────────────────────────────────────────────────
    if not was_above_ma50:
        return (True, True, 0,
                f"FALLING KNIFE VETO: Stock was already below MA50 before today's drop — "
                f"pre-existing downtrend, not a dip buy opportunity")

    if consec_down >= 3:
        return (True, True, 0,
                f"FALLING KNIFE VETO: {consec_down} consecutive down days before today — "
                f"sustained distribution, not a single-day shock")

    # ── Penalty assessment ────────────────────────────────────────────────────
    penalty = 0.0

    if not was_above_ma20:
        penalty += 1.5
        reasons.append(f"Was below MA20 yesterday (mild prior weakness): -1.5pts")

    if consec_down == 2:
        penalty += 1.0
        reasons.append(f"2 consecutive down days before drop: -1.0pt")
    elif consec_down == 1:
        penalty += 0.5
        reasons.append(f"1 down day before drop: -0.5pt")

    # ── Capitulation bonuses (offset knife penalties) ─────────────────────────
    if oversold:
        penalty = max(0.0, penalty - 1.0)
        reasons.append(f"RSI {rsi:.0f} (oversold/panic) offsets knife risk: +1.0pt back")

    if capitulation:
        penalty = max(0.0, penalty - 0.5)
        reasons.append(f"Volume {vol_ratio:.1f}x avg (capitulation selling): +0.5pt back")

    is_knife = penalty > 0
    reason   = "; ".join(reasons) if reasons else "Clean single-day shock on healthy stock"
    return (is_knife, False, round(penalty, 2), reason)


def score_dip_buy(symbol: str, research: dict) -> dict:
    """
    Special scoring path for stocks down ≥15% in a single day.
    MA signals are ignored — price will always be bearish after a shock drop.

    Two-gate system:
      1. Falling knife filter: was the stock STRONG before today?
         - Hard veto if below MA50 yesterday or 3+ consecutive down days
         - Penalty if below MA20 or 1-2 prior down days
         - Penalty reduced if RSI is oversold or volume shows capitulation

      2. Conviction scoring (10 pts, gate ≥ 8.0):
         Fundamentals quality  0–4 pts  (analyst targets, buy ratings, rev growth)
         News catalyst type    0–3 pts  (temporary event = opportunity; fraud = veto)
         Macro regime          0–1.5 pts
         Portfolio / cash      0–2 pts
         Falling knife penalty  deducted from total
    """
    sym_data   = research.get("symbols", {}).get(symbol, {})
    news       = sym_data.get("news", [])
    funds      = sym_data.get("fundamentals", {})
    technicals = sym_data.get("technicals", {})
    dip_pct    = abs(sym_data.get("dip_pct_change", 0))

    if "error" in sym_data:
        return {"symbol": symbol, "recommendation": "VETO", "score": 0.0,
                "dip_buy_candidate": True, "rationale": f"Data error: {sym_data['error']}",
                "criteria": {}}

    # ── Gate 1: Structural news veto ─────────────────────────────────────────
    for item in news[:10]:
        headline = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        for kw in DIP_BUY_STRUCTURAL_KW:
            if kw in headline:
                return {
                    "symbol": symbol, "recommendation": "VETO", "score": 0.0,
                    "dip_buy_candidate": True,
                    "rationale": f"DIP VETO — structural red flag: '{kw}' in news. "
                                 f"Not a dip — potential business collapse.",
                    "criteria": {"structural_veto": True, "trigger": kw}
                }

    # ── Gate 2: Falling knife check ───────────────────────────────────────────
    is_knife, hard_veto, knife_penalty, knife_reason = check_falling_knife(technicals, dip_pct)
    if hard_veto:
        return {
            "symbol": symbol, "recommendation": "VETO", "score": 0.0,
            "dip_buy_candidate": True,
            "rationale": knife_reason,
            "criteria": {"falling_knife_veto": True, "knife_reason": knife_reason}
        }

    # ── Fundamentals score (0–4) ─────────────────────────────────────────────
    fund_score = 0.0
    fund_notes = []

    analyst_target = funds.get("analyst_target")
    current_price  = funds.get("current_price") or sym_data.get("dip_price", 0)
    buy_count      = funds.get("analyst_buy_count", 0) or 0
    rev_growth     = funds.get("revenue_growth")

    try:
        if analyst_target and current_price:
            upside = (float(str(analyst_target).replace(",", "")) /
                      float(str(current_price).replace(",", "")) - 1) * 100
            if upside >= 60:
                fund_score += 2.0
                fund_notes.append(f"Analyst target = {upside:.0f}% upside")
            elif upside >= 30:
                fund_score += 1.0
                fund_notes.append(f"Analyst target = {upside:.0f}% upside")
            elif upside >= 10:
                fund_score += 0.5
                fund_notes.append(f"Analyst target = {upside:.0f}% upside")
    except (ValueError, TypeError):
        pass

    if buy_count >= 10:
        fund_score += 1.5
        fund_notes.append(f"{buy_count} analyst buy ratings")
    elif buy_count >= 5:
        fund_score += 1.0
        fund_notes.append(f"{buy_count} analyst buy ratings")
    elif buy_count >= 2:
        fund_score += 0.5
        fund_notes.append(f"{buy_count} analyst buy ratings")

    try:
        if rev_growth:
            rg = float(str(rev_growth).replace("%", "").replace("x", ""))
            if rg > 0.20:
                fund_score += 0.5
                fund_notes.append(f"Revenue growing {rev_growth}")
    except (ValueError, TypeError):
        pass

    fund_score = round(min(fund_score, 4.0), 2)
    fund_note  = "; ".join(fund_notes) if fund_notes else "Insufficient fundamental data"

    # ── News catalyst score (0–3) ─────────────────────────────────────────────
    news_score = 2.0
    news_notes = []
    for item in news[:5]:
        headline = (item.get("headline", "") + " " + item.get("summary", "")).lower()
        for kw in DIP_BUY_TEMPORARY_KW:
            if kw in headline:
                news_score = max(0.5, news_score - 0.5)
                news_notes.append(f"Temporary catalyst: {kw}")
                break
    if not news_notes:
        news_notes.append("No confirmed catalyst — may be sympathy selling or macro fear")
    news_note = "; ".join(news_notes)

    # ── Macro + Portfolio ─────────────────────────────────────────────────────
    m_score, m_note = score_macro(research)
    p_score, p_note = score_portfolio(research, symbol)
    if "CASH BELOW 20%" in p_note:
        return {"symbol": symbol, "recommendation": "VETO", "score": 0.0,
                "dip_buy_candidate": True,
                "rationale": "Cash below 20% floor — cannot take dip position",
                "criteria": {}}

    # ── Final score (knife penalty deducted last) ─────────────────────────────
    raw_total = fund_score + news_score + m_score + p_score
    total     = round(max(0.0, min(raw_total - knife_penalty, 10.0)), 2)
    rec       = "APPROVE" if total >= DIP_BUY_MIN_SCORE else "VETO"

    knife_label = (
        f"Clean entry (no knife risk)"          if knife_penalty == 0 else
        f"Knife risk penalty: -{knife_penalty}pt"
    )

    return {
        "symbol":            symbol,
        "recommendation":    rec,
        "score":             total,
        "dip_buy_candidate": True,
        "dip_buy_approved":  rec == "APPROVE",
        "dip_pct_drop":      dip_pct,
        "score_breakdown": {
            "fundamentals":    f"{fund_score}/4.0",
            "news_catalyst":   f"{news_score}/3.0",
            "macro":           f"{m_score}/{W_MACRO}",
            "portfolio":       f"{p_score}/{W_PORTFOLIO}",
            "knife_penalty":   f"-{knife_penalty}",
        },
        "criteria": {
            "fundamentals":    {"score": fund_score,      "note": fund_note},
            "news_catalyst":   {"score": news_score,      "note": news_note},
            "macro":           {"score": m_score,         "note": m_note},
            "portfolio":       {"score": p_score,         "note": p_note},
            "falling_knife":   {"penalty": knife_penalty, "note": f"{knife_label}. {knife_reason}"},
        },
        "rationale": (
            f"DIP BUY — stock down {dip_pct:.0f}% today. "
            f"Knife check: {knife_reason}. "
            f"Fundamentals: {fund_note}. News: {news_note}. "
            f"Macro: {m_note}. "
            f"Raw score {raw_total:.1f} − knife penalty {knife_penalty} = {total}/10 "
            f"(gate ≥{DIP_BUY_MIN_SCORE}). {rec}."
        ),
    }


# ── Master Scoring Function ───────────────────────────────────────────────────

def score_symbol(symbol: str, research: dict) -> dict:
    """
    Run all criteria for one symbol. Returns full judge output dict.
    Dip buy candidates (25%+ single-day drop) are routed to score_dip_buy()
    which uses fundamentals-first scoring instead of MA signals.
    """
    sym_data   = research.get("symbols", {}).get(symbol, {})
    technicals = sym_data.get("technicals", {})
    news       = sym_data.get("news", [])

    # Route dip buy candidates to fundamentals-first scoring path
    if sym_data.get("dip_buy_candidate"):
        return score_dip_buy(symbol, research)

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
    earnings_veto, earnings_reason = check_earnings_veto(news, symbol, research)
    if earnings_veto:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0, "rationale": earnings_reason,
            "criteria": {"earnings_veto": True}
        }

    # Volatility regime veto (CLAUDE.md "Volatility Position Sizing").
    # Above VIX 35 the rule is manage-existing-only. This never fired before
    # because the VIX was fetched each morning and then discarded.
    _intel = research.get("intelligence") or {}
    _vix   = _intel.get("vix")
    if isinstance(_vix, (int, float)) and _vix > VIX_NO_NEW_POSITIONS:
        return {
            "symbol": symbol, "recommendation": "VETO", "score": 0.0,
            "rationale": (f"VOLATILITY VETO: VIX {_vix:.1f} exceeds "
                          f"{VIX_NO_NEW_POSITIONS:.0f} — no new positions, "
                          f"manage existing only"),
            "criteria": {"volatility_veto": True, "vix": _vix},
        }

    # Chase veto (CLAUDE.md Entry Checklist items 1-2). research.py already
    # computes RSI(14) and MA20, but nothing enforced the "don't chase" rules:
    # entries were approved at 20-day highs on overbought RSI.
    chase_veto, chase_reason = check_chase_veto(technicals, symbol)
    if chase_veto:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0, "rationale": chase_reason,
            "criteria": {"chase_veto": True, "note": chase_reason},
        }

    # Determine if this is a discovery or watchlist stock
    is_discovery = sym_data.get("source", "watchlist") in DISCOVERY_SOURCES

    # Speculative status drives the two gates below and the executor's stop tier.
    _funds = sym_data.get("fundamentals", {}) or {}
    _cap_b = _funds.get("market_cap_b")
    is_speculative = is_discovery or (_cap_b is not None
                                      and _cap_b < SPECULATIVE_CAP_THRESHOLD)

    # Holiday week veto for speculative names — thin liquidity, erratic moves.
    if is_speculative:
        _in_holiday, _holiday = is_holiday_week()
        if _in_holiday:
            return {
                "symbol": symbol, "recommendation": "VETO", "score": 0.0,
                "rationale": (f"HOLIDAY_WEEK: {_holiday} this week — no "
                              f"speculative trades in low-liquidity weeks"),
                "criteria": {"holiday_veto": True, "holiday": _holiday},
            }

    # Concentration cap: at most two speculative positions at once.
    if is_speculative:
        _spec = count_speculative_positions(research)
        if _spec >= 2:
            return {
                "symbol": symbol, "recommendation": "VETO", "score": 0.0,
                "rationale": (f"SECTOR_CONCENTRATION: already {_spec} "
                              f"speculative positions open (max 2)"),
                "criteria": {"concentration_veto": True, "speculative_open": _spec},
            }

    # Score each criterion, clamped to its declared weight.
    # The individual scorers were written against an older 3.0/1.5-point scale and
    # still return values above the current weights (technical up to 3.0 vs
    # W_TECHNICAL 2.0, macro and volatility up to 1.5 vs 1.0). Summing them raw
    # inflated every total by as much as 2.0 points and pushed unvetted discovery
    # stocks over the 8.5 gate. Clamp at the source so the 10-point scale is real.
    t_score, t_note = _clamp(score_technical(technicals),  W_TECHNICAL)
    _hints = load_learned_hints()
    _news_w = W_NEWS * _hints["applied"].get("news_weight_mult", 1.0)
    n_score, n_note = _clamp(score_news(news, sym_data),   _news_w)
    m_score, m_note = _clamp(score_macro(research),        W_MACRO)
    p_score, p_note = _clamp(score_portfolio(research, symbol), W_PORTFOLIO)
    v_score, v_note = _clamp(score_volatility(research),   W_VOLATILITY)
    q_score, q_note, q_hard_veto = score_quality(
        sym_data, is_discovery, symbol,
        (research.get("intelligence") or {}).get("insider_buy_symbols"),
    )  # already clamped

    # Hard veto: micro-cap discovery stock
    if q_hard_veto:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0,
            "rationale": f"Quality veto: {q_note}",
            "criteria": {"quality": {"score": 0.0, "note": q_note, "veto": True}}
        }

    # Hard veto: cash below floor
    if "CASH BELOW 20%" in p_note:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": 0.0,
            "rationale": "Cash reserve below 20% floor — hard veto",
            "criteria": {
                "technical":  {"score": t_score, "note": t_note},
                "news":       {"score": n_score, "note": n_note},
                "macro":      {"score": m_score, "note": m_note},
                "portfolio":  {"score": p_score, "note": p_note},
                "volatility": {"score": v_score, "note": v_note},
                "quality":    {"score": q_score, "note": q_note},
            }
        }

    # Hard veto: signal is SELL
    if technicals.get("signal") in ("STRONG_SELL", "WEAK_SELL"):
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": t_score,
            "rationale": "Sell signal present — not a buy candidate",
            "criteria": {
                "technical":  {"score": t_score, "note": t_note},
                "news":       {"score": n_score, "note": n_note},
                "macro":      {"score": m_score, "note": m_note},
                "portfolio":  {"score": p_score, "note": p_note},
                "volatility": {"score": v_score, "note": v_note},
                "quality":    {"score": q_score, "note": q_note},
            }
        }

    total = round(min(t_score + n_score + m_score + p_score + v_score + q_score,
                      MAX_TOTAL_SCORE), 2)
    is_avg_down = "AVG_DOWN candidate" in p_note

    # Hard veto: unvetted discovery stock with no quality evidence at all.
    if is_discovery and q_score < DISCOVERY_MIN_QUALITY:
        return {
            "symbol": symbol, "recommendation": "VETO",
            "score": total,
            "approval_gate": SCORE_GATE_DISCOVERY,
            "is_discovery": True,
            "rationale": (
                f"Discovery quality veto: scored {q_score}/{W_QUALITY} on quality "
                f"(minimum {DISCOVERY_MIN_QUALITY}). {q_note}"
            ),
            "criteria": {
                "technical":  {"score": t_score, "note": t_note},
                "news":       {"score": n_score, "note": n_note},
                "macro":      {"score": m_score, "note": m_note},
                "portfolio":  {"score": p_score, "note": p_note},
                "volatility": {"score": v_score, "note": v_note},
                "quality":    {"score": q_score, "note": q_note, "veto": True},
            },
        }

    # Source-aware approval threshold, with a learned tightening if one applies.
    gate = SCORE_GATE_DISCOVERY if is_discovery else SCORE_GATE_WATCHLIST
    _learned_gate = _hints["applied"].get("watchlist_gate")
    if _learned_gate and not is_discovery:
        gate = max(gate, _learned_gate)

    if total >= gate:
        rec = "APPROVE"
    elif total >= 5.0:
        rec = "HOLD"
    else:
        rec = "VETO"

    # Avg-down requires stronger conviction regardless of source
    if is_avg_down and total < 8.0:
        rec = "VETO"

    source_note = f"Source: {'discovery' if is_discovery else 'watchlist'} | Gate: {gate}"
    rationale = (
        f"Technical: {t_note}. "
        f"News: {n_note}. "
        f"Macro: {m_note}. "
        f"Portfolio: {p_note}. "
        f"Volatility: {v_note}. "
        f"Quality: {q_note}. "
        f"{source_note}."
    )

    return {
        "symbol": symbol,
        "recommendation": rec,
        "score": total,
        "approval_gate": gate,
        # Passed to the executor so the Citadel VIX sizing rule is actually
        # applied to share counts, not just mentioned in a rationale string.
        "size_multiplier": _intel.get("size_multiplier", 1.0),
        "learned_hints": _hints,
        "vix": _intel.get("vix"),
        "is_discovery": is_discovery,
        "is_speculative": is_speculative,
        "avg_down_candidate": is_avg_down,
        "score_breakdown": {
            "technical":  f"{t_score}/{W_TECHNICAL}",
            "news":       f"{n_score}/{W_NEWS}",
            "macro":      f"{m_score}/{W_MACRO}",
            "portfolio":  f"{p_score}/{W_PORTFOLIO}",
            "volatility": f"{v_score}/{W_VOLATILITY}",
            "quality":    f"{q_score}/{W_QUALITY}",
        },
        "criteria": {
            "technical":  {"score": t_score, "note": t_note},
            "news":       {"score": n_score, "note": n_note},
            "macro":      {"score": m_score, "note": m_note},
            "portfolio":  {"score": p_score, "note": p_note},
            "volatility": {"score": v_score, "note": v_note},
            "quality":    {"score": q_score, "note": q_note},
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

    # Load positions for avg-down eligibility checks
    positions_by_sym = {p["symbol"]: p for p in research.get("positions", [])}

    # Judge buy candidates (STRONG_BUY/WEAK_BUY) AND existing positions that may avg-down
    for sym, data in symbols_data.items():
        if sym == "SPY":  # SPY is macro ref, not a trade candidate
            continue
        tech   = data.get("technicals", {})
        signal = tech.get("signal", "HOLD")
        is_held       = sym in positions_by_sym
        is_dip        = data.get("dip_buy_candidate", False)
        if signal not in ("STRONG_BUY", "WEAK_BUY") and not is_held and not is_dip:
            continue

        scored = score_symbol(sym, research)
        results["scores"][sym] = scored

        if scored["recommendation"] == "APPROVE":
            results["approved"].append(sym)
        elif scored["recommendation"] == "HOLD":
            results["held"].append(sym)
        else:
            results["vetoed"].append(sym)
        # Log the gate used so the markdown report is transparent
        scored["_gate_used"] = scored.get("approval_gate",
            SCORE_GATE_DISCOVERY if scored.get("is_discovery") else SCORE_GATE_WATCHLIST)

    # Save judge output
    out_path = os.path.join(JOURNAL_DIR, f"{today}-judge.json")
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)

    # Write human-readable markdown report
    md_lines = [f"# Judge Report — {today}\n",
                f"**Approved**: {', '.join(results['approved']) or 'None'}",
                f"**Held**: {', '.join(results['held']) or 'None'}",
                f"**Vetoed**: {', '.join(results['vetoed']) or 'None'}\n"]

    # Learned hints are reported every run, applied or not. A change to trading
    # behaviour must be visible in the artefact, not buried in a JSON file.
    _h = load_learned_hints()
    md_lines.append("**Learned hints (alpaca_equities)**: "
                    + (", ".join(f"{k}={v}" for k, v in _h["applied"].items())
                       if _h["applied"] else "none applied"))
    for _n in _h["notes"]:
        md_lines.append(f"  - {_n}")
    md_lines.append("\n---\n")
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
