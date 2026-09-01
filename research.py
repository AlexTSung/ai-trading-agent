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
from itertools import zip_longest
from statistics import mean

# ── Load .env ────────────────────────────────────────────────────────────────
# setup.sh writes a .env file, but nothing used to read it, so filling in keys
# there had no effect. Environment variables already set (GitHub Secrets, MCP
# config, an exporting shell) always win — load_dotenv does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars may be supplied by the shell

from version import get_version
from market_time import et_today_iso, et_now

# ── Auth ────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")

# Citadel volatility sizing thresholds (CLAUDE.md "Volatility Position Sizing").
# Evaluated against the real VIX index level from FRED, not a VIXY share price.
VIX_HALF_SIZE        = 25.0   # above this, halve position sizes
VIX_NO_NEW_POSITIONS = 35.0   # above this, manage existing only
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
# ── Entry limit reachability ──────────────────────────────────────────────────
# Thresholds in ATR units. ATR is the average daily range, so a gap measured in
# ATRs is roughly "how many normal days of movement away is this price".
LIMIT_LIKELY_ATR    = 1.0    # inside one normal day's range
LIMIT_PLAUSIBLE_ATR = 2.5    # a few days of drift would reach it


def entry_limit_reachability(last: float, limit_price: float, atr: float):
    """
    How likely a resting entry limit is to actually trade.

    Returns (gap_pct, atr_distance, label).

    The limit price itself is CLAUDE.md Hard Rule 2's formula and is not changed
    here. What this adds is a measure of whether that price is reachable: a
    stock 6% above its MA20 needs a 6.5% pullback to fill, and a bid at that
    level may simply never be touched.

    A percentage gap is not comparable across underlyings — 5% is five normal
    days for a 1%-ATR utility and just over one day for a 4%-ATR growth name.
    ATR normalises that.

    A missing or zero ATR yields "UNKNOWN" rather than an optimistic guess.
    Fail-closed on missing data is the rule a $0.03 stock broke once already.
    """
    if not isinstance(last, (int, float)) or last <= 0:
        return None, None, "UNKNOWN"
    if not isinstance(limit_price, (int, float)) or limit_price <= 0:
        return None, None, "UNKNOWN"

    gap_pct = round((last - limit_price) / last * 100, 2)

    if isinstance(atr, (int, float)) and atr > 0:
        atr_distance = round((last - limit_price) / atr, 2)
    else:
        atr_distance = None

    if gap_pct <= 0:
        # Already at or below the target — this is a value entry, not a wait.
        return gap_pct, atr_distance, "AT_OR_BELOW"
    if atr_distance is None:
        return gap_pct, atr_distance, "UNKNOWN"
    if atr_distance <= LIMIT_LIKELY_ATR:
        return gap_pct, atr_distance, "LIKELY"
    if atr_distance <= LIMIT_PLAUSIBLE_ATR:
        return gap_pct, atr_distance, "PLAUSIBLE"
    return gap_pct, atr_distance, "UNLIKELY"


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

    # ── Pre-drop strength (used by falling knife detection in judge.py) ──────
    prev_close          = closes[-2] if len(closes) >= 2 else last
    was_above_ma50_yest = prev_close > ma50
    was_above_ma20_yest = prev_close > ma20

    # Count consecutive down closes ending yesterday (not counting today)
    consec_down = 0
    for i in range(len(closes) - 2, max(len(closes) - 8, 0), -1):
        if closes[i] < closes[i - 1]:
            consec_down += 1
        else:
            break

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

    # ── Entry limit and how reachable it is ───────────────────────────────────
    # The limit itself is CLAUDE.md Hard Rule 2's formula, unchanged: MA20 x
    # 0.995, i.e. 0.5% below MA20 support. What was missing is any measure of
    # whether that price is actually likely to trade. A stock 6% above its MA20
    # needs a 6.5% pullback to fill, and a day-limit order at that level simply
    # expires unfilled every session — which is what cancel_orphan_exits exists
    # to clean up after.
    #
    # ATR is the natural yardstick: it is the average daily range, so the gap to
    # the limit measured in ATRs is roughly "how many normal days of movement
    # away is this". A gap inside 1 ATR is ordinary noise and fills often; past
    # ~2.5 ATR it needs a genuine trend change, not a wiggle. Expressing the gap
    # in percent alone is not comparable across a 1%-ATR utility and a 4%-ATR
    # growth name.
    limit_price = round(ma20 * 0.995, 2)
    limit_gap_pct, limit_atr_distance, reachability = entry_limit_reachability(
        last, limit_price, atr)

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
        "entry_score":          entry_score,
        "entry_quality":        "EXCELLENT" if entry_score >= 70 else "GOOD" if entry_score >= 45 else "FAIR" if entry_score >= 25 else "POOR",
        "limit_price":          limit_price,
        "limit_gap_pct":        limit_gap_pct,
        "limit_atr_distance":   limit_atr_distance,
        "limit_reachability":   reachability,
        "prev_close":           round(prev_close, 4),
        "was_above_ma50_yest":  was_above_ma50_yest,
        "was_above_ma20_yest":  was_above_ma20_yest,
        "consec_down_days":     consec_down,
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
    today = et_today_iso()
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
    today = et_today_iso()
    intel_path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-intelligence.json")
    if not os.path.exists(intel_path):
        return []
    return _extract_discovery_tickers(intel_path, max_tickers=max_tickers)


def _intel_path_today():
    return os.path.join(os.path.dirname(__file__), "logs", "paper",
                        f"{et_today_iso()}-intelligence.json")


def load_intelligence_context() -> dict:
    """
    Surface the parts of the morning intelligence run that the pipeline was
    throwing away.

    intelligence.py fetches the VIX (FRED VIXCLS), a fear/greed reading, an
    earnings watch and SEC Form 4 insider filings every morning, but research
    previously opened that file only to harvest discovery tickers. Everything
    else was written to disk and discarded, which is why the Judge's volatility
    criterion reported "VIX proxy unavailable" and handed out a free point to
    every candidate, and why the earnings blackout ran on headlines alone.
    """
    ctx = {
        "available": False,
        "vix": None,
        "vix_date": None,
        "vix_regime": "UNKNOWN",
        "size_multiplier": 1.0,
        "fear_greed_score": None,
        "fear_greed_rating": None,
        "earnings_watch_symbols": [],
        "insider_buy_symbols": [],
        "notes": [],
    }

    path = _intel_path_today()
    if not os.path.exists(path):
        ctx["notes"].append("No intelligence file for today — run intelligence.py morning")
        return ctx

    try:
        with open(path) as f:
            intel = json.load(f)
    except (OSError, ValueError) as e:
        ctx["notes"].append(f"Intelligence file unreadable: {e}")
        return ctx

    ctx["available"] = True

    # ── VIX ───────────────────────────────────────────────────────────────────
    # get_fear_greed() is misleadingly named: it returns the real VIX close from
    # FRED (VIXCLS) alongside its 0-100 mapping. That is the authoritative level
    # for the Citadel sizing thresholds in CLAUDE.md. get_vix() only returns a
    # VIXY share price, which is not comparable to a VIX level.
    fg = intel.get("fear_greed") or {}
    vix = fg.get("vix")
    if isinstance(vix, (int, float)):
        ctx["vix"] = float(vix)
        ctx["vix_date"] = fg.get("vix_date")
        ctx["fear_greed_score"] = fg.get("score")
        ctx["fear_greed_rating"] = fg.get("rating")
        if vix > VIX_NO_NEW_POSITIONS:
            ctx["vix_regime"] = "EXTREME"
            ctx["size_multiplier"] = 0.0
        elif vix > VIX_HALF_SIZE:
            ctx["vix_regime"] = "ELEVATED"
            ctx["size_multiplier"] = 0.5
        else:
            ctx["vix_regime"] = "NORMAL"
            ctx["size_multiplier"] = 1.0
    else:
        ctx["notes"].append("VIX unavailable in intelligence output")

    # ── Earnings watch ────────────────────────────────────────────────────────
    # Carry the triggering headline, not just the symbol. The Judge re-checks it
    # with the same forward/backward-looking filter it applies to a symbol's own
    # news, so a loose upstream keyword match cannot veto on its own.
    ew = intel.get("earnings_watch") or {}
    ctx["earnings_watch_symbols"] = sorted({
        item.get("symbol") for item in ew.get("earnings_watch", [])
        if item.get("symbol")
    })
    ctx["earnings_watch_detail"] = {
        item["symbol"]: item.get("headline", "")
        for item in ew.get("earnings_watch", []) if item.get("symbol")
    }

    # ── Insider buying (SEC EDGAR Form 4) ─────────────────────────────────────
    ia = intel.get("insider_activity") or {}
    ctx["insider_buy_symbols"] = sorted({
        f.get("symbol") for f in ia.get("insider_filings", [])
        if f.get("symbol")
    })

    return ctx


def _extract_discovery_tickers(intel_path, max_tickers=15):
    """
    Harvest discovery tickers from the intelligence file.

    Was called as _extract_discovery_tickers(intel_path) with no max_tickers
    argument, while this function referenced the name unconditionally at
    `return tickers[:max_tickers]`. Every call raised NameError, and the
    broad `except Exception` below swallowed it and returned []. Alpaca
    most-active, top gainers, and Reddit trending — an entire discovery
    channel from the morning intelligence scan — silently reached the Judge
    as zero tickers, every single day. Reproduced directly: calling this
    with a minimal fake intelligence.json logs "2 discovery tickers: [...]"
    immediately followed by "name 'max_tickers' is not defined" and returns
    [] regardless of what was found.

    Also widens the per-source slices to match what intelligence.py's
    get_market_discovery() already fetches (top=20 most-active, top=10
    movers) — the old [:10]/[:5] discarded roughly half of data already
    paid for with an API call, for no reason tied to any risk control.
    """
    if not os.path.exists(intel_path):
        return []
    try:
        with open(intel_path) as f:
            intel = json.load(f)
        seen = set()

        # Alpaca most-active — most reliable daily discovery source
        discovery = intel.get("market_discovery", {}).get("discovery", {})
        most_active = []
        for s in discovery.get("most_active", [])[:20]:
            sym = s.get("symbol", "")
            if not sym or len(sym) > 5 or sym in seen:
                continue
            seen.add(sym)
            most_active.append({
                "symbol": sym,
                "description": f"Alpaca most-active — {s.get('volume', 0):,} shares traded",
                "max_allocation_pct": 3,
                "source": "alpaca_most_active",
            })

        # Top gainers filtered to < 8% (not a pump)
        gainers = []
        for s in discovery.get("top_gainers", [])[:10]:
            sym = s.get("symbol", "")
            price = s.get("price", 0)
            if not sym or len(sym) > 5 or sym in seen or (price and price < 5):
                continue
            seen.add(sym)
            gainers.append({
                "symbol": sym,
                "description": f"Top gainer +{s.get('change_pct', 0)}% today",
                "max_allocation_pct": 3,
                "source": "alpaca_gainers",
            })

        # Reddit trending tickers (bonus when available)
        reddit = []
        for t in intel.get("reddit", {}).get("tickers", [])[:5]:
            sym = t.get("symbol", "")
            if not sym or len(sym) > 5 or sym in seen:
                continue
            seen.add(sym)
            reddit.append({
                "symbol": sym,
                "description": f"Reddit trending — {t.get('reddit_mentions', 0)} mentions",
                "max_allocation_pct": 3,
                "source": "reddit",
            })

        # Interleaved round-robin, not concatenate-then-truncate. Most-active
        # alone can return up to 20 candidates; appending it first and slicing
        # to max_tickers meant a full most-active batch silently crowded out
        # every gainer and Reddit ticker before either ever got a look. A
        # single wide source is not the same as diverse discovery, and the
        # trades that most need this channel — surprising movers, not the
        # mega-caps that dominate most-active every day — are exactly the ones
        # a naive concatenation starves.
        tickers = []
        for group in zip_longest(most_active, gainers, reddit):
            for t in group:
                if t is not None:
                    tickers.append(t)

        print(f"  [intel loader] {len(tickers)} discovery tickers: {[t['symbol'] for t in tickers]}", file=sys.stderr)
        return tickers[:max_tickers]
    except Exception as e:
        print(f"  [intelligence loader] {e}", file=sys.stderr)
        return []


def _load_dip_tickers():
    """
    Load 25%+ single-day drop candidates from today's screener output.
    These bypass normal signal filtering in the Judge — dip scoring path used instead.
    """
    today = et_today_iso()
    path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-screener.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        tickers = []
        for d in data.get("dip_opportunities", []):
            sym = d.get("symbol", "")
            if not sym or len(sym) > 5:
                continue
            tickers.append({
                "symbol":            sym,
                "description":       f"DIP BUY ALERT: {d.get('pct_change', 0):.1f}% today "
                                     f"(prev close ${d.get('prev_close', 0):.2f} → ${d.get('price', 0):.2f})",
                "max_allocation_pct": 8,   # higher conviction sizing
                "source":            "dip_screener",
                "dip_buy_candidate": True,
                "dip_pct_change":    d.get("pct_change", 0),
                "dip_prev_close":    d.get("prev_close", 0),
                "dip_price":         d.get("price", 0),
            })
        if tickers:
            print(f"  [dip loader] {len(tickers)} dip candidates: {[t['symbol'] for t in tickers]}", file=sys.stderr)
        return tickers
    except Exception as e:
        print(f"  [dip loader] {e}", file=sys.stderr)
        return []


def _load_screener_tickers(max_tickers=15):
    """Load top candidates from today's full market screener output."""
    today = et_today_iso()
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
    dip_tickers      = _load_dip_tickers()

    watchlist_syms = {item["symbol"] for item in watchlist_items}
    extra_items = []
    for t in social_tickers + intel_tickers + screener_tickers + dip_tickers:
        if t["symbol"] not in watchlist_syms:
            extra_items.append(t)
            watchlist_syms.add(t["symbol"])
        elif t.get("dip_buy_candidate"):
            # Dip flag must propagate even if symbol is already on watchlist
            for w in watchlist_items:
                if w["symbol"] == t["symbol"]:
                    w["dip_buy_candidate"]  = True
                    w["dip_pct_change"]     = t.get("dip_pct_change", 0)
                    w["dip_prev_close"]     = t.get("dip_prev_close", 0)
                    w["max_allocation_pct"] = 8  # upgrade sizing

    all_items = watchlist_items + extra_items
    print(f"  [research] Total symbols to research: {len(all_items)} "
          f"(watchlist: {len(watchlist_items)}, discovered: {len(extra_items)})", file=sys.stderr)
    if extra_items:
        print(f"  [research] Discovered: {[t['symbol'] for t in extra_items]}", file=sys.stderr)

    results = {
        "agent_version": get_version(),
        "timestamp": et_now().isoformat(),
        "market_open": clock.get("is_open", False),
        "next_open": clock.get("next_open", ""),
        "account": {
            "cash": account.get("cash"),
            "portfolio_value": account.get("portfolio_value"),
            "buying_power": account.get("buying_power"),
        },
        "positions": get_positions(),
        "symbols": {},
        "intelligence": load_intelligence_context(),
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

            sym_result = {
                "description":      item.get("description", ""),
                "max_allocation_pct": item.get("max_allocation_pct", 5),
                "source":           item.get("source", "watchlist"),
                "technicals":       tech_data,
                "news":             news_sum,
                "fundamentals":     fundamentals,
            }
            # Propagate dip buy flag so Judge routes to dip scoring path
            if item.get("dip_buy_candidate"):
                sym_result["dip_buy_candidate"] = True
                sym_result["dip_pct_change"]    = item.get("dip_pct_change", 0)
                sym_result["dip_prev_close"]    = item.get("dip_prev_close", 0)
            results["symbols"][sym] = sym_result
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

        market_cap_raw = summary.get("marketCap", {}).get("raw") or keystats.get("enterpriseValue", {}).get("raw")
        profit_margin_raw = findata.get("profitMargins", {}).get("raw")

        return {
            "pe_ratio":           _val(summary, "trailingPE"),
            "forward_pe":         _val(summary, "forwardPE"),
            "eps_ttm":            _val(keystats, "trailingEps"),
            "revenue_growth":     _val(findata, "revenueGrowth"),
            "profit_margin":      _val(findata, "profitMargins"),
            "analyst_target":     _val(findata, "targetMeanPrice"),
            "current_price":      _val(findata, "currentPrice"),
            "short_float":        _val(keystats, "shortPercentOfFloat"),
            "analyst_buy_count":  analyst_rec,
            "analyst_sell_count": analyst_sell,
            "market_cap":         market_cap_raw,
            "market_cap_b":       round(market_cap_raw / 1e9, 2) if market_cap_raw else None,
            "is_profitable":      (profit_margin_raw > 0) if profit_margin_raw is not None else None,
            "upside_pct":         round(
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
        "agent_version": get_version(),
        "timestamp": et_now().isoformat(),
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
        _out_path = os.path.join(_log_dir, f"{et_today_iso()}.json")
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
