#!/usr/bin/env python3
"""
robinhood_screener.py — Live options screener powered by Robinhood MCP.

Replaces BSM-estimated IV in options_screener.py with:
  - Real implied volatility from Robinhood option quotes
  - Live Greeks (delta, gamma, theta, vega) direct from market
  - Accurate bid/ask and open interest
  - Earnings calendar awareness (avoid entering before binary events)

Usage (from Python):
    from robinhood_screener import screen_symbol_robinhood, scan_universe_robinhood

Note: This module calls Robinhood MCP tools. It is designed to be called
from an agent environment where those tools are available. For batch
backtesting or offline use, fall back to options_screener.py.

Theory (Natenberg Ch 8, Hull Ch 14):
  - Real IV from market prices is far more accurate than BSM back-calculation
    from stale close prices. Robinhood provides the market's actual IV.
  - Greeks are derived from the same BSM model but with live inputs; using
    the market's IV directly eliminates compounding estimation errors.
  - IVR using today's live IV vs a rolling history gives the strongest signal.
"""

import os
import sys
import json
import math
import requests
from datetime import datetime, date, timedelta
from typing import Optional

# ── Alpaca fallback for HV (we still need price history) ─────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
DATA_URL      = "https://data.alpaca.markets"

RISK_FREE_RATE = 0.053


def _alpaca_headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


def get_historical_closes_alpaca(symbol: str, days: int = 252) -> list[float]:
    """Fetch daily closing prices from Alpaca for HV calculation."""
    end   = date.today().isoformat()
    start = (date.today() - timedelta(days=days + 50)).isoformat()
    url   = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": "1Day",
        "start":     start,
        "end":       end,
        "limit":     days + 50,
        "feed":      "iex",
    }
    r = requests.get(url, headers=_alpaca_headers(), params=params, timeout=15)
    if r.status_code != 200:
        return []
    bars = r.json().get("bars", [])
    return [b["c"] for b in bars if "c" in b]


def historical_vol(closes: list[float], window: int = 30) -> float:
    """Annualized historical volatility from daily close prices."""
    if len(closes) < window + 1:
        return 0.0
    prices = closes[-(window + 1):]
    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    return math.sqrt(variance * 252)


# ── Target expiry selection ───────────────────────────────────────────────────

def pick_target_expiry(expiration_dates: list[str], target_dte: int = 38) -> Optional[str]:
    """
    Pick the expiry closest to target_dte days from today.
    Filters to 21–60 DTE window (Natenberg optimal theta zone).
    """
    today = date.today()
    best = None
    best_diff = float("inf")
    for exp_str in expiration_dates:
        try:
            exp = date.fromisoformat(exp_str)
            dte = (exp - today).days
            if 21 <= dte <= 60:
                diff = abs(dte - target_dte)
                if diff < best_diff:
                    best_diff = diff
                    best = exp_str
        except ValueError:
            continue
    return best


# ── IVR from rolling Robinhood IV snapshots ───────────────────────────────────

class IVHistoryCache:
    """
    Rolling cache of ATM IV observations for IVR calculation.
    Stored as {symbol: [iv_float, ...]} in a JSON file.
    Each observation is today's ATM IV from Robinhood.
    We accumulate over time; with 30+ entries we get a meaningful IVR.
    """

    CACHE_PATH = os.path.join(
        os.path.dirname(__file__), "logs", "options", "iv_history_cache.json"
    )

    @classmethod
    def load(cls) -> dict:
        try:
            with open(cls.CACHE_PATH, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @classmethod
    def save(cls, data: dict):
        os.makedirs(os.path.dirname(cls.CACHE_PATH), exist_ok=True)
        with open(cls.CACHE_PATH, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def update(cls, symbol: str, iv: float, max_history: int = 252):
        data = cls.load()
        if symbol not in data:
            data[symbol] = []
        data[symbol].append(round(iv, 4))
        data[symbol] = data[symbol][-max_history:]
        cls.save(data)

    @classmethod
    def get_history(cls, symbol: str) -> list[float]:
        return cls.load().get(symbol, [])


def iv_rank_from_cache(symbol: str, current_iv: float) -> float:
    """
    IVR = (current_iv - 52w_low) / (52w_high - 52w_low) × 100.
    Uses cached Robinhood IV observations.
    Falls back to HV-proxy if cache is < 10 entries.
    """
    history = IVHistoryCache.get_history(symbol)
    if len(history) < 10:
        # Not enough Robinhood data yet — fall back to HV history from Alpaca
        closes = get_historical_closes_alpaca(symbol, days=312)
        if len(closes) < 32:
            return 50.0  # Unknown — treat as neutral
        hv_history = []
        for i in range(30, len(closes)):
            try:
                hv = historical_vol(closes[i - 30:i + 1])
                hv_history.append(hv)
            except Exception:
                continue
        if not hv_history:
            return 50.0
        low52 = min(hv_history)
        high52 = max(hv_history)
        if high52 - low52 < 0.001:
            return 50.0
        return (current_iv - low52) / (high52 - low52) * 100

    # Use Robinhood IV cache
    low52  = min(history)
    high52 = max(history)
    if high52 - low52 < 0.001:
        return 50.0
    return (current_iv - low52) / (high52 - low52) * 100


def iv_percentile_from_cache(symbol: str, current_iv: float) -> float:
    """IVP = % of days in past year where IV was below current IV."""
    history = IVHistoryCache.get_history(symbol)
    if not history:
        return 50.0
    below = sum(1 for h in history if h < current_iv)
    return below / len(history) * 100


# ── Core screener using Robinhood live quotes ─────────────────────────────────

def screen_symbol_robinhood(
    symbol: str,
    chain_id: str,
    expiration_dates: list[str],
    stock_price: float,
    atm_iv: float,
    atm_delta: float,
    atm_theta: float,
    atm_gamma: float,
    atm_vega: float,
    atm_bid: float,
    atm_ask: float,
    open_interest: int,
    has_earnings_soon: bool = False,
) -> Optional[dict]:
    """
    Full IV rank screen using live Robinhood data instead of BSM estimates.

    Args:
        symbol: Underlying ticker
        chain_id: Robinhood chain UUID
        expiration_dates: Available expiries from get_option_chains
        stock_price: Current stock price
        atm_iv: ATM implied volatility (from Robinhood quote, as decimal e.g. 0.483)
        atm_delta/theta/gamma/vega: Greeks from Robinhood quote
        atm_bid/ask: Live bid/ask for ATM contract
        open_interest: OI for ATM contract
        has_earnings_soon: True if earnings within 7 days (skip entry)

    Returns:
        Screening result dict or None.
    """
    # Update IV cache for future IVR calculations
    IVHistoryCache.update(symbol, atm_iv)

    # Compute IVR and IVP
    ivr = iv_rank_from_cache(symbol, atm_iv)
    ivp = iv_percentile_from_cache(symbol, atm_iv)

    # HV for comparison
    closes = get_historical_closes_alpaca(symbol, days=65)
    hv30 = historical_vol(closes[-31:]) if len(closes) >= 31 else atm_iv

    # Bid-ask spread quality check
    mid = (atm_bid + atm_ask) / 2 if atm_bid > 0 and atm_ask > 0 else 0
    spread_pct = (atm_ask - atm_bid) / mid * 100 if mid > 0 else 999

    # Strategy signal (Bielfeldt: only play high-percentage hands)
    if has_earnings_soon:
        signal = "avoid_earnings"  # Don't enter before binary event
    elif ivr >= 60:
        signal = "sell_premium"    # IV historically high → sell
    elif ivr <= 20:
        signal = "buy_premium"     # IV historically low → buy
    else:
        signal = "neutral"         # Fold — no clear edge

    # Momentum: use 20-day vs 50-day from Alpaca price history
    trend = "unknown"
    if len(closes) >= 50:
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        trend = "bullish" if sma20 > sma50 else "bearish"

    # Pick target expiry
    target_exp = pick_target_expiry(expiration_dates)
    dte = (date.fromisoformat(target_exp) - date.today()).days if target_exp else 0

    return {
        "symbol":          symbol,
        "chain_id":        chain_id,
        "price":           round(stock_price, 2),
        "atm_iv":          round(atm_iv * 100, 2),      # as %
        "hv30":            round(hv30 * 100, 2),         # as %
        "iv_premium":      round((atm_iv - hv30) * 100, 2),  # IV - HV gap
        "iv_rank":         round(ivr, 1),
        "iv_pct":          round(ivp, 1),
        "signal":          signal,
        "trend":           trend,
        "expiry_target":   target_exp,
        "dte":             dte,
        "greeks": {
            "delta": round(atm_delta, 4),
            "gamma": round(atm_gamma, 6),
            "theta": round(atm_theta, 4),
            "vega":  round(atm_vega, 4),
        },
        "liquidity": {
            "bid":        atm_bid,
            "ask":        atm_ask,
            "spread_pct": round(spread_pct, 1),
            "open_interest": open_interest,
            "liquid":     spread_pct < 15.0 and open_interest > 100,
        },
        "has_earnings_soon": has_earnings_soon,
        "data_source":     "robinhood_live",
    }


def scan_universe_robinhood(candidates: list[dict], top_n: int = 5) -> list[dict]:
    """
    Rank a list of pre-screened candidates (already have live data) by edge quality.

    Args:
        candidates: List of dicts from screen_symbol_robinhood()
        top_n: Return top N

    Returns:
        Top candidates sorted by IV rank extremity, liquidity-filtered.
    """
    # Filter out earnings plays and illiquid options
    actionable = [
        c for c in candidates
        if c.get("signal") not in ("neutral", "avoid_earnings", None)
        and c.get("liquidity", {}).get("liquid", False)
    ]

    # Sort by distance from neutral (most extreme IV rank = strongest signal)
    actionable.sort(key=lambda x: abs(x["iv_rank"] - 50), reverse=True)
    return actionable[:top_n]


# ── Iron condor builder ───────────────────────────────────────────────────────

def build_iron_condor_strikes(
    stock_price: float,
    atm_iv: float,
    dte: int,
    short_delta: float = 0.20,
    wing_width: float = 0.05,
) -> dict:
    """
    Compute iron condor strikes from BSM inverse delta (approximation).
    Real execution uses live strike lookup via get_option_instruments.

    Args:
        stock_price: Current stock price
        atm_iv: ATM implied volatility (decimal)
        dte: Days to expiry
        short_delta: Target delta for short strikes (0.15–0.25 typical)
        wing_width: Wing as fraction of stock price (e.g. 0.05 = 5%)

    Returns:
        Dict with short_call, long_call, short_put, long_put strike estimates.
    """
    T = dte / 365.0
    sigma = atm_iv

    # Normal quantile for target delta: z = N_inv(delta) for calls
    # Approximate using log-normal: K_call ≈ S * exp(z * sigma * sqrt(T))
    # For delta = 0.20 on a call: z ≈ 0.84 (from standard normal table)
    # More precisely: use d1 = -N_inv(delta) for OTM call
    def normal_quantile(p):
        # Rational approximation (Beasley-Springer-Moro)
        a = [2.50662823884, -18.61500062529, 41.39119773534, -25.44106049637]
        b = [-8.47351093090, 23.08336743743, -21.06224101826, 3.13082909833]
        c = [0.3374754822726147, 0.9761690190917186, 0.1607979714918209,
             0.0276438810333863, 0.0038405729373609, 0.0003951896511349,
             0.0000321767881768, 0.0000002888167364, 0.0000003960315187]
        if p < 0.02425:
            q = math.sqrt(-2 * math.log(p))
            return -(((c[0] + c[1] * q) + c[2] * q ** 2) / (1 + c[3] * q + c[4] * q ** 2))
        elif p > 0.97575:
            q = math.sqrt(-2 * math.log(1 - p))
            return (((c[0] + c[1] * q) + c[2] * q ** 2) / (1 + c[3] * q + c[4] * q ** 2))
        else:
            q = p - 0.5
            r = q * q
            return q * (((a[0] + r * a[1]) + r ** 2 * a[2]) + r ** 3 * a[3]) / \
                   (((b[0] + r * b[1]) + r ** 2 * b[2]) + r ** 3 * b[3])

    # d1 for target call delta: call_delta = N(d1) → d1 = N_inv(delta)
    d1_call = normal_quantile(short_delta)
    # K_short_call: solve d1 = (ln(S/K) + (r + 0.5*σ²)*T) / (σ*sqrt(T))
    # K = S * exp(-(d1 * σ * sqrt(T)) + (r + 0.5*σ²)*T)
    sqrtT   = math.sqrt(T)
    r       = RISK_FREE_RATE
    K_short_call = stock_price * math.exp(-(d1_call * sigma * sqrtT) + (r + 0.5 * sigma ** 2) * T)

    # For short put at same delta magnitude: use symmetry (put-call parity)
    d1_put  = -normal_quantile(1 - short_delta)
    K_short_put = stock_price * math.exp(-(d1_put * sigma * sqrtT) + (r + 0.5 * sigma ** 2) * T)

    # Wing width
    wing = stock_price * wing_width
    K_long_call = K_short_call + wing
    K_long_put  = K_short_put  - wing

    # Round to nearest $5 (standard strike increment for most underlyings)
    def round5(k): return round(k / 5) * 5

    return {
        "short_call": round5(K_short_call),
        "long_call":  round5(K_long_call),
        "short_put":  round5(K_short_put),
        "long_put":   round5(K_long_put),
    }


# ── Butterfly builder ─────────────────────────────────────────────────────────

def build_butterfly_strikes(stock_price: float, wing_width_pct: float = 0.05) -> dict:
    """
    Standard ATM call butterfly strikes.
    Long lower wing + Short 2x ATM + Long upper wing.
    """
    wing = stock_price * wing_width_pct
    def round5(k): return round(k / 5) * 5

    return {
        "long_low":   round5(stock_price - wing),
        "short_atm":  round5(stock_price),
        "long_high":  round5(stock_price + wing),
        "wing_width": round5(wing),
    }


# ── Report formatter ──────────────────────────────────────────────────────────

def format_screen_result(result: dict) -> str:
    """Human-readable one-liner for a screener result."""
    g = result.get("greeks", {})
    liq = result.get("liquidity", {})
    return (
        f"{result['symbol']:6s} | ${result['price']:.2f} | "
        f"IV={result['atm_iv']:.1f}% | HV={result['hv30']:.1f}% | "
        f"IVR={result['iv_rank']:.0f} | IVP={result['iv_pct']:.0f} | "
        f"signal={result['signal']:15s} | trend={result['trend']:8s} | "
        f"θ={g.get('theta', 0):.3f}/day | "
        f"OI={liq.get('open_interest', 0):,} | "
        f"spread={liq.get('spread_pct', 0):.1f}%"
    )


# ── Agent integration point ───────────────────────────────────────────────────

def build_screen_from_robinhood_quote(
    symbol: str,
    chain_id: str,
    expiration_dates: list[str],
    stock_price: float,
    option_quote: dict,
    has_earnings_soon: bool = False,
) -> Optional[dict]:
    """
    Build a screen result from a raw Robinhood option quote dict.

    Args:
        option_quote: Dict from get_option_quotes results[].quote

    This is the integration point: call from an agent that has already
    fetched the quote via Robinhood MCP, then pass the raw quote here.
    """
    iv    = float(option_quote.get("implied_volatility", 0))
    delta = float(option_quote.get("delta", 0))
    theta = float(option_quote.get("theta", 0))
    gamma = float(option_quote.get("gamma", 0))
    vega  = float(option_quote.get("vega", 0))
    bid   = float(option_quote.get("bid_price", 0))
    ask   = float(option_quote.get("ask_price", 0))
    oi    = int(option_quote.get("open_interest", 0))

    if iv <= 0:
        return None

    return screen_symbol_robinhood(
        symbol=symbol,
        chain_id=chain_id,
        expiration_dates=expiration_dates,
        stock_price=stock_price,
        atm_iv=iv,
        atm_delta=abs(delta),
        atm_theta=theta,
        atm_gamma=gamma,
        atm_vega=vega,
        atm_bid=bid,
        atm_ask=ask,
        open_interest=oi,
        has_earnings_soon=has_earnings_soon,
    )


if __name__ == "__main__":
    # Example: manual test with known TSLA data from this session
    tsla_quote = {
        "implied_volatility": "0.483170",
        "delta": "0.520359",
        "theta": "-0.359803",
        "gamma": "0.007491",
        "vega":  "0.438789",
        "bid_price": "20.650000",
        "ask_price": "20.800000",
        "open_interest": 2222,
    }
    result = build_screen_from_robinhood_quote(
        symbol="TSLA",
        chain_id="9ee49197-7b3c-46c2-8d83-5d5ad1ed9eaa",
        expiration_dates=["2026-08-21", "2026-09-18"],
        stock_price=378.05,
        option_quote=tsla_quote,
        has_earnings_soon=True,   # Earnings July 22
    )
    if result:
        print(format_screen_result(result))
        strikes = build_butterfly_strikes(result["price"])
        print(f"  Butterfly strikes: {strikes}")
        ic_strikes = build_iron_condor_strikes(result["price"], result["atm_iv"] / 100, result["dte"])
        print(f"  Iron condor strikes: {ic_strikes}")
