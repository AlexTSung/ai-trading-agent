#!/usr/bin/env python3
"""
options_screener.py — Options opportunity screener for Alpaca paper account.

Screens for:
  1. IV Rank / IV Percentile to classify volatility regime
  2. Most active options chains (liquidity filter)
  3. Strategy signal generation based on IV + trend

Theory (Natenberg Ch 8, Hull Ch 14):
  - High IV rank → market overpricing volatility → sell premium strategies
  - Low IV rank → market underpricing volatility → buy premium strategies
  - IV comes from BSM reverse-calculation on market prices
"""

import os
import sys
import json
import math
import requests
from datetime import datetime, date, timedelta
from typing import Optional

from options_pricing import (
    implied_vol, historical_vol, iv_rank, iv_percentile,
    days_to_expiry, trading_days_to_expiry, bsm_greeks
)

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = "https://paper-api.alpaca.markets"
DATA_URL      = "https://data.alpaca.markets"

RISK_FREE_RATE = 0.053  # ~current Fed funds rate (update periodically)


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


# ── Earnings blackout (CLAUDE.md Hard Rule 9, options gap) ───────────────────
# The equities Judge has vetoed entries within 3 trading days of earnings
# since the ruleset was first written down. Options never got the same check —
# confirmed by grep, zero references to "earnings" anywhere in this file,
# options_strategy.py, or run_options.py. Selling premium into an earnings
# print is the same mistake with more leverage (CLAUDE.md's own options
# improvements list flagged this as the next highest-value gap), and buying
# volatility into a known catalyst carries its own IV-crush risk the moment
# the report clears — so this blocks new entries in both directions, matching
# the equities side's blanket rule rather than only gating premium sellers.
def check_earnings_blackout(symbol: str) -> tuple:
    """
    Returns (vetoed: bool, reason: str). Reuses judge.py's keyword classifier
    (same false-positive guards as equities: rejects 13F "Q2 Portfolio"
    stories and backward-looking recaps unless forward-looking language is
    also present) so the two agents' earnings logic can't quietly drift apart.

    Consults two sources, same as the equities check:
      1. Today's equities-side intelligence.json earnings watch, if that file
         exists. Options runs independently of the morning equities pipeline
         (different schedule, can fire first), so this degrades gracefully to
         source 2 alone when the file isn't there yet — it is never required.
      2. This symbol's own recent news, fetched directly here.
    """
    import judge
    import research as _research
    from market_time import et_today_iso

    intel_path = os.path.join(os.path.dirname(__file__), "logs", "paper",
                              f"{et_today_iso()}-intelligence.json")
    research_ctx = None
    if os.path.exists(intel_path):
        try:
            with open(intel_path) as f:
                research_ctx = {"intelligence": json.load(f)}
        except Exception:
            research_ctx = None

    try:
        news_items = _research.summarize_news(_research.get_news(symbol, limit=10))
    except Exception:
        news_items = []

    return judge.check_earnings_veto(news_items, symbol, research=research_ctx)


# ── VIX vol-regime gate on premium selling ───────────────────────────────────
# High IV rank is the entry signal for selling premium, but it reads the same
# after a spike has peaked (good — vol about to mean-revert) and while one is
# still expanding (Volmageddon, -90% in a day per CLAUDE.md's Aurum reference —
# bad). The equities side already gates on the real VIX (judge.score_volatility,
# intelligence.get_fear_greed): half size above 25, no new positions above 35.
# Options ignored it entirely until now. Only short-premium structures are
# gated — buying a straddle when vol is already elevated isn't the risk this
# protects against, and is_short_premium() already exists for the aggregate
# concentration cap, so this reuses the same distinction.
VIX_HALF_SIZE_OPTIONS  = 25.0
VIX_NO_NEW_SHORT_VOL   = 35.0


def get_vix_regime() -> dict:
    """
    Returns {"vix", "size_mult", "block_new_short_vol", "note"}. Calls
    intelligence.get_fear_greed() directly (a single free FRED VIXCLS lookup)
    rather than reading equities' intelligence.json, since options can run
    before the morning equities pipeline has written it today.
    """
    import intelligence
    fg = intelligence.get_fear_greed()
    vix = fg.get("vix")
    if not isinstance(vix, (int, float)):
        return {"vix": None, "size_mult": 1.0, "block_new_short_vol": False,
                "note": "VIX unavailable — full sizing, no regime block"}
    if vix > VIX_NO_NEW_SHORT_VOL:
        return {"vix": vix, "size_mult": 0.0, "block_new_short_vol": True,
                "note": f"VIX {vix:.1f} > {VIX_NO_NEW_SHORT_VOL:.0f} — no new short-premium positions"}
    if vix > VIX_HALF_SIZE_OPTIONS:
        return {"vix": vix, "size_mult": 0.5, "block_new_short_vol": False,
                "note": f"VIX {vix:.1f} > {VIX_HALF_SIZE_OPTIONS:.0f} — reduce size on new short-premium"}
    return {"vix": vix, "size_mult": 1.0, "block_new_short_vol": False,
            "note": f"VIX {vix:.1f} — normal regime"}


# ── Market data helpers ───────────────────────────────────────────────────────

def get_stock_price(symbol: str) -> Optional[float]:
    """Latest trade price for a stock."""
    url = f"{DATA_URL}/v2/stocks/{symbol}/trades/latest"
    r = requests.get(url, headers=_headers(), timeout=10)
    if r.status_code == 200:
        return r.json().get("trade", {}).get("p")
    return None


def get_historical_closes(symbol: str, days: int = 252) -> list[float]:
    """Fetch daily closing prices for historical vol calculation."""
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
    r = requests.get(url, headers=_headers(), params=params, timeout=15)
    if r.status_code != 200:
        return []
    bars = r.json().get("bars", [])
    return [b["c"] for b in bars if "c" in b]


def get_option_chain(symbol: str, expiry: Optional[str] = None) -> list[dict]:
    """
    Fetch option contracts for a symbol from Alpaca.
    Returns list of contract dicts: {symbol, strike_price, expiration_date,
    type (call/put), close_price, ...}
    """
    params = {"underlying_symbol": symbol, "limit": 200}
    if expiry:
        params["expiration_date"] = expiry
    url = f"{BASE_URL}/v2/options/contracts"
    r = requests.get(url, headers=_headers(), params=params, timeout=15)
    if r.status_code != 200:
        return []
    return r.json().get("option_contracts", [])


def get_option_snapshot(symbol: str) -> dict:
    """
    Get option snapshot (Greeks, IV, bid/ask) for a symbol from Alpaca data API.
    Returns dict keyed by option symbol.
    """
    url = f"{DATA_URL}/v1beta1/options/snapshots/{symbol}"
    params = {"feed": "indicative", "limit": 200}
    r = requests.get(url, headers=_headers(), params=params, timeout=15)
    if r.status_code != 200:
        return {}
    return r.json().get("snapshots", {})


# ── IV surface ────────────────────────────────────────────────────────────────

def compute_atm_iv(symbol: str, stock_price: float, expiry: str) -> Optional[float]:
    """
    Find the at-the-money call option closest to stock_price and compute its IV
    from the mid-price.

    Uses BSM Newton-Raphson from options_pricing.implied_vol().
    """
    cal_days = days_to_expiry(expiry)
    if cal_days <= 0:
        return None
    T = trading_days_to_expiry(cal_days) / 252.0
    if T <= 0:
        return None

    # Try Alpaca snapshot data first (has market prices)
    snapshots = get_option_snapshot(symbol)

    best_contract = None
    best_dist = float("inf")

    for opt_sym, snap in snapshots.items():
        # Parse: AAPL250117C00150000 — underlying, date, C/P, strike
        try:
            greeks = snap.get("greeks", {})
            details = snap.get("details", {})
            if not details:
                continue
            exp_d = details.get("expiration_date", "")
            if exp_d != expiry:
                continue
            strike = float(details.get("strike_price", 0))
            opt_type = details.get("type", "").lower()
            if opt_type != "call":
                continue
            dist = abs(strike - stock_price)
            if dist < best_dist:
                best_dist = dist
                best_contract = (snap, strike, T)
        except Exception:
            continue

    if best_contract is None:
        return None

    snap, strike, T = best_contract
    quote = snap.get("latestQuote", {})
    bid   = quote.get("bp", 0)
    ask   = quote.get("ap", 0)
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0

    iv = implied_vol(mid, stock_price, strike, T, RISK_FREE_RATE, "call")
    return iv


def build_iv_history(symbol: str, lookback_days: int = 252) -> list[float]:
    """
    Approximate historical IV from 30-day historical volatility of the stock.
    True IV history requires options price history (not readily available).
    We use HV as a proxy; in production this would be pulled from a vol data vendor.

    Hull §14.4: HV is the realized vol; IV is the forward-looking market estimate.
    They're correlated but IV typically has a vol-risk premium above HV.
    """
    closes = get_historical_closes(symbol, days=lookback_days + 60)
    if len(closes) < 32:
        return []

    iv_series = []
    window = 30  # 30-day rolling HV
    for i in range(window, len(closes)):
        window_closes = closes[i-window:i+1]
        try:
            hv = historical_vol(window_closes)
            iv_series.append(hv)
        except Exception:
            continue

    return iv_series


# ── IV Rank screening ─────────────────────────────────────────────────────────

def screen_symbol(symbol: str) -> Optional[dict]:
    """
    Full IV rank screen for a single symbol.

    Returns dict with:
      symbol, price, hv30, hv_current, iv_rank, iv_percentile,
      strategy_signal, expiry_target
    """
    closes = get_historical_closes(symbol, days=260)
    if len(closes) < 32:
        return None

    price = closes[-1]

    # Current 30-day HV
    hv30 = historical_vol(closes[-31:])

    # 52-week IV history (using HV as proxy)
    iv_history = build_iv_history(symbol, lookback_days=252)
    if not iv_history:
        return None

    current_iv = iv_history[-1]
    iv_52w_low  = min(iv_history)
    iv_52w_high = max(iv_history)

    ivr = iv_rank(current_iv, iv_52w_low, iv_52w_high)
    ivp = iv_percentile(current_iv, iv_history[:-1])

    # Target expiry: 30-45 DTE (optimal theta decay zone)
    today = date.today()
    target_dte = 38
    target_exp = today + timedelta(days=target_dte)
    # Roll to nearest Friday (standard US options expiry)
    while target_exp.weekday() != 4:  # 4 = Friday
        target_exp += timedelta(days=1)

    # Strategy signal (Natenberg Ch 8 / Figure 8-19)
    # Bielfeldt: wait for the right trade — don't play marginal hands.
    # Raised sell threshold to 60 (was 50); lowered buy threshold to 20 (was 25).
    # We skip more trades, but each entry has a stronger statistical edge.
    if ivr >= 60:
        signal = "sell_premium"   # Iron condor, credit spreads
    elif ivr <= 20:
        signal = "buy_premium"    # Long straddle, strangles
    else:
        signal = "neutral"        # Pass — no strong edge (Bielfeldt: fold the weak hand)

    # Momentum: simple 20-day vs 50-day
    if len(closes) >= 50:
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        trend = "bullish" if sma20 > sma50 else "bearish"
    else:
        trend = "neutral"

    return {
        "symbol":       symbol,
        "price":        round(price, 2),
        "hv30":         round(hv30 * 100, 2),         # as %
        "current_iv":   round(current_iv * 100, 2),   # as %
        "iv_52w_low":   round(iv_52w_low * 100, 2),
        "iv_52w_high":  round(iv_52w_high * 100, 2),
        "iv_rank":      round(ivr, 1),
        "iv_pct":       round(ivp, 1),
        "signal":       signal,
        "trend":        trend,
        "expiry_target": target_exp.isoformat(),
        "dte":          (target_exp - today).days,
    }


def scan_universe(symbols: list[str], top_n: int = 10) -> list[dict]:
    """
    Screen a list of symbols and return the top_n ranked by absolute IV rank
    distance from 50 (most extreme = most actionable).
    """
    results = []
    for sym in symbols:
        try:
            result = screen_symbol(sym)
            if result and result["signal"] != "neutral":
                results.append(result)
            print(f"  [{sym}] {result['signal'] if result else 'skip'} "
                  f"IVR={result['iv_rank'] if result else '?':.0f}",
                  file=sys.stderr)
        except Exception as e:
            print(f"  [{sym}] ERROR: {e}", file=sys.stderr)

    # Sort by signal priority: sell_premium and buy_premium ranked by IV rank extremity
    results.sort(key=lambda x: abs(x["iv_rank"] - 50), reverse=True)
    return results[:top_n]


# ── Liquidity filter ──────────────────────────────────────────────────────────

def check_option_liquidity(option_symbol: str) -> dict:
    """
    Check bid-ask spread and open interest for an option contract.
    Wide spreads indicate illiquid options — avoid.

    Rule of thumb: bid-ask spread < 10% of mid-price for tradeable options.
    """
    url = f"{DATA_URL}/v1beta1/options/quotes/latest"
    params = {"symbols": option_symbol, "feed": "indicative"}
    r = requests.get(url, headers=_headers(), params=params, timeout=10)
    if r.status_code != 200:
        return {"liquid": False}

    quotes = r.json().get("quotes", {})
    q = quotes.get(option_symbol, {})

    bid = q.get("bp", 0)
    ask = q.get("ap", 0)
    if bid <= 0 or ask <= 0:
        return {"liquid": False, "bid": 0, "ask": 0, "spread_pct": None}

    mid         = (bid + ask) / 2.0
    spread_pct  = (ask - bid) / mid * 100 if mid > 0 else 999

    return {
        "liquid":     spread_pct < 15.0,
        "bid":        bid,
        "ask":        ask,
        "mid":        round(mid, 2),
        "spread_pct": round(spread_pct, 1),
    }


if __name__ == "__main__":
    # Quick test with a known high-vol name
    watchlist = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA", "AMZN", "META"]
    print("Scanning for options opportunities...\n")
    candidates = scan_universe(watchlist, top_n=5)
    for c in candidates:
        print(f"{c['symbol']:6s} | price=${c['price']:.2f} | "
              f"IVR={c['iv_rank']:.0f} | IVP={c['iv_pct']:.0f} | "
              f"HV30={c['hv30']:.1f}% | signal={c['signal']} | "
              f"trend={c['trend']} | exp={c['expiry_target']}")
