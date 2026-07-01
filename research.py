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
def compute_technicals(bars_data):
    """
    Full technical analysis suite:
    MA20/MA50, RSI(14), MACD, Bollinger Bands, Volume spike, ATR.
    Returns a rich signal dict the Judge uses for scoring.
    """
    bars = bars_data.get("bars", [])
    if len(bars) < 50:
        return {"error": f"Not enough bars: got {len(bars)}, need 50"}

    closes  = [b["c"] for b in bars]
    highs   = [b["h"] for b in bars]
    lows    = [b["l"] for b in bars]
    volumes = [b["v"] for b in bars]
    last    = closes[-1]

    # ── Moving Averages ───────────────────────────────────────────────────
    ma20      = mean(closes[-20:])
    ma50      = mean(closes[-50:])
    ma20_prev = mean(closes[-21:-1])

    # ── RSI(14) ───────────────────────────────────────────────────────────
    def _rsi(prices, period=14):
        gains, losses = [], []
        for i in range(1, len(prices)):
            d = prices[i] - prices[i-1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        if len(gains) < period:
            return 50.0
        avg_gain = mean(gains[-period:])
        avg_loss = mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    rsi = _rsi(closes[-30:])

    # ── MACD (12/26/9) ────────────────────────────────────────────────────
    def _ema(prices, period):
        k = 2 / (period + 1)
        ema = prices[0]
        for p in prices[1:]:
            ema = p * k + ema * (1 - k)
        return ema

    ema12       = _ema(closes[-40:], 12)
    ema26       = _ema(closes[-40:], 26)
    macd_line   = ema12 - ema26
    # Signal line = 9-period EMA of MACD — approximate with recent MACD values
    macd_signal = macd_line * 0.9  # simplified; full implementation needs history
    macd_hist   = macd_line - macd_signal
    macd_bullish = macd_line > 0 and macd_hist > 0

    # ── Bollinger Bands (20, 2σ) ──────────────────────────────────────────
    bb_mid   = ma20
    bb_std   = (sum((c - bb_mid) ** 2 for c in closes[-20:]) / 20) ** 0.5
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_pct   = round((last - bb_lower) / (bb_upper - bb_lower) * 100, 1) if bb_upper != bb_lower else 50.0
    # <20% = near lower band (oversold/buy zone), >80% = near upper band (overbought)

    # ── Volume Spike ──────────────────────────────────────────────────────
    vol_avg20   = mean(volumes[-20:])
    vol_today   = volumes[-1]
    vol_ratio   = round(vol_today / vol_avg20, 2) if vol_avg20 > 0 else 1.0
    vol_spike   = vol_ratio > 1.5  # 50% above avg = institutional interest

    # ── ATR(14) — Average True Range ─────────────────────────────────────
    true_ranges = []
    for i in range(1, len(bars)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        true_ranges.append(tr)
    atr = round(mean(true_ranges[-14:]), 4) if len(true_ranges) >= 14 else None
    atr_pct = round(atr / last * 100, 2) if atr and last else None  # ATR as % of price

    # ── MA Signal ─────────────────────────────────────────────────────────
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

    # ── Entry quality score (0–100) ───────────────────────────────────────
    # Combines RSI, BB position, MACD, volume for a single entry quality number
    entry_score = 0
    if rsi < 45:       entry_score += 30   # not overbought
    elif rsi < 55:     entry_score += 15
    if bb_pct < 40:    entry_score += 25   # near lower band = better entry
    elif bb_pct < 60:  entry_score += 10
    if macd_bullish:   entry_score += 25   # momentum confirming
    if vol_spike:      entry_score += 20   # institutional volume

    return {
        "ma20":             round(ma20, 4),
        "ma50":             round(ma50, 4),
        "last_close":       round(last, 4),
        "ma20_trending_up": ma20 > ma20_prev,
        "signal":           signal,
        "rsi":              rsi,
        "rsi_zone":         "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else "NEUTRAL",
        "macd_bullish":     macd_bullish,
        "macd_line":        round(macd_line, 4),
        "bb_upper":         round(bb_upper, 4),
        "bb_lower":         round(bb_lower, 4),
        "bb_pct":           bb_pct,
        "bb_zone":          "BUY_ZONE" if bb_pct < 25 else "OVERBOUGHT_ZONE" if bb_pct > 75 else "MID",
        "volume_ratio":     vol_ratio,
        "volume_spike":     vol_spike,
        "atr":              atr,
        "atr_pct":          atr_pct,
        "entry_score":      entry_score,
        "entry_quality":    "EXCELLENT" if entry_score >= 70 else "GOOD" if entry_score >= 45 else "FAIR" if entry_score >= 25 else "POOR",
        "limit_price":      round(ma20 * 0.995, 2),  # target entry per CLAUDE.md rule
    }


def compute_moving_averages(bars_data):
    """Legacy wrapper — calls compute_technicals for backwards compatibility."""
    return compute_technicals(bars_data)

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


def _load_screener_tickers(max_tickers=15):
    """Load top candidates from today's full market screener output."""
    from datetime import date as _date
    today = _date.today().isoformat()
    path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-screener.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        tickers = []
        seen = set()

        # Top market candidates by volume
        for c in data.get("top_candidates", [])[:max_tickers]:
            sym = c.get("symbol", "")
            if not sym or sym in seen:
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"Market screener — ${c.get('price', 0):.2f}, {c.get('pct_change', 0):+.1f}% today, vol {c.get('volume', 0):,}",
                "max_allocation_pct": 3,
                "source": "market_screener",
            })

        # Tickers hot in news firehose
        for t in data.get("news_firehose", {}).get("hot_tickers", [])[:5]:
            sym = t.get("symbol", "")
            if not sym or sym in seen or len(sym) > 5:
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"News firehose — {t.get('mentions', 0)} mentions across 10 sources",
                "max_allocation_pct": 3,
                "source": "news_firehose",
            })

        # Congressional buys
        for t in data.get("congressional", {}).get("congressional_buys", [])[:3]:
            sym = t.get("symbol", "")
            if not sym or sym in seen or len(sym) > 5:
                continue
            seen.add(sym)
            tickers.append({
                "symbol": sym,
                "description": f"Congressional buy — {t.get('member', '')} bought {t.get('amount', '')} on {t.get('date', '')}",
                "max_allocation_pct": 3,
                "source": "congressional",
            })

        print(f"  [screener loader] {len(tickers)} tickers from market screener", file=sys.stderr)
        return tickers
    except Exception as e:
        print(f"  [screener loader] {e}", file=sys.stderr)
        return []


def full_research():
    """
    Run complete research on every symbol in watchlist.json,
    PLUS social screener, intelligence, and full market screener candidates.
    All sources feed into the Judge.
    """
    watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.json")
    with open(watchlist_path) as f:
        config = json.load(f)

    watchlist_items = config.get("watchlist", [])
    clock   = get_market_status()
    account = get_account()

    # ── Expand symbol list from all discovery sources ────────────────────────
    social_tickers   = _load_social_tickers(max_tickers=10)
    intel_tickers    = _load_intelligence_tickers(max_tickers=15)
    screener_tickers = _load_screener_tickers(max_tickers=15)

    watchlist_syms = {item["symbol"] for item in watchlist_items}
    extra_items = []
    for t in social_tickers + intel_tickers + screener_tickers:
        if t["symbol"] not in watchlist_syms:
            extra_items.append(t)
            watchlist_syms.add(t["symbol"])

    all_items = watchlist_items + extra_items
    print(f"  [research] Total symbols to research: {len(all_items)} "
          f"(watchlist: {len(watchlist_items)}, discovered: {len(extra_items)})", file=sys.stderr)
    if extra_items:
        print(f"  [research] Discovered: {[t['symbol'] for t in extra_items]}", file=sys.stderr)

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
            bars_raw    = get_bars(sym, limit=60)
            news_raw    = get_news(sym)
            tech_data   = compute_technicals(bars_raw)
            news_sum    = summarize_news(news_raw)
            fundamentals = get_yahoo_fundamentals(sym)

            results["symbols"][sym] = {
                "description":      item.get("description", ""),
                "max_allocation_pct": item.get("max_allocation_pct", 5),
                "source":           item.get("source", "watchlist"),
                "technicals":       tech_data,
                "news":             news_sum,
                "fundamentals":     fundamentals,
            }
        except Exception as e:
            results["symbols"][sym] = {"error": str(e)}

    return results


def get_yahoo_fundamentals(symbol):
    """
    Pull key fundamentals from Yahoo Finance — free, no API key.
    Returns P/E, EPS, revenue growth, analyst target, short float.
    """
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"
        params = {"modules": "summaryDetail,defaultKeyStatistics,financialData,recommendationTrend"}
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        if r.status_code != 200:
            return {"error": f"Yahoo Finance {r.status_code}"}
        data = r.json().get("quoteSummary", {}).get("result", [{}])[0]

        summary   = data.get("summaryDetail", {})
        keystats  = data.get("defaultKeyStatistics", {})
        findata   = data.get("financialData", {})
        rec       = data.get("recommendationTrend", {}).get("trend", [{}])

        def _val(d, k):
            v = d.get(k, {})
            return v.get("fmt") or v.get("raw")

        analyst_rec = rec[0].get("buy", 0) if rec else 0
        analyst_sell = rec[0].get("sell", 0) if rec else 0

        return {
            "pe_ratio":          _val(summary, "trailingPE"),
            "forward_pe":        _val(summary, "forwardPE"),
            "eps_ttm":           _val(keystats, "trailingEps"),
            "revenue_growth":    _val(findata, "revenueGrowth"),
            "profit_margin":     _val(findata, "profitMargins"),
            "analyst_target":    _val(findata, "targetMeanPrice"),
            "current_price":     _val(findata, "currentPrice"),
            "short_float":       _val(keystats, "shortPercentOfFloat"),
            "analyst_buy_count": analyst_rec,
            "analyst_sell_count": analyst_sell,
            "upside_pct":        round(
                (float(_val(findata, "targetMeanPrice") or 0) /
                 float(_val(findata, "currentPrice") or 1) - 1) * 100, 1
            ) if _val(findata, "targetMeanPrice") and _val(findata, "currentPrice") else None,
        }
    except Exception as e:
        return {"error": str(e)}

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
