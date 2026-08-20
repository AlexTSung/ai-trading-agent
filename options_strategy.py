#!/usr/bin/env python3
"""
options_strategy.py — Options strategy selection and position construction.

Implements the volatility spread strategies from Natenberg Ch 7-8:
  - Iron Condor (short strangle + wing protection): sell premium, high IV rank
  - Short Strangle: sell OTM call + OTM put, uncapped risk, high IV rank
  - Long Straddle: buy ATM call + put, buy premium, low IV rank
  - Vertical Spreads: defined-risk directional plays
  - Calendar Spread: sell near-term, buy longer-term; long time spread

Strategy selection framework (Natenberg Ch 8 / Figure 8-19):
  IV Rank ≥ 50 + no direction → Iron Condor (sell premium, negative gamma/vega)
  IV Rank ≥ 50 + bullish       → Bull Put Spread (credit)
  IV Rank ≥ 50 + bearish       → Bear Call Spread (credit)
  IV Rank ≤ 25 + no direction  → Long Straddle / Strangle (buy premium)
  IV Rank 25-50                → Calendar Spread (long time spread)
"""

import math
from typing import Optional
from options_pricing import bsm_greeks, spread_greeks, bsm_price

RISK_FREE_RATE = 0.053


# ── Strategy builders ─────────────────────────────────────────────────────────

def build_iron_condor(S: float, T: float, sigma: float,
                      call_wing: float = 0.10, put_wing: float = 0.10,
                      wing_width: float = 0.05) -> dict:
    """
    Iron Condor: short OTM strangle + long OTM wing protection.

    Structure (from stock price S):
      Long put  at S*(1 - put_wing - wing_width)   [lower wing]
      Short put at S*(1 - put_wing)                [short put leg]
      Short call at S*(1 + call_wing)               [short call leg]
      Long call  at S*(1 + call_wing + wing_width)  [upper wing]

    Natenberg: iron condor = long strangle (wings) + short strangle (body).
    Equivalent to a short butterfly spread — profits if stock stays range-bound.

    Args:
        S: Current stock price
        T: Time to expiry in years
        sigma: Current IV estimate
        call_wing: % OTM for short call (0.10 = 10% OTM)
        put_wing:  % OTM for short put  (0.10 = 10% OTM)
        wing_width: additional % width for long wing protection

    Returns:
        Strategy dict with legs, net_credit, max_profit, max_loss, breakevens
    """
    r = RISK_FREE_RATE

    K_long_put   = round(S * (1 - put_wing - wing_width), 0)
    K_short_put  = round(S * (1 - put_wing), 0)
    K_short_call = round(S * (1 + call_wing), 0)
    K_long_call  = round(S * (1 + call_wing + wing_width), 0)

    # Prices per share
    lp  = bsm_price(S, K_long_put,   T, r, sigma, "put")
    sp  = bsm_price(S, K_short_put,  T, r, sigma, "put")
    sc  = bsm_price(S, K_short_call, T, r, sigma, "call")
    lc  = bsm_price(S, K_long_call,  T, r, sigma, "call")

    # Net credit received (positive = receive premium)
    net_credit = (sp - lp) + (sc - lc)

    # Max profit = net credit (stock stays between short strikes at expiry)
    # Max loss = wing width - net credit (stock moves beyond wing)
    max_profit = net_credit
    max_loss   = (K_short_put - K_long_put) - net_credit  # put spread width - credit

    # Breakevens
    be_lower = K_short_put  - net_credit
    be_upper = K_short_call + net_credit

    # Aggregate Greeks
    r_ = RISK_FREE_RATE
    legs = [
        {"S": S, "K": K_long_put,   "T": T, "r": r_, "sigma": sigma, "type": "put",  "qty": +1},
        {"S": S, "K": K_short_put,  "T": T, "r": r_, "sigma": sigma, "type": "put",  "qty": -1},
        {"S": S, "K": K_short_call, "T": T, "r": r_, "sigma": sigma, "type": "call", "qty": -1},
        {"S": S, "K": K_long_call,  "T": T, "r": r_, "sigma": sigma, "type": "call", "qty": +1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":    "iron_condor",
        "legs":        [
            {"type": "long_put",   "strike": K_long_put,   "price": round(lp, 2)},
            {"type": "short_put",  "strike": K_short_put,  "price": round(sp, 2)},
            {"type": "short_call", "strike": K_short_call, "price": round(sc, 2)},
            {"type": "long_call",  "strike": K_long_call,  "price": round(lc, 2)},
        ],
        "net_credit":  round(net_credit, 2),
        "max_profit":  round(max_profit, 2),
        "max_loss":    round(max_loss, 2),
        "be_lower":    round(be_lower, 2),
        "be_upper":    round(be_upper, 2),
        "profit_range_width": round(K_short_call - K_short_put, 0),
        "reward_risk": round(max_profit / max_loss, 3) if max_loss > 0 else None,
        "delta":       round(net_greeks["delta"], 4),
        "gamma":       round(net_greeks["gamma"], 4),
        "theta":       round(net_greeks["theta"], 4),
        "vega":        round(net_greeks["vega"],  4),
    }


def build_short_strangle(S: float, T: float, sigma: float,
                         call_otm: float = 0.10, put_otm: float = 0.10) -> dict:
    """
    Short Strangle: sell OTM call + sell OTM put. Undefined risk.

    Best for: high IV rank (≥50), expecting stock to stay range-bound.
    Risk: unlimited on call side if stock rallies sharply.

    Natenberg Ch 8: short strangle = ratio vertical spread category.
    Gamma negative, Theta positive, Vega negative.
    """
    r = RISK_FREE_RATE
    K_put  = round(S * (1 - put_otm), 0)
    K_call = round(S * (1 + call_otm), 0)

    put_price  = bsm_price(S, K_put,  T, r, sigma, "put")
    call_price = bsm_price(S, K_call, T, r, sigma, "call")
    net_credit = put_price + call_price

    be_lower = K_put  - net_credit
    be_upper = K_call + net_credit

    legs = [
        {"S": S, "K": K_put,  "T": T, "r": r, "sigma": sigma, "type": "put",  "qty": -1},
        {"S": S, "K": K_call, "T": T, "r": r, "sigma": sigma, "type": "call", "qty": -1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":   "short_strangle",
        "legs":       [
            {"type": "short_put",  "strike": K_put,  "price": round(put_price, 2)},
            {"type": "short_call", "strike": K_call, "price": round(call_price, 2)},
        ],
        "net_credit": round(net_credit, 2),
        "max_profit": round(net_credit, 2),
        "max_loss":   None,  # unlimited
        "be_lower":   round(be_lower, 2),
        "be_upper":   round(be_upper, 2),
        "delta":      round(net_greeks["delta"], 4),
        "gamma":      round(net_greeks["gamma"], 4),
        "theta":      round(net_greeks["theta"], 4),
        "vega":       round(net_greeks["vega"],  4),
    }


def build_long_straddle(S: float, T: float, sigma: float) -> dict:
    """
    Long Straddle: buy ATM call + ATM put. Unlimited upside.

    Best for: low IV rank (≤25), expecting large move in either direction.
    Natenberg Ch 8: backspread category. Gamma positive, Theta negative, Vega positive.
    """
    r = RISK_FREE_RATE
    K = round(S, 0)  # ATM strike

    call_price = bsm_price(S, K, T, r, sigma, "call")
    put_price  = bsm_price(S, K, T, r, sigma, "put")
    net_debit  = call_price + put_price

    be_lower = K - net_debit
    be_upper = K + net_debit

    legs = [
        {"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "type": "call", "qty": +1},
        {"S": S, "K": K, "T": T, "r": r, "sigma": sigma, "type": "put",  "qty": +1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":  "long_straddle",
        "legs":      [
            {"type": "long_call", "strike": K, "price": round(call_price, 2)},
            {"type": "long_put",  "strike": K, "price": round(put_price,  2)},
        ],
        "net_debit": round(net_debit, 2),
        "max_profit": None,  # unlimited
        "max_loss":   round(net_debit, 2),
        "be_lower":  round(be_lower, 2),
        "be_upper":  round(be_upper, 2),
        "delta":     round(net_greeks["delta"], 4),
        "gamma":     round(net_greeks["gamma"], 4),
        "theta":     round(net_greeks["theta"], 4),
        "vega":      round(net_greeks["vega"],  4),
    }


def build_bull_put_spread(S: float, T: float, sigma: float,
                          short_otm: float = 0.05, width: float = 0.05) -> dict:
    """
    Bull Put Spread: sell OTM put + buy further OTM put (credit spread).

    Best for: high IV rank, bullish bias. Defined risk.
    Net credit; profit if stock stays above short put strike.
    """
    r = RISK_FREE_RATE
    K_short = round(S * (1 - short_otm), 0)
    K_long  = round(S * (1 - short_otm - width), 0)

    sp = bsm_price(S, K_short, T, r, sigma, "put")
    lp = bsm_price(S, K_long,  T, r, sigma, "put")
    net_credit = sp - lp
    max_loss   = (K_short - K_long) - net_credit

    legs = [
        {"S": S, "K": K_short, "T": T, "r": r, "sigma": sigma, "type": "put", "qty": -1},
        {"S": S, "K": K_long,  "T": T, "r": r, "sigma": sigma, "type": "put", "qty": +1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":    "bull_put_spread",
        "legs":        [
            {"type": "short_put", "strike": K_short, "price": round(sp, 2)},
            {"type": "long_put",  "strike": K_long,  "price": round(lp, 2)},
        ],
        "net_credit":  round(net_credit, 2),
        "max_profit":  round(net_credit, 2),
        "max_loss":    round(max_loss, 2),
        "be_lower":    round(K_short - net_credit, 2),
        "reward_risk": round(net_credit / max_loss, 3) if max_loss > 0 else None,
        "delta":       round(net_greeks["delta"], 4),
        "gamma":       round(net_greeks["gamma"], 4),
        "theta":       round(net_greeks["theta"], 4),
        "vega":        round(net_greeks["vega"],  4),
    }


def build_bear_call_spread(S: float, T: float, sigma: float,
                            short_otm: float = 0.05, width: float = 0.05) -> dict:
    """
    Bear Call Spread: sell OTM call + buy further OTM call (credit spread).

    Best for: high IV rank, bearish bias. Defined risk.
    """
    r = RISK_FREE_RATE
    K_short = round(S * (1 + short_otm), 0)
    K_long  = round(S * (1 + short_otm + width), 0)

    sc = bsm_price(S, K_short, T, r, sigma, "call")
    lc = bsm_price(S, K_long,  T, r, sigma, "call")
    net_credit = sc - lc
    max_loss   = (K_long - K_short) - net_credit

    legs = [
        {"S": S, "K": K_short, "T": T, "r": r, "sigma": sigma, "type": "call", "qty": -1},
        {"S": S, "K": K_long,  "T": T, "r": r, "sigma": sigma, "type": "call", "qty": +1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":    "bear_call_spread",
        "legs":        [
            {"type": "short_call", "strike": K_short, "price": round(sc, 2)},
            {"type": "long_call",  "strike": K_long,  "price": round(lc, 2)},
        ],
        "net_credit":  round(net_credit, 2),
        "max_profit":  round(net_credit, 2),
        "max_loss":    round(max_loss, 2),
        "be_upper":    round(K_short + net_credit, 2),
        "reward_risk": round(net_credit / max_loss, 3) if max_loss > 0 else None,
        "delta":       round(net_greeks["delta"], 4),
        "gamma":       round(net_greeks["gamma"], 4),
        "theta":       round(net_greeks["theta"], 4),
        "vega":        round(net_greeks["vega"],  4),
    }


# ── Butterfly spread (Tony Saliba's core structure) ───────────────────────────

def build_butterfly_spread(S: float, T: float, sigma: float,
                            width: float = 0.05) -> dict:
    """
    Long Butterfly: buy 1 low strike + sell 2 middle (ATM) + buy 1 high strike.

    Tony Saliba's core structure from Market Wizards:
    - Positive theta (time works for you in flat market)
    - Limited, defined risk on both sides
    - Profits maximally when stock pins at the middle strike at expiry
    - Saliba: 'The butterfly gives you theta positive with capped risk'
    - Natenberg Ch 8: butterfly = ratio spread equivalent; profits from vol crush

    Args:
        S: Current stock price
        T: Time to expiry in years
        sigma: Current IV estimate
        width: % distance between wings (0.05 = 5% from center)
    """
    r = RISK_FREE_RATE

    K_low    = round(S * (1 - width), 0)
    K_middle = round(S, 0)             # ATM
    K_high   = round(S * (1 + width), 0)

    p_low    = bsm_price(S, K_low,    T, r, sigma, "call")
    p_mid    = bsm_price(S, K_middle, T, r, sigma, "call")
    p_high   = bsm_price(S, K_high,   T, r, sigma, "call")

    # Net debit: pay for wings, collect from two short ATM calls
    net_debit = p_low + p_high - 2 * p_mid
    net_debit = max(net_debit, 0.01)  # should be positive for standard butterfly

    max_profit = (K_middle - K_low) - net_debit  # at expiry, stock pins at K_middle
    max_loss   = net_debit                         # premium paid

    be_lower = K_low    + net_debit
    be_upper = K_high   - net_debit

    legs = [
        {"S": S, "K": K_low,    "T": T, "r": r, "sigma": sigma, "type": "call", "qty": +1},
        {"S": S, "K": K_middle, "T": T, "r": r, "sigma": sigma, "type": "call", "qty": -2},
        {"S": S, "K": K_high,   "T": T, "r": r, "sigma": sigma, "type": "call", "qty": +1},
    ]
    net_greeks = spread_greeks(legs)

    return {
        "strategy":    "butterfly",
        "legs":        [
            {"type": "long_call",   "strike": K_low,    "price": round(p_low, 2)},
            {"type": "short_call2", "strike": K_middle, "price": round(p_mid, 2)},
            {"type": "long_call",   "strike": K_high,   "price": round(p_high, 2)},
        ],
        "net_debit":   round(net_debit, 2),
        "max_profit":  round(max_profit, 2),
        "max_loss":    round(max_loss, 2),
        "be_lower":    round(be_lower, 2),
        "be_upper":    round(be_upper, 2),
        "reward_risk": round(max_profit / max_loss, 3) if max_loss > 0 else None,
        "delta":       round(net_greeks["delta"], 4),
        "gamma":       round(net_greeks["gamma"], 4),
        "theta":       round(net_greeks["theta"], 4),
        "vega":        round(net_greeks["vega"],  4),
    }


# ── Pre-trade scenario analysis (Gary Bielfeldt / Tony Saliba) ─────────────────

def scenario_analysis(strat: dict, stock_moves: list[float] = None) -> dict:
    """
    Pre-trade contingency planning.

    Gary Bielfeldt (Market Wizards): 'You need to develop a plan of your strategies
    for various contingencies. That way you won't get swayed by every news item.'

    Tony Saliba: 'I always do my daily homework — recap the day, figure out what-ifs,
    anticipate scenarios and make contingency plans.'

    Computes P&L across a range of stock price moves at expiry.
    Returns worst-case, base-case, and best-case P&L for the strategy.

    Args:
        strat: Strategy dict from build_*() functions
        stock_moves: List of % moves to simulate (default: -20% to +20%)
    """
    if stock_moves is None:
        stock_moves = [-0.20, -0.15, -0.10, -0.05, 0.0, +0.05, +0.10, +0.15, +0.20]

    S         = strat.get("underlying", 0)
    max_profit = strat.get("max_profit", 0) or 0
    max_loss   = strat.get("max_loss", 0) or 0
    strategy   = strat.get("strategy", "")
    net_credit = strat.get("net_credit", 0) or 0
    net_debit  = strat.get("net_debit", 0) or 0

    scenarios = []
    for move in stock_moves:
        S_new = S * (1 + move)
        label = f"{move*100:+.0f}%"

        # Simplified expiry P&L estimates (per contract, per share)
        if strategy == "iron_condor":
            be_lower = strat.get("be_lower", 0)
            be_upper = strat.get("be_upper", 999999)
            if be_lower <= S_new <= be_upper:
                # Stock within breakevens — profit zone
                pl = net_credit * 0.7  # approximate (time value still exists)
                outcome = "profit"
            elif S_new < be_lower:
                # Below lower breakeven — loss growing
                distance = be_lower - S_new
                pl = max(net_credit - distance, -max_loss)
                outcome = "loss"
            else:
                distance = S_new - be_upper
                pl = max(net_credit - distance, -max_loss)
                outcome = "loss"

        elif strategy in ("bull_put_spread", "bear_call_spread"):
            be = strat.get("be_lower") or strat.get("be_upper", S)
            if strategy == "bull_put_spread":
                pl = net_credit if S_new > be else -max_loss
            else:
                pl = net_credit if S_new < be else -max_loss
            outcome = "profit" if pl > 0 else "loss"

        elif strategy == "long_straddle":
            be_lower = strat.get("be_lower", S)
            be_upper = strat.get("be_upper", S)
            if S_new < be_lower or S_new > be_upper:
                move_size = abs(S_new - S)
                pl = move_size - net_debit
                outcome = "profit"
            else:
                pl = -net_debit * 0.5  # partial loss (time value remains)
                outcome = "loss"

        elif strategy == "butterfly":
            K_low  = strat["legs"][0]["strike"]
            K_mid  = strat["legs"][1]["strike"]
            K_high = strat["legs"][2]["strike"]
            be_lower = strat.get("be_lower", K_low)
            be_upper = strat.get("be_upper", K_high)
            if be_lower <= S_new <= be_upper:
                dist_from_center = abs(S_new - K_mid)
                half_width = K_mid - K_low
                pl = max_profit * (1 - dist_from_center / half_width) if half_width else 0
                outcome = "profit"
            else:
                pl = -net_debit
                outcome = "loss"

        else:
            pl = 0.0
            outcome = "unknown"

        scenarios.append({
            "move":    label,
            "S_new":   round(S_new, 2),
            "pl":      round(pl * 100, 2),  # per contract (×100 shares)
            "outcome": outcome,
        })

    worst = min(scenarios, key=lambda x: x["pl"])
    best  = max(scenarios, key=lambda x: x["pl"])
    flat  = next((s for s in scenarios if s["move"] == " +0%"), scenarios[len(scenarios)//2])

    return {
        "strategy":  strategy,
        "symbol":    strat.get("symbol", ""),
        "scenarios": scenarios,
        "worst_case": worst,
        "best_case":  best,
        "flat_case":  flat,
        "max_defined_loss_per_contract": round((max_loss or net_debit) * 100, 2),
    }


# ── Strategy selector ─────────────────────────────────────────────────────────

def select_strategy(screen_result: dict) -> Optional[dict]:
    """
    Select optimal strategy based on IV rank + trend signal.

    Decision matrix (Natenberg Ch 8):
      IVR ≥ 50, neutral → Iron Condor (defined risk premium selling)
      IVR ≥ 50, bullish → Bull Put Spread (credit, bullish bias)
      IVR ≥ 50, bearish → Bear Call Spread (credit, bearish bias)
      IVR ≤ 25, any     → Long Straddle (buy premium for big move)
      IVR 25-50         → Skip (no strong edge)

    For paper trading: prefer defined-risk strategies (iron condor over short strangle).
    """
    S      = screen_result["price"]
    sigma  = screen_result["current_iv"] / 100.0  # convert from % to decimal
    ivr    = screen_result["iv_rank"]
    trend  = screen_result["trend"]
    dte    = screen_result["dte"]
    T      = (dte * 252 / 365) / 252.0  # trading-day-adjusted years

    if T <= 0 or sigma <= 0 or S <= 0:
        return None

    # Bielfeldt: poker strategy — only play high-percentage hands.
    # Raise threshold from 50 to 60 for selling premium; below 20 for buying.
    # This means we pass on more trades, but the ones we take have stronger edge.
    if ivr >= 60:
        if trend == "bullish":
            strat = build_bull_put_spread(S, T, sigma)
        elif trend == "bearish":
            strat = build_bear_call_spread(S, T, sigma)
        else:
            # Default to iron condor — best defined-risk premium seller
            strat = build_iron_condor(S, T, sigma)
    elif ivr <= 20:
        # Only buy vol when it is truly compressed (IVR ≤ 20, not 25)
        strat = build_long_straddle(S, T, sigma)
    else:
        return None  # Neutral zone — Bielfeldt: fold the marginal hand

    # Enrich with screening context
    strat["symbol"]       = screen_result["symbol"]
    strat["underlying"]   = S
    strat["iv_rank"]      = ivr
    strat["iv_pct"]       = screen_result["iv_pct"]
    strat["current_iv"]   = screen_result["current_iv"]
    strat["hv30"]         = screen_result["hv30"]
    strat["trend"]        = trend
    strat["expiry"]       = screen_result["expiry_target"]
    strat["dte"]          = dte
    strat["T_years"]      = round(T, 4)

    # Position sizing hint: risk max 1% of account per trade
    # For iron condor: 1 contract = 100 shares
    strat["position_sizing_note"] = (
        "Size: max 1 contract per $5,000 account equity. "
        "Scale up as win rate validates the strategy."
    )

    return strat


# ── Risk checks ───────────────────────────────────────────────────────────────

def passes_risk_filters(strat: dict, account_equity: float = 25000.0) -> tuple[bool, str]:
    """
    Basic risk gate before placing any order.

    Rules:
      1. Max loss per trade ≤ 2% of account equity
      2. Reward/risk ≥ 0.20 (at least $0.20 credit per $1 at risk)
      3. Must have positive theta (time working for us on credit strategies)
      4. DTE between 21 and 60 (sweet spot for theta decay vs gamma risk)
    """
    max_loss = strat.get("max_loss")
    if max_loss is None:
        return False, "Undefined max loss — use defined-risk structure in paper trading"

    max_loss_per_contract = max_loss * 100  # per contract (100 shares)
    max_allowed = account_equity * 0.02

    if max_loss_per_contract > max_allowed:
        return False, f"Max loss ${max_loss_per_contract:.0f} > 2% account (${max_allowed:.0f})"

    # Bielfeldt: raise the minimum reward/risk bar — only play strong hands
    rr = strat.get("reward_risk")
    if rr is not None and rr < 0.25:
        return False, f"Reward/risk {rr:.2f} < 0.25 minimum (Bielfeldt: fold weak hands)"

    theta = strat.get("theta", 0)
    strategy = strat.get("strategy", "")
    if strategy in ("bull_put_spread", "bear_call_spread", "iron_condor", "short_strangle"):
        if theta >= 0:
            return False, f"Expected positive theta for premium-selling strategy, got {theta:.4f}"

    dte = strat.get("dte", 0)
    if dte < 21:
        return False, f"DTE {dte} < 21 — too close to expiry (gamma risk)"
    if dte > 60:
        return False, f"DTE {dte} > 60 — theta decay too slow"

    return True, "OK"


if __name__ == "__main__":
    # Demo: price an iron condor on a $150 stock
    S, T, sigma = 150.0, 38/252, 0.25

    print("=== Iron Condor ===")
    ic = build_iron_condor(S, T, sigma)
    for k, v in ic.items():
        if k != "legs":
            print(f"  {k}: {v}")
    print("  legs:")
    for leg in ic["legs"]:
        print(f"    {leg}")

    print("\n=== Short Strangle ===")
    ss = build_short_strangle(S, T, sigma)
    for k, v in ss.items():
        if k != "legs":
            print(f"  {k}: {v}")

    print("\n=== Long Straddle ===")
    ls = build_long_straddle(S, T, 0.15)  # low vol scenario
    for k, v in ls.items():
        if k != "legs":
            print(f"  {k}: {v}")
