#!/usr/bin/env python3
"""
options_strategy.py — Options strategy selection and position construction.

Implements the volatility spread strategies from Natenberg Ch 7-8:
  - Iron Condor (short strangle + wing protection): sell premium, high IV rank
  - Short Strangle: sell OTM call + OTM put, uncapped risk, high IV rank
  - Long Straddle: buy ATM call + put, buy premium, low IV rank
  - Vertical Spreads: defined-risk directional plays
  - Calendar Spread: sell near-term, buy longer-term; long time spread

Strategy selection as implemented (see select_strategy for the live thresholds):
  IV Rank ≥ 60 + no direction → Iron Condor (sell premium, negative gamma/vega)
  IV Rank ≥ 60 + bullish      → Bull Put Spread (credit)
  IV Rank ≥ 60 + bearish      → Bear Call Spread (credit)
  IV Rank ≤ 20 + no direction → Long Straddle (buy premium)
  IV Rank 21-59               → No position

Note: calendar spreads are described in Natenberg but are NOT implemented here,
and the 21-59 band takes no position rather than selling a time spread. Entry is
gated to 21-60 DTE; short_strangle is built but always rejected by
passes_risk_filters because its risk is undefined.
"""

import math
from typing import Optional
from options_pricing import bsm_greeks, spread_greeks, bsm_price

RISK_FREE_RATE = 0.053

# ── Entry window ──────────────────────────────────────────────────────────────
# Published premium-selling practice enters at 30-45 DTE and manages out by 21.
# 21-60 is the accepted band: inside 21 days gamma accelerates sharply, beyond
# 60 theta decay is too slow to pay for the risk held.
DTE_MIN_ENTRY = 21
DTE_MAX_ENTRY = 60
DTE_TARGET    = 45   # preferred entry when the chain offers a choice

# ── Delta-based strike selection ──────────────────────────────────────────────
# Strikes were chosen by a fixed 10% OTM offset, which ignores volatility
# entirely: 10% is inside one standard deviation on a high-IV name and far
# outside it on a quiet one, so the same nominal offset gave wildly different
# assignment probabilities. Published premium-selling practice sizes by delta
# instead — a ~16-delta short strike sits roughly one standard deviation out and
# has about an 84% chance of expiring worthless, whatever the underlying's vol.
SHORT_STRIKE_DELTA = 0.16   # target |delta| for short legs
WING_DELTA         = 0.05   # target |delta| for protective wings

# ── Execution cost ───────────────────────────────────────────────────────────
# Every price in this module is a Black-Scholes theoretical value — i.e. a mid.
# We cannot transact at a mid. Selling a spread means hitting the bid on the
# short legs and paying the ask on the long ones, and again in reverse when the
# position is closed (which it usually is: check_positions_for_management exits
# at DTE 21 or +50% of credit, not at expiry).
#
# Wang, Pennock, Rothschild & Devanur, "Designing Expressive and Liquid
# Financial Options Markets" (ICAIF '24) §4.3 measured closing quotes on all
# option series of the 30 DJI constituents: mean bid-ask spread of 78 cents per
# series, which their consolidating market maker tightened to 40 cents. That is
# the cost of crossing, and on a four-leg structure it is paid eight times over
# a round trip.
#
# We do NOT hardcode 78 cents. An absolute figure measured across every
# moneyness and maturity of 30 stocks is meaningless applied to a 45-DTE
# 16-delta SPY put. Instead:
#   - prefer real per-leg bid/ask when the caller supplies quotes;
#   - otherwise fall back to a *relative* estimate — a spread equal to
#     SPREAD_FRACTION_OF_MID_FALLBACK of the leg's own mid. 10% is the
#     tradeable-option rule of thumb already stated in
#     options_screener.check_option_liquidity, so the fallback is the widest
#     spread we would knowingly accept rather than an average.
# The fallback is deliberately pessimistic: it should make us reject a trade we
# would have taken, never accept one we would have refused.
SPREAD_FRACTION_OF_MID_FALLBACK = 0.10
MIN_HALF_SPREAD                 = 0.005   # half of the $0.01 minimum tick
ROUND_TRIP_CROSSINGS            = 2       # open + close

# Liquidity floor, same threshold options_screener.check_option_liquidity
# already applies per contract. That function was dead code — never called from
# any pipeline — so no option order has ever been checked against it.
MAX_LEG_SPREAD_PCT = 15.0


def strike_for_delta(S: float, T: float, sigma: float, target_delta: float,
                     option_type: str, r: float = None) -> float:
    """
    Strike whose |delta| is closest to target_delta, found by bisection on the
    Black-Scholes delta (monotonic in strike, so bisection is safe).

    Returns a whole-dollar strike. Falls back to a fixed percentage offset if
    inputs are degenerate, so a bad IV reading cannot produce a nonsense strike.
    """
    r = RISK_FREE_RATE if r is None else r
    if S <= 0 or T <= 0 or sigma <= 0 or not (0 < target_delta < 1):
        offset = 0.10 if option_type == "call" else -0.10
        return round(S * (1 + offset), 0)

    def abs_delta(K):
        try:
            return abs(bsm_greeks(S, K, T, r, sigma, option_type)["delta"])
        except Exception:
            return 0.0

    # Calls: delta falls as strike rises. Puts: |delta| falls as strike falls.
    if option_type == "call":
        lo, hi = S, S * 3.0
    else:
        lo, hi = S * 0.05, S

    for _ in range(60):
        mid = (lo + hi) / 2.0
        d = abs_delta(mid)
        if abs(d - target_delta) < 1e-4:
            break
        if option_type == "call":
            if d > target_delta:
                lo = mid          # too much delta -> move strike further out
            else:
                hi = mid
        else:
            if d > target_delta:
                hi = mid          # too much delta -> move strike lower
            else:
                lo = mid
    strike = round((lo + hi) / 2.0, 0)
    return max(1.0, strike)


# ── Strategy builders ─────────────────────────────────────────────────────────

def build_iron_condor(S: float, T: float, sigma: float,
                      call_wing: float = 0.10, put_wing: float = 0.10,
                      wing_width: float = 0.05, use_delta: bool = True) -> dict:
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

    # Delta-selected strikes when IV is usable, else the legacy fixed offsets.
    # A fixed 10% offset is ~1.6 SD at 15% IV and ~0.4 SD at 100% IV, so it gave
    # the same structure wildly different assignment odds across underlyings.
    if use_delta and sigma > 0 and T > 0:
        K_short_put  = strike_for_delta(S, T, sigma, SHORT_STRIKE_DELTA, "put")
        K_short_call = strike_for_delta(S, T, sigma, SHORT_STRIKE_DELTA, "call")
        K_long_put   = strike_for_delta(S, T, sigma, WING_DELTA, "put")
        K_long_call  = strike_for_delta(S, T, sigma, WING_DELTA, "call")
        # Wings must sit outside the shorts; fall back if the chain collapses.
        if not (K_long_put < K_short_put < K_short_call < K_long_call):
            use_delta = False
    if not (use_delta and sigma > 0 and T > 0):
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
        "dte":         round(T * 365),
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
                         call_otm: float = 0.10, put_otm: float = 0.10,
                         use_delta: bool = True) -> dict:
    """
    Short Strangle: sell OTM call + sell OTM put. Undefined risk.

    Best for: high IV rank (≥50), expecting stock to stay range-bound.
    Risk: unlimited on call side if stock rallies sharply.

    Natenberg Ch 8: short strangle = ratio vertical spread category.
    Gamma negative, Theta positive, Vega negative.
    """
    r = RISK_FREE_RATE
    if use_delta and sigma > 0 and T > 0:
        K_put  = strike_for_delta(S, T, sigma, SHORT_STRIKE_DELTA, "put")
        K_call = strike_for_delta(S, T, sigma, SHORT_STRIKE_DELTA, "call")
    else:
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
        "dte":         round(T * 365),
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
        "dte":         round(T * 365),
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
        "dte":         round(T * 365),
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
        "dte":         round(T * 365),
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
        "dte":         round(T * 365),
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


# ── Execution cost ────────────────────────────────────────────────────────────

def _leg_count(strat: dict) -> int:
    """
    Number of contracts crossed, counting ratio legs by their quantity.
    A butterfly's `short_call2` leg is two contracts and costs two spreads.
    """
    n = 0
    for leg in strat.get("legs", []) or []:
        n += 2 if str(leg.get("type", "")).endswith("2") else 1
    return n


def execution_cost(strat: dict, leg_quotes: list[dict] = None) -> dict:
    """
    Round-trip cost of crossing the bid-ask spread on every leg, per share.

    Args:
        strat: strategy dict from a build_*() function.
        leg_quotes: optional list of per-leg quote dicts in the same order as
            strat["legs"], each with "bid" and "ask". Supply these from
            options_screener.check_option_liquidity for a measured cost. When
            omitted the relative fallback is used and `source` says so.

    Returns:
        {cost_per_share, cost_per_contract, source, per_leg, widest_spread_pct}

    `source` is "measured" only when a usable quote was supplied for EVERY leg.
    A partial set is not blended with estimates — a cost that is half measured
    and half guessed is neither, and reporting it as measured would be a lie.
    """
    legs = strat.get("legs", []) or []
    if not legs:
        return {"cost_per_share": 0.0, "cost_per_contract": 0.0,
                "source": "no_legs", "per_leg": [], "widest_spread_pct": None}

    quotes_usable = False
    if leg_quotes and len(leg_quotes) == len(legs):
        quotes_usable = all(
            isinstance(q, dict)
            and isinstance(q.get("bid"), (int, float)) and q["bid"] > 0
            and isinstance(q.get("ask"), (int, float)) and q["ask"] > q["bid"]
            for q in leg_quotes
        )

    per_leg = []
    widest_pct = 0.0
    total = 0.0
    for i, leg in enumerate(legs):
        multiple = 2 if str(leg.get("type", "")).endswith("2") else 1
        if quotes_usable:
            bid, ask = leg_quotes[i]["bid"], leg_quotes[i]["ask"]
            mid = (bid + ask) / 2.0
            half = (ask - bid) / 2.0
            pct = (ask - bid) / mid * 100 if mid > 0 else 999.0
        else:
            mid = float(leg.get("price", 0.0) or 0.0)
            half = max(MIN_HALF_SPREAD, SPREAD_FRACTION_OF_MID_FALLBACK / 2.0 * mid)
            pct = SPREAD_FRACTION_OF_MID_FALLBACK * 100

        widest_pct = max(widest_pct, pct)
        leg_cost = half * multiple * ROUND_TRIP_CROSSINGS
        total += leg_cost
        per_leg.append({
            "type":        leg.get("type"),
            "strike":      leg.get("strike"),
            "mid":         round(mid, 4),
            "half_spread": round(half, 4),
            "contracts":   multiple,
            "round_trip_cost": round(leg_cost, 4),
        })

    return {
        "cost_per_share":    round(total, 4),
        "cost_per_contract": round(total * 100, 2),
        "source":            "measured" if quotes_usable else "estimate",
        "per_leg":           per_leg,
        "widest_spread_pct": round(widest_pct, 1),
    }


def net_of_cost_metrics(strat: dict, leg_quotes: list[dict] = None) -> dict:
    """
    Restate a strategy's economics after execution cost.

    Cost comes out of the credit AND is added to the loss: we receive less than
    the theoretical credit going in and pay away more than the theoretical loss
    getting out. Reward/risk therefore falls on both numerator and denominator,
    which is why applying the haircut to the credit alone understates it.
    """
    cost = execution_cost(strat, leg_quotes)
    c    = cost["cost_per_share"]

    credit = strat.get("net_credit")
    debit  = strat.get("net_debit")
    max_profit = strat.get("max_profit")
    max_loss   = strat.get("max_loss")

    out = {
        "exec_cost_per_share":    c,
        "exec_cost_per_contract": cost["cost_per_contract"],
        "exec_cost_source":       cost["source"],
        "widest_leg_spread_pct":  cost["widest_spread_pct"],
    }

    if credit is not None:
        out["net_credit_after_costs"] = round(credit - c, 4)
    if debit is not None:
        out["net_debit_after_costs"] = round(debit + c, 4)

    mp = (max_profit - c) if isinstance(max_profit, (int, float)) else None
    ml = (max_loss   + c) if isinstance(max_loss,   (int, float)) else None
    out["max_profit_after_costs"] = round(mp, 4) if mp is not None else None
    out["max_loss_after_costs"]   = round(ml, 4) if ml is not None else None
    out["reward_risk_net"] = (
        round(mp / ml, 4) if (mp is not None and ml is not None and ml > 0) else None
    )
    # Share of the theoretical credit consumed by crossing. Reported so a
    # marginal structure is visibly marginal in the journal, not just rejected.
    if isinstance(credit, (int, float)) and credit > 0:
        out["cost_pct_of_credit"] = round(c / credit * 100, 1)
    return out


# ── Static no-arbitrage validation ────────────────────────────────────────────
# Wang et al. §4.3 found arbitrage violations in 296 of 356 consolidated DJI
# option markets built from real closing quotes — violations are the norm, not
# the exception. Their No Arbitrage Principle (§3, after Definition 1) is that a
# portfolio guaranteed a non-negative payoff must have a non-negative cost.
#
# We are a price taker; the ~$2.28 average violation they measured is not a
# trade for us. What the principle gives us is a free correctness check on our
# OWN numbers. Every structure below has bounds it cannot violate unless an
# input is corrupt — a stale IV, a zero T, a strike ordering that collapsed. A
# credit larger than the width of the spread that secures it is not a great
# fill, it is arithmetic that did not happen. Fail closed rather than send it.

def validate_no_arbitrage(strat: dict) -> tuple[bool, str]:
    """
    Check a constructed strategy against the static no-arbitrage bounds its own
    structure implies. Returns (ok, reason).

    This validates our pricing, not the market's. A violation means the strategy
    dict is internally inconsistent and must not be traded.
    """
    strategy = strat.get("strategy", "")
    legs     = strat.get("legs", []) or []
    if not legs:
        return False, "No legs — nothing to validate"

    strikes = [leg.get("strike") for leg in legs]
    if any(not isinstance(k, (int, float)) or k <= 0 for k in strikes):
        return False, "Non-positive or missing strike — corrupt structure"

    prices = [leg.get("price") for leg in legs]
    if any(not isinstance(p, (int, float)) or p < 0 for p in prices):
        return False, "Negative or missing leg price — corrupt pricing input"

    credit = strat.get("net_credit")
    debit  = strat.get("net_debit")

    if strategy in ("bull_put_spread", "bear_call_spread"):
        # Vertical credit spread: short leg dominates the long leg, so its price
        # must be the greater of the two, and the credit cannot exceed the
        # strike width — that width is the entire liability being sold.
        by_type = {leg["type"]: leg for leg in legs}
        short_leg = next((l for t, l in by_type.items() if t.startswith("short")), None)
        long_leg  = next((l for t, l in by_type.items() if t.startswith("long")),  None)
        if short_leg is None or long_leg is None:
            return False, "Vertical spread missing a short or long leg"
        width = abs(short_leg["strike"] - long_leg["strike"])
        if width <= 0:
            return False, "Vertical spread has zero strike width"
        if short_leg["price"] < long_leg["price"]:
            return False, (f"Dominated leg priced above dominant leg "
                           f"({short_leg['type']} {short_leg['price']:.2f} < "
                           f"{long_leg['type']} {long_leg['price']:.2f}) — "
                           f"violates portfolio dominance")
        if credit is None or credit <= 0:
            return False, f"Credit spread with non-positive credit ({credit})"
        if credit >= width:
            return False, (f"Credit {credit:.2f} ≥ strike width {width:.2f} — "
                           f"riskless profit is impossible; pricing input is wrong")

    elif strategy == "iron_condor":
        by_type = {leg["type"]: leg for leg in legs}
        needed = ("long_put", "short_put", "short_call", "long_call")
        if not all(t in by_type for t in needed):
            return False, "Iron condor missing one of its four legs"
        lp, sp, sc, lc = (by_type[t]["strike"] for t in needed)
        if not (lp < sp < sc < lc):
            return False, (f"Iron condor strikes out of order "
                           f"({lp} < {sp} < {sc} < {lc} required)")
        put_width  = sp - lp
        call_width = lc - sc
        if credit is None or credit <= 0:
            return False, f"Iron condor with non-positive credit ({credit})"
        # Only one side can finish in the money, so the binding bound is the
        # narrower wing.
        if credit >= min(put_width, call_width):
            return False, (f"Credit {credit:.2f} ≥ narrower wing width "
                           f"{min(put_width, call_width):.2f} — riskless profit "
                           f"is impossible; pricing input is wrong")

    elif strategy == "butterfly":
        # Convexity: the middle strike must be equidistant enough that the
        # long-butterfly debit is positive but below the wing width. Merton's
        # convexity condition on option prices in strike.
        ks = sorted(strikes)
        if len(ks) != 3:
            return False, "Butterfly must have exactly three strikes"
        low, mid, high = ks
        if not (low < mid < high):
            return False, f"Butterfly strikes not strictly increasing ({ks})"
        if debit is None or debit <= 0:
            return False, (f"Long butterfly with non-positive debit ({debit}) — "
                           f"would be a free option, violating convexity")
        if debit >= (mid - low):
            return False, (f"Butterfly debit {debit:.2f} ≥ wing width "
                           f"{mid - low:.2f} — cannot exceed max payoff")

    elif strategy == "long_straddle":
        if debit is None or debit <= 0:
            return False, f"Long straddle with non-positive debit ({debit})"
        # Both legs must carry value; a zero-priced leg means T or sigma
        # collapsed and the greeks below are meaningless.
        if min(prices) <= 0:
            return False, "Straddle leg priced at zero — degenerate T or sigma"

    elif strategy == "short_strangle":
        if credit is None or credit <= 0:
            return False, f"Short strangle with non-positive credit ({credit})"

    return True, "OK"


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

    # `underlying` is stamped by select_strategy, not by the build_*()
    # functions. Defaulting it to 0 made every simulated price 0, which is
    # below every breakeven, so a strategy dict that skipped the selector
    # reported nine consecutive max-loss scenarios and a 100% losing fraction
    # with no error. A scenario analysis that cannot see the spot price has
    # nothing to say — raise instead of inventing a loss.
    S = strat.get("underlying")
    if not isinstance(S, (int, float)) or S <= 0:
        raise ValueError(
            "scenario_analysis needs strat['underlying'] (spot price); got "
            f"{S!r}. Pass a strategy from select_strategy(), or stamp the "
            "underlying before calling."
        )

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

    # ── Shortfall statistics ─────────────────────────────────────────────────
    # Bemporad, Bellucci & Gabbriellini, "Dynamic option hedging via stochastic
    # model predictive control based on scenario simulation" (Quantitative
    # Finance 2014) §4.1.2 offers shortfall objectives (eqs 23-24) as
    # alternatives to the minimum-variance one it solves by default. The
    # distinction matters to us: variance penalises a scenario that comes in
    # *better* than expected exactly as hard as one that comes in worse.
    # Shortfall penalises only the losing side.
    #
    # worst_case alone is one point on the curve and says nothing about how
    # much of the distribution is losing. flat_case is the opposite bias — the
    # scenario a premium seller most wants to look at. Average shortfall across
    # the losing scenarios sits between them and is the honest number for a
    # short-premium book, where the shape of the loss tail is the whole risk.
    #
    # These are equal-weighted over an arbitrary grid of moves, not
    # probability-weighted, so read them as relative severity between
    # structures, not as an expected loss.
    losing = [s for s in scenarios if s["pl"] < 0]
    if losing:
        avg_shortfall = sum(s["pl"] for s in losing) / len(losing)
        max_shortfall = min(s["pl"] for s in losing)
    else:
        avg_shortfall = 0.0
        max_shortfall = 0.0

    return {
        "strategy":  strategy,
        "symbol":    strat.get("symbol", ""),
        "scenarios": scenarios,
        "worst_case": worst,
        "best_case":  best,
        "flat_case":  flat,
        "max_defined_loss_per_contract": round((max_loss or net_debit) * 100, 2),
        # Per contract, negative = loss. Scenario grid is equal-weighted.
        "avg_shortfall_per_contract": round(avg_shortfall, 2),
        "max_shortfall_per_contract": round(max_shortfall, 2),
        "losing_scenario_count":      len(losing),
        "scenario_count":             len(scenarios),
        "losing_scenario_fraction":   round(len(losing) / len(scenarios), 3) if scenarios else None,
    }


# ── Strategy selector ─────────────────────────────────────────────────────────

def select_strategy(screen_result: dict) -> Optional[dict]:
    """
    Select optimal strategy based on IV rank + trend signal.

    Decision matrix (Natenberg Ch 8):
      IVR ≥ 60, neutral → Iron Condor (defined risk premium selling)
      IVR ≥ 60, bullish → Bull Put Spread (credit, bullish bias)
      IVR ≥ 60, bearish → Bear Call Spread (credit, bearish bias)
      IVR ≤ 20, any     → Long Straddle (buy premium for big move)
      IVR 21-59         → Skip (no strong edge)

    These thresholds are deliberately stricter than the commonly cited IVR > 50
    for selling premium: 60 to sell, 20 to buy. The published edge is that
    implied volatility exceeds subsequent realized volatility roughly 85% of the
    time by 2-4 vol points, and that premium sellers filtering on IVR > 50
    capture more of it than sellers who do not filter. Requiring 60 trades fewer
    setups for a larger average edge, which suits a strategy being validated on
    a small sample. Do not loosen these without evidence from the round-trip
    record — see CLAUDE.md on learned-hint rails.

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

def passes_risk_filters(strat: dict, account_equity: float = None,
                        leg_quotes: list[dict] = None) -> tuple[bool, str]:
    """
    Basic risk gate before placing any order.

    Rules:
      1. Structure must satisfy its own static no-arbitrage bounds
      2. Max loss per trade ≤ 2% of account equity (net of execution cost)
      3. Reward/risk ≥ 0.25 NET OF EXECUTION COST
      4. Per-leg bid-ask spread ≤ 15% of mid when quotes are available
      5. Must have positive theta (time working for us on credit strategies)
      6. DTE between 21 and 60 (sweet spot for theta decay vs gamma risk)

    Args:
        leg_quotes: optional per-leg {"bid","ask"} in strat["legs"] order, from
            options_screener.check_option_liquidity. Without them the execution
            cost is a pessimistic estimate and the liquidity check is skipped —
            pass them whenever the chain is reachable.
    """
    # No silent default. A hardcoded $25,000 fallback sized risk against an
    # account that does not exist; refuse rather than guess.
    if account_equity is None or account_equity <= 0:
        return False, ("Account equity unknown — cannot size the 2% max-loss cap. "
                       "Pass honest equity from options_trade.get_equity().")

    # Static no-arbitrage bounds first. If the structure's own arithmetic is
    # inconsistent, every number downstream — max loss, reward/risk, greeks —
    # is derived from a corrupt input and there is nothing to gate on.
    arb_ok, arb_reason = validate_no_arbitrage(strat)
    if not arb_ok:
        return False, f"No-arbitrage violation: {arb_reason}"

    max_loss = strat.get("max_loss")
    if max_loss is None:
        return False, "Undefined max loss — use defined-risk structure in paper trading"

    # Execution cost. Both the loss cap and the reward/risk bar are measured
    # against what we can actually transact at, not the theoretical mid.
    costs = net_of_cost_metrics(strat, leg_quotes)
    cost_note = (f"exec cost ${costs['exec_cost_per_contract']:.0f}/contract "
                 f"({costs['exec_cost_source']})")

    max_loss_after = costs["max_loss_after_costs"]
    if max_loss_after is None:
        return False, "Could not compute max loss net of execution cost"

    max_loss_per_contract = max_loss_after * 100  # per contract (100 shares)
    max_allowed = account_equity * 0.02

    if max_loss_per_contract > max_allowed:
        return False, (f"Max loss ${max_loss_per_contract:.0f} > 2% account "
                       f"(${max_allowed:.0f}) — {cost_note}")

    # Liquidity floor. Only enforceable with real quotes; an estimated spread
    # would just be checking the fallback constant against itself.
    if costs["exec_cost_source"] == "measured":
        widest = costs["widest_leg_spread_pct"]
        if widest is not None and widest > MAX_LEG_SPREAD_PCT:
            return False, (f"Widest leg spread {widest:.1f}% > {MAX_LEG_SPREAD_PCT:.0f}% "
                           f"of mid — illiquid contract")

    # Bielfeldt: raise the minimum reward/risk bar — only play strong hands.
    # Measured NET of execution cost. On mid prices a 45-DTE 16-delta iron
    # condor clears 0.25 by a hair; crossing eight spreads over the round trip
    # consumes 16-26% of the credit and puts it under the bar. The gate is not
    # loosened to compensate — a structure whose edge does not survive its own
    # execution cost had no edge, and CLAUDE.md's tighten-only rail forbids
    # lowering a documented floor to book more trades.
    rr_net = costs["reward_risk_net"]
    rr_gross = strat.get("reward_risk")
    if rr_net is not None and rr_net < 0.25:
        gross_note = f" (gross {rr_gross:.2f})" if isinstance(rr_gross, (int, float)) else ""
        return False, (f"Reward/risk net of costs {rr_net:.2f} < 0.25 minimum"
                       f"{gross_note} — {cost_note}")

    # Theta sign. The builders return POSITIVE theta for credit structures —
    # time decay working in our favour, which is the whole point of selling
    # premium. The condition here was inverted (`theta >= 0` rejected), so every
    # iron condor, put spread, call spread and strangle was refused by a filter
    # whose own error message said positive theta was what it wanted. The
    # options pipeline could not open a single premium-selling position.
    theta = strat.get("theta")
    strategy = strat.get("strategy", "")
    if strategy in ("bull_put_spread", "bear_call_spread", "iron_condor",
                    "short_strangle"):
        if not isinstance(theta, (int, float)):
            return False, "Theta unavailable — cannot confirm time decay favours us"
        if theta <= 0:
            return False, (f"Premium-selling structure needs positive theta, "
                           f"got {theta:.4f}")

    # DTE. The builders do not always populate this, and `None < 21` raises a
    # TypeError rather than failing the check, so missing DTE is rejected
    # explicitly instead of crashing the run.
    dte = strat.get("dte")
    if not isinstance(dte, (int, float)):
        return False, "DTE unknown — cannot verify the 21-60 day entry window"
    if dte < DTE_MIN_ENTRY:
        return False, f"DTE {dte} < {DTE_MIN_ENTRY} — too close to expiry (gamma risk)"
    if dte > DTE_MAX_ENTRY:
        return False, f"DTE {dte} > {DTE_MAX_ENTRY} — theta decay too slow"

    return True, f"OK — {cost_note}, reward/risk net {rr_net if rr_net is not None else 'n/a'}"


# ── Aggregate short-volatility exposure ───────────────────────────────────────
# Aurum, "Arbitrage hedge fund primer" (January 2025), short volatility /
# alternative risk premia section: short vol produces "small, consistent gains
# interrupted by occasional large losses during extreme events", and names
# February 2018 'Volmageddon' — short-vol exchange-traded products losing up to
# 90% of their value in a single day — alongside 2008 and March 2020. Its stated
# risk controls are upper-bounded losses, strict position limits, stop-losses,
# and diversification across assets.
#
# We have the first (defined-risk only) and the third (stop at -100% of credit).
# We did not have the second or fourth at the portfolio level. run_options.py
# capped concurrent positions at 5 and passes_risk_filters caps each at 2% of
# equity, so the pipeline could hold 10% of equity of max loss, every dollar of
# it short premium on correlated underlyings.
#
# The primer's own explanation of why that is worse than it looks: "Increased
# correlations during market stress can reduce the benefits of strategies
# relying on low correlations." Five separately-sized short-vol positions are
# one position in a spike, so the per-trade cap does not bound the real risk.
# CLAUDE.md already applies this logic to equities ("SPY and QQQ count as one
# position for correlation purposes"); options need the same treatment.
MAX_AGGREGATE_SHORT_VOL_PCT   = 0.06   # of equity, summed defined max loss
MAX_CONCURRENT_SHORT_VOL      = 3      # of the 5 total position slots

SHORT_PREMIUM_STRATEGIES = (
    "iron_condor", "bull_put_spread", "bear_call_spread", "short_strangle",
)

# Underlyings that are one volatility bet, not several. Broad US equity index
# proxies share nearly all of their variance; selling premium on SPY and QQQ in
# the same week is one trade sized twice.
CORRELATED_UNDERLYING_GROUPS = (
    {"SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI"},
)


def underlying_group(symbol: str) -> str:
    """Correlation group key for an underlying. Ungrouped names map to themselves."""
    s = (symbol or "").upper()
    for i, group in enumerate(CORRELATED_UNDERLYING_GROUPS):
        if s in group:
            return f"index_equity_{i}"
    return s


def is_short_premium(strat: dict) -> bool:
    """True if the structure is net short volatility (collects premium)."""
    return strat.get("strategy", "") in SHORT_PREMIUM_STRATEGIES


def aggregate_short_vol_check(strat: dict, open_short_vol: list[dict],
                              account_equity: float,
                              leg_quotes: list[dict] = None) -> tuple[bool, str]:
    """
    Portfolio-level gate on short-volatility concentration.

    Args:
        strat: candidate strategy dict.
        open_short_vol: already-open short-premium positions, each
            {"symbol": str, "max_loss_per_contract": float, "qty": int}.
            max_loss_per_contract is in dollars (per-share loss × 100).
        account_equity: honest equity, from options_trade.get_equity().
        leg_quotes: optional per-leg quotes, so the candidate's contribution is
            costed the same way passes_risk_filters costs it.

    Returns (ok, reason). Long-premium structures pass through untouched — they
    are the other side of this risk, not more of it.
    """
    if not is_short_premium(strat):
        return True, "Not a short-premium structure — concentration cap N/A"

    if account_equity is None or account_equity <= 0:
        return False, ("Account equity unknown — cannot size the aggregate "
                       "short-vol cap")

    open_short_vol = open_short_vol or []

    if len(open_short_vol) >= MAX_CONCURRENT_SHORT_VOL:
        return False, (f"{len(open_short_vol)} short-vol positions open ≥ "
                       f"{MAX_CONCURRENT_SHORT_VOL} cap — a volatility spike "
                       f"hits all of them at once")

    # Correlation: one short-vol bet per underlying group.
    candidate_group = underlying_group(strat.get("symbol", ""))
    for pos in open_short_vol:
        if underlying_group(pos.get("symbol", "")) == candidate_group:
            return False, (f"Already short premium on {pos.get('symbol')} — same "
                           f"correlation group as {strat.get('symbol')} "
                           f"({candidate_group}); would double one bet")

    # Aggregate defined risk, net of execution cost on the candidate.
    costs = net_of_cost_metrics(strat, leg_quotes)
    candidate_loss = costs["max_loss_after_costs"]
    if candidate_loss is None:
        return False, "Candidate has undefined max loss — cannot aggregate risk"
    candidate_risk = candidate_loss * 100

    existing_risk = 0.0
    for pos in open_short_vol:
        per_contract = pos.get("max_loss_per_contract")
        if not isinstance(per_contract, (int, float)) or per_contract <= 0:
            # An open short-vol position whose risk we cannot read is not
            # assumed to be zero. Refuse rather than under-count the book.
            return False, (f"Open short-vol position {pos.get('symbol','?')} has "
                           f"unknown max loss — cannot verify the aggregate cap")
        existing_risk += per_contract * max(1, int(pos.get("qty", 1) or 1))

    total_risk = existing_risk + candidate_risk
    cap = account_equity * MAX_AGGREGATE_SHORT_VOL_PCT

    if total_risk > cap:
        return False, (f"Aggregate short-vol risk ${total_risk:,.0f} "
                       f"(${existing_risk:,.0f} open + ${candidate_risk:,.0f} new) "
                       f"> {MAX_AGGREGATE_SHORT_VOL_PCT*100:.0f}% of equity "
                       f"(${cap:,.0f})")

    return True, (f"Aggregate short-vol risk ${total_risk:,.0f} of ${cap:,.0f} cap "
                  f"({len(open_short_vol)+1}/{MAX_CONCURRENT_SHORT_VOL} slots)")


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
