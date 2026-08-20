#!/usr/bin/env python3
"""
social_screener.py (formerly reddit_screener.py)
Scans StockTwits for trending tickers, momentum signals, and sentiment.
StockTwits is purpose-built for stock mentions — free tier, no API key required.

NOTE ON REDDIT:
Reddit's developer API now requires a formal access application via their
Responsible Builder policy before apps can be created at reddit.com/prefs/apps.
Access is often denied unless built on Devvit (Reddit's internal app platform),
which is designed for apps running inside Reddit — not external data extraction.
StockTwits is a superior data source for our use case: real-time ticker mentions,
native bullish/bearish sentiment tags, and zero auth friction.

Usage:
  python reddit_screener.py scan             # Trending tickers + top mentions
  python reddit_screener.py ticker NVDA      # Deep scan on one symbol
  python reddit_screener.py hot              # Top 20 tickers right now
  python reddit_screener.py watchlist        # Scan your watchlist symbols
"""

import os
import sys
import json
import time
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
# ── Load .env ────────────────────────────────────────────────────────────────
# setup.sh writes a .env file, but nothing used to read it, so filling in keys
# there had no effect. Environment variables already set (GitHub Secrets, MCP
# config, an exporting shell) always win — load_dotenv does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars may be supplied by the shell

STOCKTWITS_BASE  = "https://api.stocktwits.com/api/2"
MAX_AGE_DAYS     = 7
REQUEST_DELAY    = 0.5   # seconds between calls — free tier is 200 req/hour
WATCHLIST_PATH   = os.path.join(os.path.dirname(__file__), "watchlist.json")
JOURNAL_DIR      = os.path.join(os.path.dirname(__file__), "logs", "paper")

# Optional: set STOCKTWITS_TOKEN for higher rate limits (free account)
STOCKTWITS_TOKEN = os.getenv("STOCKTWITS_TOKEN")

# ── Pump & Dump Guardrail Thresholds ─────────────────────────────────────────
MIN_PRICE          = 5.00    # Hard floor — penny stocks are primary pump vehicles
MIN_MARKET_CAP_M   = 500     # $500M minimum market cap (millions)
MAX_INTRADAY_MOVE  = 0.08    # 8% — if already up this much, we're the bag
EXTREME_BULL_PCT   = 85      # >85% bullish with no news = suspicious signal
DIVERGENCE_VETO    = True    # Veto if social bullish but MA says HOLD/SELL

def _headers() -> dict:
    h = {"User-Agent": "trading-screener/1.0"}
    if STOCKTWITS_TOKEN:
        h["Authorization"] = f"OAuth {STOCKTWITS_TOKEN}"
    return h

def _get(path: str, params: dict = None) -> dict:
    r = requests.get(f"{STOCKTWITS_BASE}{path}",
                     headers=_headers(),
                     params=params or {},
                     timeout=10)
    r.raise_for_status()
    return r.json()

# ── StockTwits Data Fetchers ──────────────────────────────────────────────────

def get_trending_symbols() -> list[dict]:
    """
    Fetch currently trending symbols on StockTwits.
    Returns list of {symbol, watchlist_count, title}.
    """
    data = _get("/trending/symbols.json")
    return data.get("symbols", [])

def get_symbol_stream(symbol: str, since_id: int = None) -> dict:
    """
    Fetch recent messages for a symbol.
    Returns messages with native sentiment tags (Bullish/Bearish).
    """
    params = {"limit": 30}
    if since_id:
        params["since"] = since_id
    return _get(f"/streams/symbol/{symbol}.json", params)

def get_suggested_symbols() -> list[dict]:
    """Fetch StockTwits suggested/hot symbols."""
    try:
        data = _get("/trending/symbols/equities.json")
        return data.get("symbols", [])
    except Exception:
        return []

def search_symbol(symbol: str) -> dict:
    """Get symbol info and recent message count."""
    try:
        return _get(f"/search/symbols.json", params={"q": symbol})
    except Exception:
        return {}

# ── Sentiment Analysis ────────────────────────────────────────────────────────

def analyze_stream(messages: list[dict], max_age_days: int = MAX_AGE_DAYS) -> dict:
    """
    Analyze a symbol's message stream.
    StockTwits messages have native sentiment: entities.sentiment.basic = Bullish/Bearish
    Returns: {bullish, bearish, neutral, total, recent_count, sentiment, messages}
    """
    cutoff   = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    bullish  = bearish = neutral = 0
    recent   = []

    for msg in messages:
        created_str = msg.get("created_at", "")
        try:
            created = datetime.strptime(created_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if created < cutoff:
            continue

        sentiment_data = (msg.get("entities") or {}).get("sentiment") or {}
        sentiment      = sentiment_data.get("basic", "Neutral")

        if sentiment == "Bullish":
            bullish += 1
        elif sentiment == "Bearish":
            bearish += 1
        else:
            neutral += 1

        recent.append({
            "id":        msg.get("id"),
            "body":      msg.get("body", "")[:200],
            "sentiment": sentiment,
            "created":   created_str,
            "likes":     (msg.get("likes") or {}).get("total", 0),
            "user":      (msg.get("user") or {}).get("username", ""),
        })

    total = bullish + bearish + neutral
    if total == 0:
        net_sentiment = "NO_DATA"
    elif bullish >= bearish * 1.5:
        net_sentiment = "BULLISH"
    elif bearish >= bullish * 1.5:
        net_sentiment = "BEARISH"
    else:
        net_sentiment = "MIXED"

    return {
        "bullish":      bullish,
        "bearish":      bearish,
        "neutral":      neutral,
        "total":        total,
        "sentiment":    net_sentiment,
        "bull_pct":     round(bullish / total * 100, 1) if total else 0,
        "recent_msgs":  recent[:5],
    }

def momentum_score(symbol_data: dict, stream_analysis: dict) -> float:
    """
    Composite momentum score 0–100:
    - Watchlist count (social proof)
    - Message volume (activity)
    - Bullish sentiment ratio
    """
    watchlist = symbol_data.get("watchlist_count", 0)
    total_msg = stream_analysis.get("total", 0)
    bull_pct  = stream_analysis.get("bull_pct", 50)

    # Normalize each component to 0–33
    watch_score = min(watchlist / 10000 * 33, 33)
    msg_score   = min(total_msg / 30 * 33, 33)
    sent_score  = (bull_pct / 100) * 33

    return round(watch_score + msg_score + sent_score, 1)

# ── Watchlist Scanner ─────────────────────────────────────────────────────────

def load_watchlist() -> list[str]:
    if not os.path.exists(WATCHLIST_PATH):
        return ["NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "META", "GOOGL", "AMD"]
    with open(WATCHLIST_PATH) as f:
        data = json.load(f)
    items = data.get("watchlist", data) if isinstance(data, dict) else data
    return [item["symbol"] if isinstance(item, dict) else item for item in items]

def scan_watchlist(max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    symbols = load_watchlist()
    results = []
    print(f"  Scanning {len(symbols)} watchlist symbols...", file=sys.stderr)
    for sym in symbols:
        try:
            stream = get_symbol_stream(sym)
            msgs   = stream.get("messages", [])
            analysis = analyze_stream(msgs, max_age_days)
            sym_info = stream.get("symbol", {})
            results.append({
                "ticker":         sym,
                "watchlist_count": sym_info.get("watchlist_count", 0),
                "analysis":       analysis,
                "momentum":       momentum_score(sym_info, analysis),
            })
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ⚠️  {sym}: {e}", file=sys.stderr)
    return sorted(results, key=lambda x: x["momentum"], reverse=True)

# ── Pump & Dump Detection ─────────────────────────────────────────────────────

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
DATA_URL      = "https://data.alpaca.markets"

def _alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

def get_price_data(symbol: str) -> dict:
    """
    Fetch current price, today's open, and 20-day avg volume from Alpaca.
    Returns empty dict if Alpaca credentials not set.
    """
    if not ALPACA_KEY or not ALPACA_SECRET:
        return {}
    try:
        # Latest quote
        r = requests.get(
            f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest",
            headers=_alpaca_headers(), timeout=5
        )
        r.raise_for_status()
        quote = r.json().get("quote", {})
        current_price = float(quote.get("ap") or quote.get("bp") or 0)

        # Recent bars for avg volume + today's open
        r2 = requests.get(
            f"{DATA_URL}/v2/stocks/{symbol}/bars",
            headers=_alpaca_headers(),
            params={"timeframe": "1Day", "limit": 21, "sort": "desc"},
            timeout=5
        )
        r2.raise_for_status()
        bars = r2.json().get("bars", [])

        if not bars:
            return {"current_price": current_price}

        today_bar  = bars[0]
        prior_bars = bars[1:21]

        today_open  = float(today_bar.get("o", 0))
        today_close = float(today_bar.get("c", current_price))
        avg_volume  = sum(b.get("v", 0) for b in prior_bars) / len(prior_bars) if prior_bars else 0
        today_vol   = float(today_bar.get("v", 0))
        prev_close  = float(prior_bars[0].get("c", 0)) if prior_bars else 0

        intraday_move = (today_close - prev_close) / prev_close if prev_close else 0
        volume_ratio  = today_vol / avg_volume if avg_volume else 0

        return {
            "current_price":  round(current_price or today_close, 2),
            "today_open":     round(today_open, 2),
            "prev_close":     round(prev_close, 2),
            "intraday_move":  round(intraday_move, 4),
            "intraday_pct":   round(intraday_move * 100, 2),
            "avg_volume_20d": round(avg_volume),
            "today_volume":   round(today_vol),
            "volume_ratio":   round(volume_ratio, 2),
        }
    except Exception:
        return {}


def run_pump_check(symbol: str, analysis: dict, price_data: dict) -> dict:
    """
    Run all 6 pump & dump guardrails.
    Returns: {is_pump: bool, flags: list, verdict: str, safe_to_judge: bool}

    Guardrails:
    1. Price floor < $5 → VETO (penny stock)
    2. Intraday move > 8% → VETO (already pumped, we'd be the bag)
    3. Extreme sentiment (>85% bull) with no news → FLAG (suspicious uniformity)
    4. Volume spike >4x avg with price already up → FLAG (distribution pattern)
    5. Social-only signal (no MA buy signal cross-reference) → FLAG
    6. Market cap < $500M → VETO (thin float = easy to manipulate)
    """
    flags   = []
    vetoes  = []

    bull_pct      = analysis.get("bull_pct", 0)
    total_msgs    = analysis.get("total", 0)
    current_price = price_data.get("current_price", 0)
    intraday_pct  = price_data.get("intraday_pct", 0)
    volume_ratio  = price_data.get("volume_ratio", 0)

    # ── Guardrail 1: Price floor ──────────────────────────────────────────────
    # Fails closed. The previous `current_price > 0 and current_price < MIN_PRICE`
    # form skipped the veto entirely when price data was missing, because
    # price_data.get("current_price", 0) defaults to 0 — so an unpriced penny
    # stock passed the floor it was supposed to fail.
    if current_price <= 0:
        vetoes.append(
            f"PRICE_UNKNOWN: no usable price for this ticker — cannot verify the "
            f"${MIN_PRICE:.2f} floor, so it is rejected rather than assumed safe"
        )
    elif current_price < MIN_PRICE:
        vetoes.append(
            f"PRICE_FLOOR: ${current_price:.2f} is below ${MIN_PRICE:.2f} minimum — "
            f"penny stocks are primary pump vehicles"
        )

    # ── Guardrail 2: Already pumped ───────────────────────────────────────────
    if intraday_pct > MAX_INTRADAY_MOVE * 100:
        vetoes.append(
            f"ALREADY_PUMPED: Stock up {intraday_pct:+.1f}% today — "
            f"buying now means holding the bag after insiders sell"
        )

    # ── Guardrail 3: Extreme sentiment uniformity ─────────────────────────────
    if bull_pct >= EXTREME_BULL_PCT and total_msgs >= 10:
        flags.append(
            f"EXTREME_SENTIMENT: {bull_pct:.0f}% bullish is suspiciously uniform — "
            f"organic sentiment rarely exceeds 85%. Requires verified news catalyst."
        )

    # ── Guardrail 4: Volume spike + price move = distribution ────────────────
    if volume_ratio >= 4.0 and intraday_pct > 3.0:
        flags.append(
            f"VOLUME_SPIKE: {volume_ratio:.1f}x avg volume with {intraday_pct:+.1f}% price move — "
            f"classic distribution pattern (insiders selling into retail buying)"
        )
    elif volume_ratio >= 2.5 and intraday_pct > 5.0:
        flags.append(
            f"VOLUME_SPIKE: {volume_ratio:.1f}x avg volume on {intraday_pct:+.1f}% move — monitor closely"
        )

    # ── Guardrail 5: Social-only signal ──────────────────────────────────────
    # This flag is set here; the Judge checks MA alignment before approving
    if bull_pct >= 60 and total_msgs > 0:
        flags.append(
            "SOCIAL_ONLY: High social sentiment noted — Judge must confirm MA buy signal "
            "before approving. Social buzz without technical confirmation = pump risk."
        )

    # ── Guardrail 6: Implied thin float (low price + high volume ratio) ───────
    if current_price > 0 and current_price < 10 and volume_ratio >= 3.0:
        vetoes.append(
            f"THIN_FLOAT_RISK: Low-price stock (${current_price:.2f}) with {volume_ratio:.1f}x "
            f"volume surge — high manipulation risk on thin float names"
        )

    is_pump     = len(vetoes) > 0
    has_flags   = len(flags) > 0
    safe        = not is_pump

    if is_pump:
        verdict = "🚫 PUMP_RISK — DO NOT TRADE"
    elif has_flags:
        verdict = "⚠️  FLAGGED — Judge must verify MA + news before approving"
    else:
        verdict = "✅ CLEAN — No pump indicators detected"

    return {
        "symbol":        symbol,
        "is_pump":       is_pump,
        "has_flags":     has_flags,
        "safe_to_judge": safe,
        "verdict":       verdict,
        "vetoes":        vetoes,
        "flags":         flags,
        "price_data":    price_data,
    }


# ── Full Scan ─────────────────────────────────────────────────────────────────

def run_scan(max_age_days: int = MAX_AGE_DAYS) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    print("🔍 Fetching trending symbols from StockTwits...", file=sys.stderr)

    # 1. Get trending symbols
    trending = get_trending_symbols()
    suggested = get_suggested_symbols()

    # Combine and dedupe
    all_symbols = {s["symbol"]: s for s in trending}
    for s in suggested:
        all_symbols.setdefault(s["symbol"], s)

    print(f"  {len(all_symbols)} symbols to scan", file=sys.stderr)

    # 2. Pull stream + sentiment for each
    results = []
    for sym, sym_data in list(all_symbols.items())[:40]:  # Cap at 40 to stay in rate limits
        try:
            stream   = get_symbol_stream(sym)
            msgs     = stream.get("messages", [])
            analysis = analyze_stream(msgs, max_age_days)
            score    = momentum_score(sym_data, analysis)
            results.append({
                "ticker":          sym,
                "title":           sym_data.get("title", ""),
                "watchlist_count": sym_data.get("watchlist_count", 0),
                "sentiment":       analysis["sentiment"],
                "bull_pct":        analysis["bull_pct"],
                "msg_count":       analysis["total"],
                "bullish":         analysis["bullish"],
                "bearish":         analysis["bearish"],
                "momentum":        score,
                "top_messages":    analysis["recent_msgs"][:3],
            })
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ⚠️  {sym}: {e}", file=sys.stderr)

    # 3. Add watchlist symbols not already in trending
    watchlist_syms  = load_watchlist()
    trending_syms   = {r["ticker"] for r in results}
    missing_syms    = [s for s in watchlist_syms if s not in trending_syms]

    for sym in missing_syms[:10]:
        try:
            stream   = get_symbol_stream(sym)
            msgs     = stream.get("messages", [])
            analysis = analyze_stream(msgs, max_age_days)
            sym_info = stream.get("symbol", {})
            results.append({
                "ticker":          sym,
                "title":           sym_info.get("title", sym),
                "watchlist_count": sym_info.get("watchlist_count", 0),
                "sentiment":       analysis["sentiment"],
                "bull_pct":        analysis["bull_pct"],
                "msg_count":       analysis["total"],
                "bullish":         analysis["bullish"],
                "bearish":         analysis["bearish"],
                "momentum":        momentum_score(sym_info, analysis),
                "top_messages":    analysis["recent_msgs"][:3],
                "source":          "watchlist",
            })
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"    ⚠️  {sym}: {e}", file=sys.stderr)

    results.sort(key=lambda x: x["momentum"], reverse=True)

    # 3b. Run pump & dump check on every result
    print("🛡️  Running pump & dump guardrails...", file=sys.stderr)
    clean = vetoed = flagged = 0
    for r in results:
        sym = r["ticker"]
        # Build a minimal analysis dict from what we already have
        analysis_mini = {
            "bull_pct": r.get("bull_pct", 0),
            "total":    r.get("msg_count", 0),
        }
        price_data = get_price_data(sym)
        pump = run_pump_check(sym, analysis_mini, price_data)
        r["pump_risk"]       = pump
        r["safe_to_judge"]   = pump["safe_to_judge"]
        r["pump_verdict"]    = pump["verdict"]
        if pump["is_pump"]:
            vetoed += 1
            print(f"    🚫 PUMP VETO  {sym}: {'; '.join(pump['vetoes'])}", file=sys.stderr)
        elif pump["has_flags"]:
            flagged += 1
            print(f"    ⚠️  FLAGGED   {sym}: {len(pump['flags'])} flag(s)", file=sys.stderr)
        else:
            clean += 1
    print(f"  Pump scan: {clean} clean, {flagged} flagged, {vetoed} vetoed", file=sys.stderr)

    # 4. Save outputs
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    out = {
        "date":        today,
        "timestamp":   datetime.now().isoformat(),
        "source":      "StockTwits",
        "max_age_days": max_age_days,
        "top_tickers": results[:50],
    }
    with open(os.path.join(JOURNAL_DIR, f"{today}-social.json"), "w") as f:
        json.dump(out, f, indent=2)

    # Markdown report
    pump_vetoed = [r for r in results if r.get("pump_risk", {}).get("is_pump")]
    pump_flagged = [r for r in results if r.get("pump_risk", {}).get("has_flags") and not r.get("pump_risk", {}).get("is_pump")]
    judge_safe   = [r for r in results if r.get("safe_to_judge", True)]

    md = [
        f"# Social Screener — {today}",
        f"*Source: StockTwits | {len(results)} symbols scanned | Last {max_age_days} days*\n",
        f"## Pump & Dump Summary",
        f"- ✅ **Clean (safe to Judge)**: {len(judge_safe)}",
        f"- ⚠️  **Flagged (review required)**: {len(pump_flagged)}",
        f"- 🚫 **Pump vetoed (do not trade)**: {len(pump_vetoed)}\n",
    ]

    if pump_vetoed:
        md.append("### 🚫 Vetoed Symbols")
        for r in pump_vetoed:
            reasons = "; ".join(r.get("pump_risk", {}).get("vetoes", []))
            md.append(f"- **{r['ticker']}**: {reasons}")
        md.append("")

    if pump_flagged:
        md.append("### ⚠️ Flagged Symbols (Judge must verify MA + news)")
        for r in pump_flagged:
            flags = "; ".join(r.get("pump_risk", {}).get("flags", []))
            md.append(f"- **{r['ticker']}**: {flags}")
        md.append("")

    md += [
        "## Rankings",
        "| Rank | Ticker | Pump Risk | Sentiment | Bull% | Messages | Watchlists | Momentum |",
        "|------|--------|-----------|-----------|-------|----------|------------|----------|",
    ]
    for i, t in enumerate(results[:25], 1):
        icon = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡", "NO_DATA": "⬜"}.get(t["sentiment"], "⬜")
        pump = t.get("pump_risk", {})
        if pump.get("is_pump"):
            risk_icon = "🚫 VETO"
        elif pump.get("has_flags"):
            risk_icon = "⚠️ FLAG"
        else:
            risk_icon = "✅ CLEAN"
        md.append(
            f"| {i} | **{t['ticker']}** | {risk_icon} | {icon} {t['sentiment']} | {t['bull_pct']}% "
            f"| {t['msg_count']} | {t['watchlist_count']:,} | {t['momentum']} |"
        )

    md_path = os.path.join(JOURNAL_DIR, f"{today}-social.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md))

    out["report_path"] = md_path
    return out

def scan_ticker(symbol: str, max_age_days: int = MAX_AGE_DAYS) -> dict:
    """Deep scan on a single ticker."""
    symbol = symbol.upper()
    stream   = get_symbol_stream(symbol)
    msgs     = stream.get("messages", [])
    analysis = analyze_stream(msgs, max_age_days)
    sym_info = stream.get("symbol", {})
    return {
        "ticker":          symbol,
        "title":           sym_info.get("title", symbol),
        "watchlist_count": sym_info.get("watchlist_count", 0),
        "sentiment":       analysis["sentiment"],
        "bull_pct":        analysis["bull_pct"],
        "bullish":         analysis["bullish"],
        "bearish":         analysis["bearish"],
        "total_messages":  analysis["total"],
        "momentum":        momentum_score(sym_info, analysis),
        "recent_messages": analysis["recent_msgs"],
    }

# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "scan"
    arg2   = sys.argv[2] if len(sys.argv) > 2 else None
    days   = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else MAX_AGE_DAYS

    if action == "scan":
        result = run_scan(max_age_days=days)
        print(f"\n{'='*65}")
        print(f"TOP STOCKTWITS TICKERS — {result['date']}")
        print(f"{'='*65}")
        for i, t in enumerate(result["top_tickers"][:20], 1):
            icon = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(t["sentiment"], "⬜")
            print(f"{i:>2}. {t['ticker']:<6} {icon} {t['sentiment']:<8} "
                  f"Bull: {t['bull_pct']:>5.1f}% | {t['msg_count']:>3} msgs | "
                  f"Score: {t['momentum']}")
        print(f"\nReport: {result.get('report_path')}")

    elif action == "ticker" and arg2:
        result = scan_ticker(arg2, max_age_days=days)
        print(json.dumps(result, indent=2))

    elif action == "watchlist":
        results = scan_watchlist(max_age_days=days)
        print(f"\n{'='*55}")
        print("WATCHLIST — StockTwits Sentiment")
        print(f"{'='*55}")
        for t in results:
            icon = {"BULLISH": "🟢", "BEARISH": "🔴", "MIXED": "🟡"}.get(t["analysis"]["sentiment"], "⬜")
            print(f"{t['ticker']:<6} {icon} {t['analysis']['sentiment']:<8} "
                  f"Bull: {t['analysis']['bull_pct']:>5.1f}% | Score: {t['momentum']}")

    elif action == "hot":
        result = run_scan(max_age_days=1)
        print(json.dumps(result["top_tickers"][:20], indent=2))

    else:
        print("Usage: python reddit_screener.py scan | ticker SYMBOL | watchlist | hot")
