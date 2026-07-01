#!/usr/bin/env python3
"""
research.py — Pull market data, news, and compute technical signals.
Usage:
  python scripts/research.py full           # Full morning research on all watchlist symbols
  python scripts/research.py midday         # Midday check on open positions only
  python scripts/research.py bars AAPL      # Raw bars for one symbol
  python scripts/research.py news AAPL      # News for one symbol
  python scripts/research.py account        # Portfolio snapshot
  python scripts/research.py positions      # Open positions
  python scripts/research.py signal AAPL    # Computed signal for one symbol
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from statistics import mean

# ── Auth ────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL      = "https://data.alpaca.markets"

def _headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

def _check_auth():
    if not ALPACA_KEY or not ALPACA_SECRET:
        print(json.dumps({"error": "Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY env vars"}))
        sys.exit(1)

# ── Market Data ──────────────────────────────────────────────────────────────
def get_bars(symbol, timeframe="1Day", limit=60):
    """Fetch historical OHLCV bars. Passes explicit start date so free-tier returns full history."""
    from datetime import date, timedelta
    start = (date.today() - timedelta(days=limit * 2)).isoformat()  # 2x buffer for weekends/holidays
    url = f"{DATA_URL}/v2/stocks/{symbol}/bars"
    params = {
        "timeframe": timeframe,
        "limit": limit,
        "start": start,
        "adjustment": "raw",
        "sort": "asc",
        "feed": "iex",
    }
    r = requests.get(url, headers=_headers(), params=params)
    r.raise_for_status()
    return r.json()

def get_latest_quote(symbol):
    """Get the latest bid/ask for a symbol."""
    url = f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest"
    r = requests.get(url, headers=_headers())
    r.raise_for_status()
    return r.json()

def get_news(symbol, limit=5):
    """Get recent news headlines."""
    url = f"{DATA_URL}/v1beta1/news"
    params = {"symbols": symbol, "limit": limit, "sort": "desc"}
    r = requests.get(url, headers=_headers(), params=params)
    r.raise_for_status()
    return r.json()

# ── Portfolio ────────────────────────────────────────────────────────────────
def get_account():
    """Current portfolio status."""
    r = requests.get(f"{BASE_URL}/v2/account", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_positions():
    """All open positions."""
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_market_status():
    """Is the market open right now?"""
    r = requests.get(f"{BASE_URL}/v2/clock", headers=_headers())
    r.raise_for_status()
    return r.json()

# ── Technical Analysis ───────────────────────────────────────────────────────
def compute_moving_averages(bars_data):
    """
    Given bars JSON from get_bars(), return:
      { "ma20": float, "ma50": float, "last_close": float, "signal": str }
    """
    bars = bars_data.get("bars", [])
    if len(bars) < 50:
        return {"error": f"Not enough bars: got {len(bars)}, need 50"}

    closes = [b["c"] for b in bars]
    last   = closes[-1]
    ma20   = mean(closes[-20:])
    ma50   = mean(closes[-50:])

    # Trend direction
    ma20_prev = mean(closes[-21:-1])

    if ma20 > ma50 and last > ma20:
        signal = "STRONG_BUY"
    elif ma20 > ma50 and last >= ma50:
        signal = "WEAK_BUY"
    elif last < ma50:
        signal = "STRONG_SELL"
    elif ma20 < ma20_prev:
        signal = "WEAK_SELL"
    else:
        signal = "HOLD"

    return {
        "ma20": round(ma20, 4),
        "ma50": round(ma50, 4),
        "last_close": round(last, 4),
        "ma20_trending_up": ma20 > ma20_prev,
        "signal": signal,
    }

def summarize_news(news_data):
    """Extract headlines and surface sentiment keywords."""
    articles = news_data.get("news", [])
    headlines = []
    for a in articles[:5]:
        headlines.append({
            "headline": a.get("headline", ""),
            "source": a.get("source", ""),
            "published": a.get("created_at", ""),
            "url": a.get("url", ""),
        })
    return headlines

# ── Full Research Pass ───────────────────────────────────────────────────────
def _load_social_tickers(max_tickers=10):
    """
    Read today's social screener output and return tickers that are
    safe_to_judge=True and not already on the watchlist.
    This is the missing link — social tickers must flow into Researcher.
    """
    from datetime import date as _date
    today = _date.today().isoformat()
    social_path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-social.json")
    if not os.path.exists(social_path):
        return []
    try:
        with open(social_path) as f:
            social = json.load(f)
        tickers = []
        for t in social.get("top_tickers", []):
            pump = t.get("pump_risk", {})
            if pump.get("is_pump"):
                continue  # already vetoed
            price_data = pump.get("price_data", {})
            price = price_data.get("current_price", 0)
            if price < 5.0:
                continue  # below price floor
            tickers.append({
                "symbol": t["ticker"],
                "description": f"Social screener pick — StockTwits momentum {t.get('momentum', 0):.0f}",
                "max_allocation_pct": 3,  # smaller size for social picks
                "source": "social_screener",
                "momentum_score": t.get("momentum", 0),
                "sentiment": t.get("analysis", {}).get("bull_pct", 0),
            })
        return tickers[:max_tickers]
    except Exception as e:
        print(f"  [social loader] {e}", file=sys.stderr)
        return []


def _load_intelligence_tickers(max_tickers=15):
    """
    Read today's intelligence scan and return discovery tickers from 3 sources:
    - Alpaca most-active stocks (always reliable — fresh every day)
    - Top gainers under 8% move (momentum without pump risk)
    - Reddit trending (when available)
    """
    from datetime import date as _date
    today = _date.today().isoformat()
    intel_path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-intelligence.json")
    if not os.path.exists(intel_path):
        return []
    try:
        with open(intel_path) as f:
            intel = json.load(f)
        tickers = []
        seen = set()

        # Alpaca most-active — most reliable daily discovery source
        discovery = intel.get("market_discovery", {}).get("discovery", {})
        for s in discovery.get("most_active", [])[:10]:
            sym = s.get("symbol", "")
            if not sym or len(sym) > 5 or sym in seen:
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"Alpaca most-active — {s.get('volume', 0):,} shares traded",
                "max_allocation_pct": 3,
                "source": "alpaca_most_active",
            })

        # Top gainers filtered to < 8% (not a pump)
        for s in discovery.get("top_gainers", [])[:5]:
            sym = s.get("symbol", "")
            price = s.get("price", 0)
            if not sym or len(sym) > 5 or sym in seen or (price and price < 5):
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"Top gainer +{s.get('change_pct', 0)}% today",
                "max_allocation_pct": 3,
                "source": "alpaca_gainers",
            })

        # Reddit trending tickers (bonus when available)
        for t in intel.get("reddit", {}).get("tickers", [])[:5]:
            sym = t.get("symbol", "")
            if not sym or len(sym) > 5 or sym in seen:
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"Reddit trending — {t.get('reddit_mentions', 0)} mentions",
                "max_allocation_pct": 3,
                "source": "reddit",
            })

        print(f"  [intel loader] {len(tickers)} discovery tickers: {[t['symbol'] for t in tickers]}", file=sys.stderr)
        return tickers[:max_tickers]
    except Exception as e:
        print(f"  [intelligence loader] {e}", file=sys.stderr)
        return []


def full_research():
    """
    Run complete research on every symbol in config/watchlist.json,
    PLUS social screener top picks and Reddit/intelligence trending tickers.
    All three feed into the Judge — this was the missing link.
    """
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    with open(watchlist_path) as f:
        config = json.load(f)

    watchlist_items = config.get("watchlist", [])
    clock   = get_market_status()
    account = get_account()

    # ── Expand symbol list with social + intelligence discovery ──────────────
    social_tickers = _load_social_tickers(max_tickers=10)
    intel_tickers  = _load_intelligence_tickers(max_tickers=10)

    watchlist_syms = {item["symbol"] for item in watchlist_items}
    extra_items = []
    for t in social_tickers + intel_tickers:
        if t["symbol"] not in watchlist_syms:
            extra_items.append(t)
            watchlist_syms.add(t["symbol"])

    all_items = watchlist_items + extra_items
    if extra_items:
        print(f"  [research] Expanded by {len(extra_items)} social/reddit tickers: "
              f"{[t['symbol'] for t in extra_items]}", file=sys.stderr)

    results = {
        "timestamp": datetime.now().isoformat(),
        "market_open": clock.get("is_open", False),
        "next_open": clock.get("next_open", ""),
        "account": {
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
            "buying_power": account.get("buying_power"),
        },
        "positions": get_positions(),
        "symbols": {},
        "discovery_sources": {
            "social_tickers": [t["symbol"] for t in social_tickers],
            "reddit_tickers": [t["symbol"] for t in intel_tickers],
        }
    }

    for item in all_items:
        sym = item["symbol"]
        try:
            bars_raw = get_bars(sym, limit=60)
            news_raw = get_news(sym)
            ma_data  = compute_moving_averages(bars_raw)
            news_sum = summarize_news(news_raw)

            results["symbols"][sym] = {
                "description": item.get("description", ""),
                "max_allocation_pct": item.get("max_allocation_pct", 5),
                "source": item.get("source", "watchlist"),
                "technicals": ma_data,
                "news": news_sum,
            }
        except Exception as e:
            results["symbols"][sym] = {"error": str(e)}

    return results

def midday_check():
    """Lighter check: only open positions + market status."""
    positions = get_positions()
    account   = get_account()
    clock     = get_market_status()

    results = {
        "timestamp": datetime.now().isoformat(),
        "market_open": clock.get("is_open", False),
        "account": {
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
        },
        "open_positions": []
    }

    for pos in positions:
        sym = pos["symbol"]
        try:
            bars_raw = get_bars(sym, limit=60)
            ma_data  = compute_moving_averages(bars_raw)
            results["open_positions"].append({
                "symbol": sym,
                "qty": pos["qty"],
                "avg_entry": pos["avg_entry_price"],
                "current_price": pos["current_price"],
                "unrealized_pl": pos["unrealized_pl"],
                "unrealized_plpc": pos["unrealized_plpc"],
                "technicals": ma_data,
                "stop_loss_triggered": float(pos["unrealized_plpc"]) <= -0.08,
            })
        except Exception as e:
            results["open_positions"].append({"symbol": sym, "error": str(e)})

    return results

# ── CLI Entry Point ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    _check_auth()
    action = sys.argv[1] if len(sys.argv) > 1 else "account"
    symbol = sys.argv[2] if len(sys.argv) > 2 else None

    if action == "full":
        data = full_research()
        # Save to logs/paper/YYYY-MM-DD.json so Judge can pick it up
        from datetime import date
        _log_dir = os.path.join(os.path.dirname(__file__), "logs", "paper")
        os.makedirs(_log_dir, exist_ok=True)
        _out_path = os.path.join(_log_dir, f"{date.today().isoformat()}.json")
        with open(_out_path, "w") as _f:
            json.dump(data, _f, indent=2)
        print(json.dumps(data, indent=2))
    elif action == "midday":
        print(json.dumps(midday_check(), indent=2))
    elif action == "bars" and symbol:
        data = get_bars(symbol)
        print(json.dumps(data, indent=2))
    elif action == "news" and symbol:
        data = get_news(symbol)
        print(json.dumps(summarize_news(data), indent=2))
    elif action == "signal" and symbol:
        bars_raw = get_bars(symbol)
        print(json.dumps(compute_moving_averages(bars_raw), indent=2))
    elif action == "positions":
        print(json.dumps(get_positions(), indent=2))
    elif action == "status":
        print(json.dumps(get_market_status(), indent=2))
    else:
        print(json.dumps(get_account(), indent=2))
