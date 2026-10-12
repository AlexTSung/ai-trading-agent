#!/usr/bin/env python3
"""
forex_judge.py — Multi-factor scoring engine for Forex trades.
Scores each candidate 0–10. Gate: ≥7.0 required to trade; ≥8.0 for larger sizing.

Usage:
  python forex_judge.py session          # Score all researched candidates
  python forex_judge.py EUR_USD          # Score a single pair
  python forex_judge.py read             # Print today's scored candidates
  python forex_judge.py read EUR_USD     # Print today's score for one pair
"""

import os
import sys
import json
from datetime import date, datetime, timezone

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "forex")
os.makedirs(JOURNAL_DIR, exist_ok=True)

from forex_screener import _check_auth, OANDA_ENV

# ── Score gates (G7 guardrail) ────────────────────────────────────────────────
GATE_TRADE       = 7.0   # Minimum to place a trade
GATE_LARGE_SIZE  = 8.0   # Minimum for 1.5× sizing
GATE_BEST_ONLY   = 9.0   # Elite — full 2% equity allocation

# ── Factor weights ─────────────────────────────────────────────────────────────
# Total weight = 10.0 (scores normalize to 0-10 scale)
# v1.3.0: rebalanced — carry raised (2.0→2.5), technical lowered (2.0→1.5).
# Data showed technical was over-weighted (learned: 5.4) on only 6 trades — overfit.
# Best-performing pairs (NZD/JPY 3W/0L, EUR/JPY 75%WR) are carry+momentum driven.
W_CARRY       = 2.5   # Carry differential (hold income) — raised from 2.0
W_MOMENTUM    = 2.0   # Price momentum + trend alignment — lowered from 2.5
W_TECHNICAL   = 1.5   # RSI, MACD, IBS, Bollinger, pivots — lowered from 2.0
W_SESSION     = 1.0   # Market session quality
W_VOLATILITY  = 1.5   # ATR regime + spread check
W_RISK_REWARD = 1.5   # Stop/TP geometry — raised from 1.0 (R:R is core edge)

TOTAL_WEIGHT = W_CARRY + W_MOMENTUM + W_TECHNICAL + W_SESSION + W_VOLATILITY + W_RISK_REWARD


def score_carry(r: dict) -> tuple[float, dict]:
    """
    Carry score (0-1).
    - Carry ≥ 2%: full score
    - Carry ≥ 1%: partial
    - Carry 0-1%: low
    - Carry negative: penalty
    Signal must match direction.
    """
    carry = r.get("carry_pct_annual", 0)
    direction = r.get("direction", "NEUTRAL")
    carry_signal = r.get("carry_signal", "NEUTRAL")

    raw = 0.0
    if carry >= 2.5:
        raw = 1.0
    elif carry >= 1.5:
        raw = 0.8
    elif carry >= 0.5:
        raw = 0.6
    elif carry >= 0:
        raw = 0.4
    elif carry >= -0.5:
        raw = 0.3
    else:
        raw = 0.1

    # Direction alignment
    if carry_signal == direction and direction != "NEUTRAL":
        raw = min(1.0, raw + 0.1)
    elif carry_signal != "NEUTRAL" and carry_signal != direction:
        raw = max(0.0, raw - 0.2)

    return raw, {"carry_pct": carry, "carry_signal": carry_signal, "raw_score": round(raw, 3)}


def score_momentum(r: dict) -> tuple[float, dict]:
    """
    Momentum score (0-1): multi-timeframe trend alignment.
    - H4 momentum direction
    - Daily momentum direction
    - HP filter MA signal
    - MA20 > MA50 (H4)
    - Above/below MA20 (H4)
    - Combo weight (if carry+momentum both positive)
    """
    direction = r.get("direction", "NEUTRAL")
    if direction == "NEUTRAL":
        return 0.0, {"reason": "direction_neutral"}

    signals = 0
    details = {}

    # H4 momentum
    mom_h4 = r.get("momentum_h4", 0)
    if direction == "LONG" and mom_h4 > 0.01:
        signals += 1
        details["h4_momentum"] = "bullish"
    elif direction == "SHORT" and mom_h4 < -0.01:
        signals += 1
        details["h4_momentum"] = "bearish"

    # Daily momentum
    mom_d = r.get("momentum_d", 0)
    if direction == "LONG" and mom_d > 0.005:
        signals += 1
        details["daily_momentum"] = "bullish"
    elif direction == "SHORT" and mom_d < -0.005:
        signals += 1
        details["daily_momentum"] = "bearish"

    # HP filter MA
    hp = r.get("hp_signal", "NEUTRAL")
    if hp == direction:
        signals += 1
        details["hp_signal"] = hp

    # MA alignment (H4)
    ma20 = r.get("ma20_h4")
    ma50 = r.get("ma50_h4")
    if ma20 and ma50:
        if direction == "LONG" and ma20 > ma50:
            signals += 1
            details["ma_cross"] = "bullish"
        elif direction == "SHORT" and ma20 < ma50:
            signals += 1
            details["ma_cross"] = "bearish"

    # Price vs MA20
    price = r.get("current_price", 0)
    if ma20:
        if direction == "LONG" and price > ma20:
            signals += 1
            details["above_ma20"] = True
        elif direction == "SHORT" and price < ma20:
            signals += 1
            details["below_ma20"] = True

    # Bonus: combo weight favors this direction
    w_mom = r.get("combo_weight_momentum", 0.5)
    if w_mom >= 0.6:
        signals += 1
        details["combo_weight"] = f"momentum_favored_{w_mom}"

    raw = min(1.0, signals / 5)
    return raw, {**details, "signals": signals, "raw_score": round(raw, 3)}


def score_technical(r: dict) -> tuple[float, dict]:
    """
    Technical score (0-1): RSI, MACD, IBS, Bollinger, pivots.
    """
    direction = r.get("direction", "NEUTRAL")
    if direction == "NEUTRAL":
        return 0.0, {"reason": "direction_neutral"}

    points = 0
    max_pts = 6
    details = {}

    rsi14 = r.get("rsi14_h4")
    rsi2  = r.get("rsi2_h4")
    ibs   = r.get("ibs_h4", 0.5)
    macd_b = r.get("macd_bullish")
    bb_upper = r.get("bb_upper")
    bb_lower = r.get("bb_lower")
    price    = r.get("current_price", 0)
    pivot    = r.get("pivot")
    support  = r.get("support")
    resistance = r.get("resistance")

    # RSI not extreme against us
    if rsi14:
        if direction == "LONG" and rsi14 < 65:
            points += 1
            details["rsi14"] = f"ok_at_{rsi14}"
        elif direction == "SHORT" and rsi14 > 35:
            points += 1
            details["rsi14"] = f"ok_at_{rsi14}"

    # RSI-2 mean reversion signal
    if rsi2:
        if direction == "LONG" and rsi2 < 30:
            points += 1
            details["rsi2"] = f"oversold_{rsi2}"
        elif direction == "SHORT" and rsi2 > 70:
            points += 1
            details["rsi2"] = f"overbought_{rsi2}"

    # MACD
    if macd_b is not None:
        if direction == "LONG" and macd_b:
            points += 1
            details["macd"] = "bullish"
        elif direction == "SHORT" and not macd_b:
            points += 1
            details["macd"] = "bearish"

    # IBS
    if direction == "LONG" and ibs < 0.3:
        points += 1
        details["ibs"] = f"oversold_{ibs}"
    elif direction == "SHORT" and ibs > 0.7:
        points += 1
        details["ibs"] = f"overbought_{ibs}"

    # Bollinger
    if bb_upper and bb_lower:
        if direction == "LONG" and price < (bb_lower * 1.001):
            points += 1
            details["bollinger"] = "near_lower_band"
        elif direction == "SHORT" and price > (bb_upper * 0.999):
            points += 1
            details["bollinger"] = "near_upper_band"

    # Pivot
    if pivot and support and resistance:
        if direction == "LONG" and price > pivot:
            points += 1
            details["pivot"] = "above_pivot"
        elif direction == "SHORT" and price < pivot:
            points += 1
            details["pivot"] = "below_pivot"

    raw = min(1.0, points / max_pts)
    return raw, {**details, "points": points, "raw_score": round(raw, 3)}


def score_session(r: dict) -> tuple[float, dict]:
    """
    Session quality score (0-1).
    London + NY overlap = 1.0; single session = 0.7; off-session = 0.3
    """
    from forex_screener import is_good_session
    session_ok = r.get("session_ok", False)

    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    london = 8 <= hour < 16
    new_york = 13 <= hour < 21
    overlap = london and new_york

    if overlap:
        raw = 1.0
        quality = "london_ny_overlap"
    elif london or new_york:
        raw = 0.75
        quality = "london" if london else "new_york"
    elif session_ok:
        raw = 0.5
        quality = "active_session"
    else:
        raw = 0.2
        quality = "off_session"

    return raw, {"session_quality": quality, "utc_hour": hour, "raw_score": round(raw, 3)}


def score_volatility(r: dict) -> tuple[float, dict]:
    """
    Volatility regime + spread cost score (0-1).
    - ATR in healthy range (not too low, not too extreme)
    - Spread within acceptable bounds (G8)
    """
    atr_pips = r.get("atr14_h4_pips")
    spread   = r.get("spread_pips", 999)
    pair     = r.get("pair", "")

    # Pair-specific max spread thresholds (G8: skip if > 3× average)
    MAX_SPREAD = {
        "EUR_USD": 2.0, "GBP_USD": 3.0, "USD_JPY": 2.5,
        "USD_CHF": 3.0, "AUD_USD": 3.0, "USD_CAD": 3.5, "NZD_USD": 4.0,
        "EUR_GBP": 2.5, "EUR_JPY": 3.5, "GBP_JPY": 5.0, "AUD_JPY": 4.0,
            "NZD_JPY": 4.5, "CAD_JPY": 4.5,
    }
    max_spread = MAX_SPREAD.get(pair, 4.0)

    spread_ok = spread <= max_spread
    spread_penalty = 0.0 if spread_ok else 0.5

    raw = 0.5  # baseline
    details = {"spread_pips": spread, "spread_ok": spread_ok, "atr_pips": atr_pips}

    if atr_pips:
        # Healthy ATR ranges by pair (typical daily pip ranges)
        HEALTHY_ATR = {
            "EUR_USD": (30, 80), "GBP_USD": (50, 130), "USD_JPY": (40, 100),
            "USD_CHF": (35, 85), "AUD_USD": (35, 90), "USD_CAD": (40, 100),
            "NZD_USD": (35, 85), "EUR_GBP": (20, 60), "EUR_JPY": (60, 150),
            "GBP_JPY": (80, 200), "AUD_JPY": (55, 140),
            "NZD_JPY": (50, 130), "CAD_JPY": (50, 130),
        }
        lo, hi = HEALTHY_ATR.get(pair, (30, 150))
        if lo <= atr_pips <= hi:
            raw = 1.0
            details["atr_regime"] = "healthy"
        elif atr_pips < lo * 0.35:
            # Genuinely dead market — don't trade
            raw = 0.3
            details["atr_regime"] = "too_quiet"
        elif atr_pips < lo * 0.60:
            # Quiet but normal for overnight/Tokyo session — still tradeable
            raw = 0.55
            details["atr_regime"] = "quiet_ok"
        elif atr_pips > hi * 1.5:
            raw = 0.4
            details["atr_regime"] = "too_volatile"
        else:
            raw = 0.7
            details["atr_regime"] = "borderline"

    raw = max(0.0, raw - spread_penalty)
    details["raw_score"] = round(raw, 3)
    return raw, details


def score_risk_reward(r: dict) -> tuple[float, dict]:
    """
    Risk:reward geometry score (0-1).
    - R:R ≥ 2:1 is target (TP1 = 2× stop)
    - Checks that stop and TP are set, sensible distances
    """
    stop_pips = r.get("stop_pips")
    tp1_pips  = r.get("tp1_pips")
    tp2_pips  = r.get("tp2_pips")
    spread    = r.get("spread_pips", 1.5)
    atr_pips  = r.get("atr14_h4_pips")

    if not stop_pips or not tp1_pips:
        return 0.4, {"reason": "no_stop_or_tp"}

    rr = tp1_pips / stop_pips if stop_pips > 0 else 0
    details = {"rr_ratio": round(rr, 2), "stop_pips": stop_pips, "tp1_pips": tp1_pips}

    if rr >= 2.5:
        raw = 1.0
    elif rr >= 2.0:
        raw = 0.85
    elif rr >= 1.5:
        raw = 0.65
    elif rr >= 1.0:
        raw = 0.4
    else:
        raw = 0.2

    # Spread as % of stop (high spread = worse actual R:R)
    if stop_pips > 0:
        spread_pct = spread / stop_pips
        if spread_pct > 0.3:
            raw = max(0.0, raw - 0.2)
            details["spread_warning"] = f"spread_is_{round(spread_pct*100)}pct_of_stop"

    details["raw_score"] = round(raw, 3)
    return raw, details


def _load_learned_config() -> dict:
    """Load forex_config.json if it exists, else return empty dict."""
    config_path = os.path.join(ROOT, "forex_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def judge_pair(r: dict) -> dict:
    """
    Run all 6 factors, compute weighted score, apply gate logic.
    Uses learned weights from forex_config.json when available.
    Returns full score breakdown with recommendation.
    """
    if "error" in r:
        return {"pair": r.get("pair"), "error": r["error"], "score": 0.0}

    direction = r.get("direction", "NEUTRAL")
    if direction == "NEUTRAL":
        return {
            "pair": r.get("pair"),
            "direction": "NEUTRAL",
            "score": 0.0,
            "verdict": "SKIP",
            "reason": "no_directional_signal",
        }

    # Load learned config — use learned weights if available
    cfg = _load_learned_config()
    learned_weights = cfg.get("weights", {})
    w_carry       = learned_weights.get("carry",       W_CARRY)
    w_momentum    = learned_weights.get("momentum",    W_MOMENTUM)
    w_technical   = learned_weights.get("technical",   W_TECHNICAL)
    w_session     = learned_weights.get("session",     W_SESSION)
    w_volatility  = learned_weights.get("volatility",  W_VOLATILITY)
    w_risk_reward = learned_weights.get("risk_reward", W_RISK_REWARD)
    total_w = w_carry + w_momentum + w_technical + w_session + w_volatility + w_risk_reward

    # Deprioritized pairs — score capped at gate so they don't trade
    pair = r.get("pair", "")
    deprioritized = cfg.get("deprioritized_pairs", [])
    is_deprioritized = pair in deprioritized

    # Direction bias — apply penalty if agent is losing on this direction
    direction_bias = cfg.get("direction_bias", {})
    direction_penalty = 0.0
    if direction == "LONG"  and direction_bias.get("long_wr")  is not None:
        if direction_bias["long_wr"] < 0.35 and direction_bias.get("sample", 0) >= 3:
            direction_penalty = -0.5   # penalize LONG when long trades losing badly
    if direction == "SHORT" and direction_bias.get("short_wr") is not None:
        if direction_bias["short_wr"] < 0.35 and direction_bias.get("sample", 0) >= 3:
            direction_penalty = -0.5   # penalize SHORT when short trades losing badly

    carry_s,   carry_d   = score_carry(r)
    momentum_s, momentum_d = score_momentum(r)
    technical_s, technical_d = score_technical(r)
    session_s, session_d = score_session(r)
    vol_s,     vol_d     = score_volatility(r)
    rr_s,      rr_d      = score_risk_reward(r)

    # Weighted sum → normalize to 0-10
    weighted = (
        carry_s     * w_carry +
        momentum_s  * w_momentum +
        technical_s * w_technical +
        session_s   * w_session +
        vol_s       * w_volatility +
        rr_s        * w_risk_reward
    )
    score = round((weighted / total_w) * 10 + direction_penalty, 2)

    # Hard disqualifiers (G-series guardrails)
    disqualifiers = []
    # Deprioritized pairs: raised gate (7.5) + half-size instead of hard block.
    # USD_CHF has 0W/2L history but is scoring 7.8+; let it earn its way back
    # with extra caution rather than being permanently frozen.
    deprioritized_gate = 7.5
    is_deprioritized_gate = is_deprioritized and score < deprioritized_gate
    if is_deprioritized_gate:
        disqualifiers.append(f"DEPRIORITIZED_{pair}_needs_{deprioritized_gate}_got_{score}")

    # G10: technical score gate — need meaningful technical confirmation
    if technical_s < 0.40:
        disqualifiers.append(f"G10_technical_score_{round(technical_s,2)}_below_0.40_minimum")

    # G12: momentum gate — high carry alone is not enough; price must confirm direction
    # Exception: JPY carry pairs (>3% annual) get a lower bar since carry income compounds
    jpy_carry_exception = "JPY" in pair and r.get("carry_pct_annual", 0) > 3.0
    if not jpy_carry_exception and momentum_s < 0.40:
        disqualifiers.append(f"G12_momentum_score_{round(momentum_s,2)}_below_0.40_minimum")

    # G11: H1 regime conflict — don't go LONG at top of range or SHORT at bottom
    regime_h1_advice = r.get("regime_h1_advice", "")
    if direction == "LONG" and "good_for_short" in (regime_h1_advice or "").lower():
        disqualifiers.append(f"G11_H1_regime_conflict_LONG_at_range_top")
    if direction == "SHORT" and "good_for_long" in (regime_h1_advice or "").lower():
        disqualifiers.append(f"G11_H1_regime_conflict_SHORT_at_range_bottom")

    if not r.get("spread_pips") is None:
        spread = r.get("spread_pips", 0)
        pair = r.get("pair", "")
        MAX_SPREAD = {
            "EUR_USD": 2.0, "GBP_USD": 3.0, "USD_JPY": 2.5,
            "USD_CHF": 3.0, "AUD_USD": 3.0, "USD_CAD": 3.5, "NZD_USD": 4.0,
            "EUR_GBP": 2.5, "EUR_JPY": 3.5, "GBP_JPY": 5.0, "AUD_JPY": 4.0,
            "NZD_JPY": 4.5, "CAD_JPY": 4.5,
        }
        if spread > MAX_SPREAD.get(pair, 4.0):
            disqualifiers.append(f"G8_spread_too_wide_{spread}pips")

    if not r.get("session_ok", True):
        disqualifiers.append("G9_off_session")

    # JPY carry pairs get a lower gate — carry income offsets lower momentum scores
    carry_pct = r.get("carry_pct_annual", 0)
    is_jpy_carry = "JPY" in pair and carry_pct > 0.5
    effective_gate = 6.5 if is_jpy_carry else GATE_TRADE

    # Verdict
    if disqualifiers:
        verdict = "DISQUALIFIED"
        reason = "; ".join(disqualifiers)
    elif score >= GATE_BEST_ONLY:
        verdict = "STRONG_BUY" if direction == "LONG" else "STRONG_SELL"
        reason = f"score_{score}_elite"
    elif score >= GATE_LARGE_SIZE:
        verdict = "BUY" if direction == "LONG" else "SELL"
        reason = f"score_{score}_large_size"
    elif score >= effective_gate:
        verdict = "BUY" if direction == "LONG" else "SELL"
        reason = f"score_{score}_standard_size" + ("_jpy_carry_gate_6.5" if is_jpy_carry and score < GATE_TRADE else "")
    else:
        verdict = "SKIP"
        reason = f"score_{score}_below_gate_{effective_gate}"

    # Size multiplier — deprioritized pairs that clear the 7.5 gate get half-size
    if verdict in ("STRONG_BUY", "STRONG_SELL"):
        size_multiplier = 2.0
    elif score >= GATE_LARGE_SIZE:
        size_multiplier = 1.5
    elif score >= effective_gate:
        size_multiplier = 1.0
    else:
        size_multiplier = 0.0
    if is_deprioritized and size_multiplier > 0:
        size_multiplier = min(size_multiplier, 0.5)  # half-size until 1st win

    return {
        "pair": r.get("pair"),
        "direction": direction,
        "score": score,
        "verdict": verdict,
        "reason": reason,
        "size_multiplier": size_multiplier,
        "environment": OANDA_ENV,
        "judged_at": datetime.now(timezone.utc).isoformat(),

        # Trade levels (pass through from research)
        "entry_price":  r.get("current_price"),
        "stop_price":   r.get("stop_price"),
        "tp1_price":    r.get("tp1_price"),
        "tp2_price":    r.get("tp2_price"),
        "stop_pips":    r.get("stop_pips"),
        "tp1_pips":     r.get("tp1_pips"),
        "atr14_pips":   r.get("atr14_h4_pips"),

        # Factor breakdown (weights reflect learned values)
        "factors": {
            "carry":       {"score": round(carry_s, 3),     "weight": round(w_carry, 2),       "detail": carry_d},
            "momentum":    {"score": round(momentum_s, 3),  "weight": round(w_momentum, 2),    "detail": momentum_d},
            "technical":   {"score": round(technical_s, 3), "weight": round(w_technical, 2),   "detail": technical_d},
            "session":     {"score": round(session_s, 3),   "weight": round(w_session, 2),     "detail": session_d},
            "volatility":  {"score": round(vol_s, 3),       "weight": round(w_volatility, 2),  "detail": vol_d},
            "risk_reward": {"score": round(rr_s, 3),        "weight": round(w_risk_reward, 2), "detail": rr_d},
        },
        "learned_weights_active": bool(learned_weights),
        "direction_penalty": direction_penalty,

        "disqualifiers": disqualifiers,
    }


def judge_session():
    """Judge all researched candidates from today's session."""
    _check_auth()
    today = date.today().isoformat()

    # Load today's research
    research_path = os.path.join(JOURNAL_DIR, f"{today}_research.json")
    if not os.path.exists(research_path):
        # Try individual research files
        import glob
        files = glob.glob(os.path.join(JOURNAL_DIR, f"{today}_*_research.json"))
        if not files:
            print(json.dumps({"error": f"No research found for {today}. Run forex_research.py first."}))
            sys.exit(1)
        research_list = []
        for f in files:
            with open(f) as fh:
                research_list.append(json.load(fh))
    else:
        with open(research_path) as f:
            data = json.load(f)
        research_list = data.get("results", [])

    if not research_list:
        print(json.dumps({"message": "No research to judge today", "verdicts": []}))
        return []

    verdicts = [judge_pair(r) for r in research_list]
    verdicts.sort(key=lambda v: v.get("score", 0), reverse=True)

    tradeable = [v for v in verdicts if v.get("verdict") not in ("SKIP", "DISQUALIFIED", "NEUTRAL")]

    output = {
        "session_date": today,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "environment": OANDA_ENV,
        "total_reviewed": len(verdicts),
        "tradeable": len(tradeable),
        "gate": GATE_TRADE,
        "verdicts": verdicts,
    }

    path = os.path.join(JOURNAL_DIR, f"{today}_verdicts.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print(f"\n{'='*60}")
    print(f"  FOREX JUDGE — {today} ({OANDA_ENV.upper()})")
    print(f"{'='*60}")
    print(f"  Reviewed: {len(verdicts)} pairs  |  Tradeable: {len(tradeable)}")
    print(f"  Gate: {GATE_TRADE} / 10\n")
    for v in verdicts:
        score   = v.get("score", 0)
        verdict = v.get("verdict", "")
        pair    = v.get("pair", "")
        dir_    = v.get("direction", "")
        tp1     = v.get("tp1_price") or "–"
        stop    = v.get("stop_price") or "–"
        flag    = "✓" if verdict not in ("SKIP", "DISQUALIFIED") else " "
        print(f"  {flag} {pair:12s} {dir_:6s}  {score:5.2f}  {verdict:20s}  stop={stop}  tp1={tp1}")
    print()

    return verdicts


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "session"

    KNOWN_PAIRS = [
        "EUR_USD","GBP_USD","USD_JPY","USD_CHF","AUD_USD","USD_CAD","NZD_USD",
        "EUR_GBP","EUR_JPY","GBP_JPY","AUD_JPY"
    ]

    if cmd == "session":
        judge_session()

    elif cmd.upper() in KNOWN_PAIRS:
        pair = cmd.upper()
        today = date.today().isoformat()
        research_path = os.path.join(JOURNAL_DIR, f"{today}_{pair}_research.json")
        if not os.path.exists(research_path):
            print(json.dumps({"error": f"No research for {pair} today. Run: python forex_research.py {pair}"}))
            sys.exit(1)
        with open(research_path) as f:
            r = json.load(f)
        verdict = judge_pair(r)
        print(json.dumps(verdict, indent=2))

    elif cmd == "read":
        today = date.today().isoformat()
        pair = sys.argv[2].upper() if len(sys.argv) > 2 else None
        if pair:
            path = os.path.join(JOURNAL_DIR, f"{today}_{pair}_research.json")
            if os.path.exists(path):
                with open(path) as f:
                    r = json.load(f)
                verdict = judge_pair(r)
                print(json.dumps(verdict, indent=2))
            else:
                print(json.dumps({"error": f"No research for {pair} on {today}"}))
        else:
            path = os.path.join(JOURNAL_DIR, f"{today}_verdicts.json")
            if os.path.exists(path):
                with open(path) as f:
                    print(f.read())
            else:
                print(json.dumps({"error": f"No verdicts found for {today}. Run: python forex_judge.py session"}))

    else:
        print(f"Unknown command. Usage: forex_judge.py [session | EUR_USD | read [EUR_USD]]")
        sys.exit(1)
