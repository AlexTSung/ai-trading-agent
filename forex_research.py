#!/usr/bin/env python3
"""
forex_research.py — Deep per-pair research for forex_judge.py.
Computes all technical + carry + session signals.

Usage:
  python forex_research.py EUR_USD        # Research a single pair
  python forex_research.py session        # Research all candidates from today's scan
  python forex_research.py read EUR_USD   # Print today's research for a pair
"""

import os
import sys
import json
import math
from datetime import date, datetime, timezone

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "forex")
os.makedirs(JOURNAL_DIR, exist_ok=True)

# Import shared helpers from screener
from forex_screener import (
    get_candles, get_current_price, parse_candles,
    compute_sma, compute_ema, compute_atr, compute_rsi, compute_macd,
    compute_momentum_score, compute_carry_signal, compute_spread_pips,
    is_good_session, get_pip_size, get_open_trades, get_account_summary,
    OANDA_ENV, _check_auth,
)


# ── HP Filter (from PDF §8.1) ─────────────────────────────────────────────────
def hp_filter(prices, lam=1600):
    """
    Hodrick-Prescott filter. Returns (trend, cycle).
    lam=1600 for quarterly; for daily FX use lam=6,250,000; for H4 use ~100,000.
    """
    n = len(prices)
    if n < 5:
        return prices, [0]*n

    # Simple iterative HP via linear algebra (numpy-free implementation)
    # Build (I + lam * D2'D2) x = y where D2 is second-difference matrix
    # Use tridiagonal solver
    a = [0.0] * n
    b = [0.0] * n
    c = [0.0] * n
    d = prices[:]

    # Construct band matrix for HP filter
    lam2 = lam
    for i in range(n):
        b[i] = 1.0
        if i == 0 or i == n-1:
            b[i] += lam2
        elif i == 1 or i == n-2:
            b[i] += 5 * lam2
        else:
            b[i] += 6 * lam2
        if i > 0:
            a[i] = -2 * lam2 if i > 1 else -lam2
        if i < n-1:
            c[i] = -2 * lam2 if i < n-2 else -lam2

    # Thomas algorithm (tridiagonal solve)
    c_prime = [0.0] * n
    d_prime = [0.0] * n
    c_prime[0] = c[0] / b[0]
    d_prime[0] = d[0] / b[0]
    for i in range(1, n):
        denom = b[i] - a[i] * c_prime[i-1]
        if abs(denom) < 1e-12:
            denom = 1e-12
        c_prime[i] = c[i] / denom if i < n-1 else 0
        d_prime[i] = (d[i] - a[i] * d_prime[i-1]) / denom

    trend = [0.0] * n
    trend[n-1] = d_prime[n-1]
    for i in range(n-2, -1, -1):
        trend[i] = d_prime[i] - c_prime[i] * trend[i+1]

    cycle = [prices[i] - trend[i] for i in range(n)]
    return trend, cycle


def compute_hp_ma_signal(closes, lam=100000, fast=10, slow=30):
    """
    HP filter + dual MA crossover (from PDF §8.1).
    Returns: 'LONG', 'SHORT', or 'NEUTRAL'
    """
    if len(closes) < slow + 5:
        return "NEUTRAL", None, None

    trend, _ = hp_filter(closes, lam=lam)

    ma_fast = sum(trend[-fast:]) / fast
    ma_slow = sum(trend[-slow:]) / slow

    if ma_fast > ma_slow:
        return "LONG", ma_fast, ma_slow
    elif ma_fast < ma_slow:
        return "SHORT", ma_fast, ma_slow
    return "NEUTRAL", ma_fast, ma_slow


# ── Momentum + Carry Combo (from PDF §8.4) ────────────────────────────────────
def compute_combo_weight(momentum_ret, carry_ret, lookback=20):
    """
    Optimal momentum+carry combo weights (eqs 451-452 from PDF).
    Returns (w_momentum, w_carry) or (0.5, 0.5) if insufficient data.
    """
    if len(momentum_ret) < lookback or len(carry_ret) < lookback:
        return 0.5, 0.5

    mom = momentum_ret[-lookback:]
    car = carry_ret[-lookback:]

    s1 = (sum((r - sum(mom)/len(mom))**2 for r in mom) / len(mom)) ** 0.5
    s2 = (sum((r - sum(car)/len(car))**2 for r in car) / len(car)) ** 0.5

    mean_m = sum(mom) / len(mom)
    mean_c = sum(car) / len(car)
    cov = sum((mom[i] - mean_m) * (car[i] - mean_c) for i in range(len(mom))) / len(mom)
    rho = cov / (s1 * s2) if s1 * s2 > 0 else 0

    denom = s1**2 + s2**2 - 2 * s1 * s2 * rho
    if denom < 1e-10:
        return 0.5, 0.5

    w1 = (s2**2 - s1 * s2 * rho) / denom
    w2 = (s1**2 - s1 * s2 * rho) / denom

    # Normalize to [0,1]
    total = abs(w1) + abs(w2)
    if total > 0:
        w1 = abs(w1) / total
        w2 = abs(w2) / total
    else:
        w1, w2 = 0.5, 0.5

    return round(w1, 3), round(w2, 3)


# ── Support & Resistance (Pivot Points, from PDF §3.14) ───────────────────────
def compute_pivots(prev_high, prev_low, prev_close):
    pivot     = (prev_high + prev_low + prev_close) / 3
    resistance = 2 * pivot - prev_low
    support    = 2 * pivot - prev_high
    return round(pivot, 5), round(resistance, 5), round(support, 5)


# ── Bollinger Bands ────────────────────────────────────────────────────────────
def compute_bollinger(closes, period=20, num_std=2.0):
    if len(closes) < period:
        return None, None, None
    sma = sum(closes[-period:]) / period
    std = (sum((c - sma)**2 for c in closes[-period:]) / period) ** 0.5
    upper = sma + num_std * std
    lower = sma - num_std * std
    return round(upper, 5), round(sma, 5), round(lower, 5)


# ── Market regime: Ranging vs Trending (proven principle) ────────────────────
def detect_market_regime(highs, lows, closes, atr, lookback=20):
    """
    Determine if price is ranging (in a cluster) or trending.
    Ranging:  use ATR × 3 for SL, wait for price to reach opposite end of range
    Trending: use ATR × 2 for SL, wait for pullback before entry

    Returns dict with regime, recommended_stop_mult, entry_advice
    """
    if len(closes) < lookback or not atr:
        return {"regime": "UNKNOWN", "stop_mult": 2.0, "entry_advice": "wait"}

    recent_h = highs[-lookback:]
    recent_l = lows[-lookback:]
    recent_c = closes[-lookback:]

    range_high = max(recent_h)
    range_low  = min(recent_l)
    range_size = range_high - range_low
    current    = closes[-1]

    # ADX-style: compare price movement to ATR
    avg_move = sum(abs(recent_c[i] - recent_c[i-1]) for i in range(1, len(recent_c))) / (len(recent_c) - 1)
    trend_strength = avg_move / atr if atr > 0 else 0

    # Momentum across lookback
    momentum = (closes[-1] - closes[-lookback]) / closes[-lookback] if closes[-lookback] != 0 else 0

    if trend_strength > 0.6 or abs(momentum) > 0.015:
        regime = "TRENDING"
        stop_mult = 2.0
        if momentum > 0:
            entry_advice = "wait_for_pullback_to_support"
        else:
            entry_advice = "wait_for_pullback_to_resistance"
    else:
        regime = "RANGING"
        stop_mult = 3.0  # wider SL for ranging — more room to breathe
        # Where is price in the range?
        range_pct = (current - range_low) / range_size if range_size > 0 else 0.5
        if range_pct > 0.7:
            entry_advice = "at_top_of_range_good_for_short"
        elif range_pct < 0.3:
            entry_advice = "at_bottom_of_range_good_for_long"
        else:
            entry_advice = "wait_for_price_to_reach_range_extremes"

    return {
        "regime": regime,
        "stop_mult": stop_mult,
        "trend_strength": round(trend_strength, 3),
        "momentum_20": round(momentum * 100, 3),
        "range_high": round(range_high, 5),
        "range_low": round(range_low, 5),
        "range_pct": round((current - range_low) / range_size, 3) if range_size > 0 else 0.5,
        "entry_advice": entry_advice,
    }


def check_breakeven_management(entry_price, current_price, stop_price, direction):
    """
    Principle: when in profit by ~2× stop distance, move SL to breakeven + a few pips.
    Returns suggested new SL if breakeven trigger is met, else None.
    """
    if not all([entry_price, current_price, stop_price]):
        return None

    stop_dist = abs(entry_price - stop_price)
    if direction == "LONG":
        profit_pips = current_price - entry_price
        if profit_pips >= stop_dist * 2:
            return round(entry_price + stop_dist * 0.05, 5)  # entry + tiny buffer for swap
    elif direction == "SHORT":
        profit_pips = entry_price - current_price
        if profit_pips >= stop_dist * 2:
            return round(entry_price - stop_dist * 0.05, 5)
    return None


# ── Triangular Arbitrage Check (from PDF §8.5) ────────────────────────────────
def check_triangular_arb(prices):
    """
    Check USD→EUR→GBP→USD triangular arbitrage opportunity.
    prices dict must have bid/ask for EUR_USD, GBP_USD, EUR_GBP.
    Returns profit_factor > 1 means opportunity exists.
    """
    required = ["EUR_USD", "GBP_USD", "EUR_GBP"]
    if not all(k in prices for k in required):
        return None

    try:
        eurusd_bid = prices["EUR_USD"]["bid"]
        gbpusd_ask = prices["GBP_USD"]["ask"]
        eurgbp_bid = prices["EUR_GBP"]["bid"]

        # USD → EUR → GBP → USD
        # 1 USD → (1/ask_eurusd) EUR → (1/ask_eurusd)*bid_eurgbp GBP → *bid_gbpusd USD
        # Using bid/ask for realistic execution
        eurusd_ask = prices["EUR_USD"]["ask"]
        gbpusd_bid = prices["GBP_USD"]["bid"]
        eurgbp_ask = prices["EUR_GBP"]["ask"]

        # Path 1: USD → EUR → GBP → USD
        profit1 = (1/eurusd_ask) * eurgbp_bid * gbpusd_bid
        # Path 2: USD → GBP → EUR → USD
        profit2 = (1/gbpusd_ask) * (1/eurgbp_ask) * eurusd_bid

        return {
            "path1_USD_EUR_GBP_USD": round(profit1, 6),
            "path2_USD_GBP_EUR_USD": round(profit2, 6),
            "arb_opportunity": profit1 > 1.0001 or profit2 > 1.0001,
        }
    except Exception:
        return None


# ── Full Research ─────────────────────────────────────────────────────────────
def research_pair(pair, scan_data=None):
    """
    Full technical + fundamental research for one pair.
    scan_data: optional dict from forex_screener.scan_pair()
    """
    # Multi-timeframe candles
    candles_h4  = get_candles(pair, granularity="H4",  count=100)
    candles_d   = get_candles(pair, granularity="D",   count=100)
    candles_h1  = get_candles(pair, granularity="H1",  count=50)

    closes_h4, highs_h4, lows_h4, opens_h4 = parse_candles(candles_h4)
    closes_d,  highs_d,  lows_d,  opens_d  = parse_candles(candles_d)
    closes_h1, highs_h1, lows_h1, opens_h1 = parse_candles(candles_h1)

    price_data = get_current_price(pair)
    if not price_data or "error" in price_data:
        return {"pair": pair, "error": "price_fetch_failed"}

    current = price_data["mid"]
    pip = get_pip_size(pair)

    # ── H4 Technicals ──────────────────────────────────────────────────────
    ma20_h4  = compute_sma(closes_h4, 20)
    ma50_h4  = compute_sma(closes_h4, 50)
    ema20_h4 = compute_ema(closes_h4, 20)
    atr14_h4 = compute_atr(highs_h4, lows_h4, closes_h4, 14)
    rsi14_h4 = compute_rsi(closes_h4, 14)
    rsi2_h4  = compute_rsi(closes_h4, 2)
    macd_l, macd_s, macd_hist = compute_macd(closes_h4)
    momentum_h4 = compute_momentum_score(closes_h4, 20)

    # IBS on H4
    ibs_h4 = 0.5
    if highs_h4 and lows_h4 and highs_h4[-1] != lows_h4[-1]:
        ibs_h4 = (closes_h4[-1] - lows_h4[-1]) / (highs_h4[-1] - lows_h4[-1])

    # Bollinger Bands (H4)
    bb_upper, bb_mid, bb_lower = compute_bollinger(closes_h4, 20, 2.0)

    # ── Daily Technicals ───────────────────────────────────────────────────
    ma20_d  = compute_sma(closes_d, 20)
    ma50_d  = compute_sma(closes_d, 50)
    ma200_d = compute_sma(closes_d, 200) if len(closes_d) >= 200 else None
    atr14_d = compute_atr(highs_d, lows_d, closes_d, 14)
    rsi14_d = compute_rsi(closes_d, 14)
    momentum_d = compute_momentum_score(closes_d, 20)

    # ── HP Filter + MA Signal (H4, λ=100,000 for intraday) ────────────────
    hp_signal, hp_ma_fast, hp_ma_slow = compute_hp_ma_signal(closes_h4, lam=100000, fast=10, slow=30)

    # ── Pivot Points (using yesterday's daily OHLC) ────────────────────────
    pivot = resistance = support = None
    if len(closes_d) >= 2:
        pivot, resistance, support = compute_pivots(highs_d[-2], lows_d[-2], closes_d[-2])

    # ── Carry ──────────────────────────────────────────────────────────────
    carry_pct = compute_carry_signal(pair)
    carry_signal = "LONG" if carry_pct > 0.5 else ("SHORT" if carry_pct < -0.5 else "NEUTRAL")

    # ── Momentum + Carry Combo Weight (eq 451-452) ─────────────────────────
    # Use daily returns as proxy for momentum returns
    mom_returns = [(closes_d[i] - closes_d[i-1]) / closes_d[i-1]
                   for i in range(1, len(closes_d))] if len(closes_d) > 1 else []
    # Carry returns proxy: constant carry_pct/365 per day
    carry_returns = [carry_pct / 36500] * len(mom_returns)
    w_mom, w_carry = compute_combo_weight(mom_returns, carry_returns, lookback=20)

    # ── Direction consensus ────────────────────────────────────────────────
    long_signals = sum([
        momentum_h4 > 0.02,
        momentum_d > 0.01,
        (ma20_h4 > ma50_h4) if (ma20_h4 and ma50_h4) else False,
        current > ma20_h4 if ma20_h4 else False,
        macd_l > macd_s if (macd_l and macd_s) else False,
        carry_signal == "LONG",
        hp_signal == "LONG",
        rsi14_h4 < 50 if rsi14_h4 else False,  # not overbought
        current > pivot if pivot else False,
    ])
    short_signals = sum([
        momentum_h4 < -0.02,
        momentum_d < -0.01,
        (ma20_h4 < ma50_h4) if (ma20_h4 and ma50_h4) else False,
        current < ma20_h4 if ma20_h4 else False,
        macd_l < macd_s if (macd_l and macd_s) else False,
        carry_signal == "SHORT",
        hp_signal == "SHORT",
        rsi14_h4 > 50 if rsi14_h4 else False,
        current < pivot if pivot else False,
    ])

    direction = "NEUTRAL"
    if long_signals >= 4:
        direction = "LONG"
    elif short_signals >= 4:
        direction = "SHORT"

    # ── Market regime (ranging vs trending) — drives SL multiplier ───────
    regime = detect_market_regime(highs_h4, lows_h4, closes_h4, atr14_h4, lookback=20)
    stop_mult = regime.get("stop_mult", 2.0)  # 2.0 trending, 3.0 ranging

    # 1H regime check for entry timing
    atr14_h1 = compute_atr(highs_h1, lows_h1, closes_h1, 14)
    regime_h1 = detect_market_regime(highs_h1, lows_h1, closes_h1, atr14_h1, lookback=20) if atr14_h1 else {}

    # ── Stop-loss and take-profit levels (ATR × 2 or 3, give room to breathe) ──
    stop_pips  = None
    tp1_pips   = None
    tp2_pips   = None
    stop_price = None
    tp1_price  = None
    tp2_price  = None

    if atr14_h4:
        stop_pips = round(stop_mult * atr14_h4 / pip)
        tp1_pips  = round(stop_pips * 2.0)   # 1:2 R:R — "sweet spot" per principles doc; partial close here
        tp2_pips  = round(stop_pips * 4.0)   # 1:4 R:R — runner target; Stage 2 trail locks in gain

        if direction == "LONG":
            stop_price = round(current - stop_mult * atr14_h4, 5)
            tp1_price  = round(current + 2.0 * stop_mult * atr14_h4, 5)
            tp2_price  = round(current + 4.0 * stop_mult * atr14_h4, 5)
        elif direction == "SHORT":
            stop_price = round(current + stop_mult * atr14_h4, 5)
            tp1_price  = round(current - 2.0 * stop_mult * atr14_h4, 5)
            tp2_price  = round(current - 4.0 * stop_mult * atr14_h4, 5)

    # ── Breakeven management check ─────────────────────────────────────────
    breakeven_sl = check_breakeven_management(current, current, stop_price, direction)

    # ── Near support/resistance check ─────────────────────────────────────
    near_support    = (current <= support * 1.002) if support else False
    near_resistance = (current >= resistance * 0.998) if resistance else False

    result = {
        "pair": pair,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "environment": OANDA_ENV,
        "direction": direction,
        "long_signals": long_signals,
        "short_signals": short_signals,

        # Price
        "current_price": round(current, 5),
        "bid": price_data["bid"],
        "ask": price_data["ask"],
        "spread_pips": round(compute_spread_pips(pair, price_data["bid"], price_data["ask"]), 1),

        # H4 Technicals
        "ma20_h4": round(ma20_h4, 5) if ma20_h4 else None,
        "ma50_h4": round(ma50_h4, 5) if ma50_h4 else None,
        "ema20_h4": round(ema20_h4, 5) if ema20_h4 else None,
        "atr14_h4": round(atr14_h4, 6) if atr14_h4 else None,
        "atr14_h4_pips": round(atr14_h4 / pip, 1) if atr14_h4 else None,
        "rsi14_h4": round(rsi14_h4, 1) if rsi14_h4 else None,
        "rsi2_h4": round(rsi2_h4, 1) if rsi2_h4 else None,
        "ibs_h4": round(ibs_h4, 3),
        "macd_line": round(macd_l, 6) if macd_l else None,
        "macd_signal": round(macd_s, 6) if macd_s else None,
        "macd_hist": round(macd_hist, 6) if macd_hist else None,
        "macd_bullish": (macd_l > macd_s) if (macd_l and macd_s) else None,
        "momentum_h4": round(momentum_h4, 4),
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,

        # Daily Technicals
        "ma20_d": round(ma20_d, 5) if ma20_d else None,
        "ma50_d": round(ma50_d, 5) if ma50_d else None,
        "ma200_d": round(ma200_d, 5) if ma200_d else None,
        "atr14_d": round(atr14_d, 6) if atr14_d else None,
        "rsi14_d": round(rsi14_d, 1) if rsi14_d else None,
        "momentum_d": round(momentum_d, 4),

        # HP Filter
        "hp_signal": hp_signal,
        "hp_ma_fast": round(hp_ma_fast, 5) if hp_ma_fast else None,
        "hp_ma_slow": round(hp_ma_slow, 5) if hp_ma_slow else None,

        # Carry
        "carry_pct_annual": round(carry_pct, 2),
        "carry_signal": carry_signal,
        "combo_weight_momentum": w_mom,
        "combo_weight_carry": w_carry,

        # Pivots
        "pivot": pivot,
        "resistance": resistance,
        "support": support,
        "near_support": near_support,
        "near_resistance": near_resistance,

        # Trade levels (ATR × 2 trending / × 3 ranging — give room to breathe)
        "stop_pips": stop_pips,
        "tp1_pips": tp1_pips,
        "tp2_pips": tp2_pips,
        "stop_price": stop_price,
        "tp1_price": tp1_price,
        "tp2_price": tp2_price,
        "stop_atr_mult": stop_mult,

        # Market regime (ranging vs trending)
        "regime": regime.get("regime"),
        "regime_stop_mult": stop_mult,
        "regime_entry_advice": regime.get("entry_advice"),
        "regime_range_high": regime.get("range_high"),
        "regime_range_low": regime.get("range_low"),
        "regime_range_pct": regime.get("range_pct"),
        "regime_h1": regime_h1.get("regime") if regime_h1 else None,
        "regime_h1_advice": regime_h1.get("entry_advice") if regime_h1 else None,

        # Breakeven management
        "breakeven_sl_suggested": breakeven_sl,

        # Session
        "session_ok": is_good_session(),
    }

    # Save
    today = date.today().isoformat()
    path = os.path.join(JOURNAL_DIR, f"{today}_{pair}_research.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)

    return result


def research_all_candidates():
    """Research all candidates from today's scan."""
    _check_auth()
    today = date.today().isoformat()
    scan_path = os.path.join(JOURNAL_DIR, f"{today}_scan.json")

    if not os.path.exists(scan_path):
        print(json.dumps({"error": f"No scan found for {today}. Run forex_screener.py scan first."}))
        sys.exit(1)

    with open(scan_path) as f:
        scan = json.load(f)

    candidates = scan.get("candidates", [])
    if not candidates:
        print(json.dumps({"message": "No candidates from today's scan", "researched": []}))
        return []

    results = []
    for c in candidates:
        pair = c["pair"]
        research = research_pair(pair, scan_data=c)
        results.append(research)

    output = {
        "session_date": today,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "results": results,
    }

    path = os.path.join(JOURNAL_DIR, f"{today}_research.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))
    return results


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "session"

    if cmd == "session":
        research_all_candidates()

    elif len(sys.argv) >= 2 and sys.argv[1].upper().replace("/", "_") in [p.replace("/","_") for p in
        ["EUR_USD","GBP_USD","USD_JPY","USD_CHF","AUD_USD","USD_CAD","NZD_USD",
         "EUR_GBP","EUR_JPY","GBP_JPY","AUD_JPY"]]:
        pair = sys.argv[1].upper().replace("/", "_")
        _check_auth()
        result = research_pair(pair)
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        pair = sys.argv[2].upper() if len(sys.argv) > 2 else None
        today = date.today().isoformat()
        if pair:
            path = os.path.join(JOURNAL_DIR, f"{today}_{pair}_research.json")
        else:
            path = os.path.join(JOURNAL_DIR, f"{today}_research.json")
        if os.path.exists(path):
            with open(path) as f:
                print(f.read())
        else:
            print(json.dumps({"error": f"No research found at {path}"}))

    else:
        print(f"Unknown command. Usage: forex_research.py [EUR_USD | session | read EUR_USD]")
        sys.exit(1)
