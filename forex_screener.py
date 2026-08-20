#!/usr/bin/env python3
"""
forex_screener.py — Scans major FX pairs for trade opportunities.
Outputs a ranked list of candidates for forex_judge.py.

Usage:
  python forex_screener.py scan          # Full scan of all monitored pairs
  python forex_screener.py pair EUR_USD  # Scan a single pair
  python forex_screener.py read          # Print today's scan output
"""

import os
import sys
import json
import math
from datetime import date, datetime, timezone

# ── Auth ──────────────────────────────────────────────────────────────────────
OANDA_API_KEY    = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_ENV        = os.getenv("OANDA_ENV", "practice")  # "practice" or "live"

PRACTICE_URL = "https://api-fxpractice.oanda.com"
LIVE_URL     = "https://api-fxtrade.oanda.com"
BASE_URL     = PRACTICE_URL if OANDA_ENV == "practice" else LIVE_URL

# ── Monitored Pairs ───────────────────────────────────────────────────────────
MAJOR_PAIRS = [
    "EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF",
    "AUD_USD", "USD_CAD", "NZD_USD",
]
CROSS_PAIRS = [
    "EUR_GBP", "EUR_JPY", "GBP_JPY", "AUD_JPY",
]
ALL_PAIRS = MAJOR_PAIRS + CROSS_PAIRS

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "forex")
os.makedirs(JOURNAL_DIR, exist_ok=True)

# ── Spread limits (pips) ─────────────────────────────────────────────────────
MAX_SPREAD_PIPS = {
    "EUR_USD": 2.0, "GBP_USD": 2.5, "USD_JPY": 2.0, "USD_CHF": 3.0,
    "AUD_USD": 2.5, "USD_CAD": 3.0, "NZD_USD": 3.0,
    "EUR_GBP": 3.0, "EUR_JPY": 3.5, "GBP_JPY": 4.0, "AUD_JPY": 3.5,
}

# Pip size per pair
PIP_SIZE = {
    "USD_JPY": 0.01, "EUR_JPY": 0.01, "GBP_JPY": 0.01, "AUD_JPY": 0.01,
}
def get_pip_size(pair):
    return PIP_SIZE.get(pair, 0.0001)


def _headers():
    return {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json",
    }

def _check_auth():
    if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
        print(json.dumps({"error": "Missing OANDA_API_KEY or OANDA_ACCOUNT_ID env vars"}))
        sys.exit(1)


# ── API Helpers ───────────────────────────────────────────────────────────────
def get_candles(pair, granularity="H4", count=100):
    """Fetch OHLCV candles for a pair."""
    try:
        import requests
        url = f"{BASE_URL}/v3/instruments/{pair}/candles"
        params = {
            "granularity": granularity,
            "count": count,
            "price": "MBA",  # mid, bid, ask
        }
        r = requests.get(url, headers=_headers(), params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("candles", [])
    except Exception as e:
        return {"error": str(e)}


def get_current_price(pair):
    """Get current bid/ask/mid for a pair."""
    try:
        import requests
        url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing"
        params = {"instruments": pair}
        r = requests.get(url, headers=_headers(), params=params, timeout=10)
        r.raise_for_status()
        prices = r.json().get("prices", [])
        if prices:
            p = prices[0]
            bid = float(p["bids"][0]["price"])
            ask = float(p["asks"][0]["price"])
            mid = (bid + ask) / 2
            return {"bid": bid, "ask": ask, "mid": mid, "tradeable": p.get("tradeable", True)}
        return None
    except Exception as e:
        return {"error": str(e)}


def get_open_trades():
    """Return list of currently open forex trades."""
    try:
        import requests
        url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/trades"
        r = requests.get(url, headers=_headers(), timeout=10)
        r.raise_for_status()
        return r.json().get("trades", [])
    except Exception as e:
        return []


def get_account_summary():
    """Get account balance and margin info."""
    try:
        import requests
        url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/summary"
        r = requests.get(url, headers=_headers(), timeout=10)
        r.raise_for_status()
        acct = r.json().get("account", {})
        return {
            "balance": float(acct.get("balance", 0)),
            "nav": float(acct.get("NAV", 0)),
            "unrealized_pl": float(acct.get("unrealizedPL", 0)),
            "margin_used": float(acct.get("marginUsed", 0)),
            "margin_available": float(acct.get("marginAvailable", 0)),
            "open_trade_count": int(acct.get("openTradeCount", 0)),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Technical Signals ─────────────────────────────────────────────────────────
def parse_candles(candles):
    """Convert OANDA candle response to lists of OHLCV."""
    closes, highs, lows, opens = [], [], [], []
    for c in candles:
        if c.get("complete", True):
            m = c.get("mid", {})
            closes.append(float(m.get("c", 0)))
            highs.append(float(m.get("h", 0)))
            lows.append(float(m.get("l", 0)))
            opens.append(float(m.get("o", 0)))
    return closes, highs, lows, opens


def compute_sma(prices, period):
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def compute_ema(prices, period):
    if len(prices) < period:
        return None
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def compute_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = compute_ema(closes, fast)
    ema_slow = compute_ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    macd_line = ema_fast - ema_slow
    # Simplified: use recent closes to compute signal line
    macd_values = []
    for i in range(len(closes) - slow + 1):
        ef = compute_ema(closes[:slow+i], fast)
        es = compute_ema(closes[:slow+i], slow)
        if ef and es:
            macd_values.append(ef - es)
    signal_line = compute_ema(macd_values, signal) if len(macd_values) >= signal else None
    histogram = (macd_line - signal_line) if signal_line else None
    return macd_line, signal_line, histogram


def compute_momentum_score(closes, period=20):
    """Risk-adjusted momentum: mean return / std (Sharpe-like)."""
    if len(closes) < period + 1:
        return 0
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(len(closes)-period, len(closes))]
    mean_r = sum(returns) / len(returns)
    std_r = (sum((r - mean_r)**2 for r in returns) / len(returns)) ** 0.5
    if std_r == 0:
        return 0
    return mean_r / std_r  # positive = bullish momentum, negative = bearish


def compute_spread_pips(pair, bid, ask):
    pip = get_pip_size(pair)
    return (ask - bid) / pip


# ── Carry Signal ──────────────────────────────────────────────────────────────
# Approximate carry from known interest rate landscape (updated as rates change)
# Positive = long base currency earns carry; negative = short base earns carry
# Format: (base_rate_approx, quote_rate_approx)
APPROX_RATES = {
    "USD": 5.25, "EUR": 4.25, "GBP": 5.00, "JPY": 0.10,
    "CHF": 1.75, "AUD": 4.35, "CAD": 5.00, "NZD": 5.50,
}

def compute_carry_signal(pair):
    """
    Forward discount = r_f - r_d (from PDF eq 442-443).
    Positive = earn carry going long. Negative = pay carry going long.
    """
    parts = pair.split("_")
    if len(parts) != 2:
        return 0
    base, quote = parts
    r_base = APPROX_RATES.get(base, 3.0)
    r_quote = APPROX_RATES.get(quote, 3.0)
    return r_base - r_quote  # positive → long base is positive carry


# ── Session Filter ────────────────────────────────────────────────────────────
def is_good_session():
    """
    Best FX liquidity: London (8AM-4PM GMT) + NY overlap (1PM-5PM GMT).
    Avoid Asian session for majors (wider spreads, lower volume).
    Returns True if currently in a preferred trading session.
    """
    now_utc = datetime.now(timezone.utc)
    hour = now_utc.hour
    weekday = now_utc.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    # No trading weekends
    if weekday >= 5:
        return False

    # London session: 8AM–4PM GMT (8–16 UTC)
    # NY session: 1PM–9PM GMT (13–21 UTC)
    # Overlap (best): 1PM–4PM GMT (13–16 UTC)
    in_london = 8 <= hour < 16
    in_ny = 13 <= hour < 21
    return in_london or in_ny


# ── Main Scan ─────────────────────────────────────────────────────────────────
def scan_pair(pair):
    """
    Full scan of one pair. Returns a candidate dict or None if filtered out.
    """
    # Fetch candles (H4 for signal, use 100 bars)
    candles = get_candles(pair, granularity="H4", count=100)
    if isinstance(candles, dict) and "error" in candles:
        return {"pair": pair, "error": candles["error"], "signal": "ERROR"}

    closes, highs, lows, opens = parse_candles(candles)
    if len(closes) < 30:
        return {"pair": pair, "signal": "INSUFFICIENT_DATA"}

    # Current price
    price_data = get_current_price(pair)
    if not price_data or "error" in price_data:
        return {"pair": pair, "signal": "PRICE_ERROR"}

    current = price_data["mid"]
    bid = price_data["bid"]
    ask = price_data["ask"]

    # ── Spread check (G8 guardrail) ────────────────────────────────────────
    spread_pips = compute_spread_pips(pair, bid, ask)
    max_spread = MAX_SPREAD_PIPS.get(pair, 3.0)
    spread_ok = spread_pips <= max_spread

    # ── Technical indicators ────────────────────────────────────────────────
    ma20  = compute_sma(closes, 20)
    ma50  = compute_sma(closes, 50)
    ma200 = compute_sma(closes, 200) if len(closes) >= 200 else None
    ema20 = compute_ema(closes, 20)
    atr14 = compute_atr(highs, lows, closes, 14)
    rsi14 = compute_rsi(closes, 14)
    rsi2  = compute_rsi(closes, 2)
    macd_line, signal_line, histogram = compute_macd(closes)
    momentum = compute_momentum_score(closes, 20)

    # MA alignment
    above_ma20  = current > ma20 if ma20 else None
    above_ma50  = current > ma50 if ma50 else None
    above_ma200 = current > ma200 if ma200 else None
    ma_bullish  = (ma20 > ma50) if (ma20 and ma50) else None
    ma_bearish  = (ma20 < ma50) if (ma20 and ma50) else None

    # MACD signal
    macd_bullish = (macd_line > signal_line) if (macd_line and signal_line) else None

    # IBS (from strategies KB — §4.4)
    if len(highs) > 0 and len(lows) > 0:
        ibs = (closes[-1] - lows[-1]) / (highs[-1] - lows[-1]) if highs[-1] != lows[-1] else 0.5
    else:
        ibs = 0.5

    # ── Carry ───────────────────────────────────────────────────────────────
    carry = compute_carry_signal(pair)

    # ── Direction signal ────────────────────────────────────────────────────
    # LONG signal: momentum > 0 + MA bullish + carry > 0 + MACD bullish
    # SHORT signal: momentum < 0 + MA bearish + carry < 0 + MACD bearish
    long_signals  = sum([
        momentum > 0.02,
        ma_bullish is True,
        above_ma20 is True,
        macd_bullish is True,
        carry > 0.5,
        ibs < 0.3,   # oversold = pullback buy
    ])
    short_signals = sum([
        momentum < -0.02,
        ma_bearish is True,
        above_ma20 is False,
        macd_bullish is False,
        carry < -0.5,
        ibs > 0.7,   # overbought = pullback sell
    ])

    if long_signals >= 3:
        direction = "LONG"
        confluence = long_signals
    elif short_signals >= 3:
        direction = "SHORT"
        confluence = short_signals
    else:
        direction = "NEUTRAL"
        confluence = max(long_signals, short_signals)

    return {
        "pair": pair,
        "signal": direction,
        "confluence": confluence,
        "spread_pips": round(spread_pips, 2),
        "spread_ok": spread_ok,
        "current_price": round(current, 5),
        "carry_pct": round(carry, 2),
        "momentum": round(momentum, 4),
        "rsi14": round(rsi14, 1) if rsi14 else None,
        "rsi2": round(rsi2, 1) if rsi2 else None,
        "ibs": round(ibs, 3),
        "atr14": round(atr14, 6) if atr14 else None,
        "atr14_pips": round(atr14 / get_pip_size(pair), 1) if atr14 else None,
        "ma20": round(ma20, 5) if ma20 else None,
        "ma50": round(ma50, 5) if ma50 else None,
        "above_ma20": above_ma20,
        "above_ma50": above_ma50,
        "above_ma200": above_ma200,
        "ma_bullish": ma_bullish,
        "macd_bullish": macd_bullish,
        "macd_line": round(macd_line, 6) if macd_line else None,
        "histogram": round(histogram, 6) if histogram else None,
        "session_ok": is_good_session(),
    }


def scan_all(pairs=None):
    _check_auth()
    pairs = pairs or ALL_PAIRS

    results = []
    for pair in pairs:
        result = scan_pair(pair)
        results.append(result)

    # Sort: LONG/SHORT first, by confluence desc, then NEUTRAL
    def sort_key(r):
        sig = r.get("signal", "NEUTRAL")
        priority = 0 if sig in ("LONG", "SHORT") else 1
        return (priority, -r.get("confluence", 0))

    results.sort(key=sort_key)

    # Filter: only tradeable candidates
    candidates = [r for r in results if r.get("signal") in ("LONG", "SHORT") and r.get("spread_ok", False)]

    output = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "session_ok": is_good_session(),
        "environment": OANDA_ENV,
        "total_pairs_scanned": len(results),
        "candidates": candidates,
        "all_results": results,
    }

    today = date.today().isoformat()
    path = os.path.join(JOURNAL_DIR, f"{today}_scan.json")
    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    print(json.dumps(output, indent=2))
    return output


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if cmd == "scan":
        scan_all()

    elif cmd == "pair":
        if len(sys.argv) < 3:
            print("Usage: forex_screener.py pair EUR_USD")
            sys.exit(1)
        pair = sys.argv[2].upper()
        result = scan_pair(pair)
        print(json.dumps(result, indent=2))

    elif cmd == "read":
        today = date.today().isoformat()
        path = os.path.join(JOURNAL_DIR, f"{today}_scan.json")
        if os.path.exists(path):
            with open(path) as f:
                print(f.read())
        else:
            print(json.dumps({"error": f"No scan found for {today}"}))

    elif cmd == "account":
        _check_auth()
        print(json.dumps(get_account_summary(), indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
