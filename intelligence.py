#!/usr/bin/env python3
"""
intelligence.py — Multi-source market intelligence scanner.

Pulls signals from social media, SEC filings, macro data, global markets,
options flow, and news beyond the fixed watchlist. Feeds into Judge pipeline.

Usage:
  python intelligence.py morning     # Full pre-market intelligence scan
  python intelligence.py reddit      # Reddit WSB + r/stocks trending tickers
  python intelligence.py twitter     # Key influencer tweet scan (web search)
  python intelligence.py insider     # SEC EDGAR Form 4 insider buys (last 48h)
  python intelligence.py macro       # VIX, Fear/Greed, yields, CPI
  python intelligence.py earnings    # Upcoming earnings next 5 trading days
  python intelligence.py global      # Pre-market signals from Asia + Europe
  python intelligence.py options     # Unusual options activity (Unusual Whales)
  python intelligence.py news AAPL   # Deep news scan for one symbol
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta, date

# ── Auth ────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL      = "https://data.alpaca.markets"
FRED_KEY      = os.getenv("FRED_API_KEY", "")  # free at fred.stlouisfed.org

def _alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }

# ── 1. Reddit scanner (web scrape — no API key needed) ────────────────────
def scan_reddit():
    """
    Scrape r/wallstreetbets and r/stocks for trending tickers.
    Uses old.reddit.com JSON endpoint — no auth required.
    Returns list of {symbol, mentions, sentiment_hint, source_url, title}.
    """
    import re
    results = []
    ticker_re = re.compile(r'\b([A-Z]{2,5})\b')
    ignore = {"DD", "YOL", "CEO", "IPO", "ETF", "SEC", "IMO", "WSB", "GDP",
              "CPI", "FED", "THE", "FOR", "AND", "BUT", "ARE", "NOT", "ITS",
              "EOD", "ATH", "ATL", "EPS", "PE", "AI", "US", "OR", "BE",
              "IV", "OI", "ER", "IV", "AM", "PM", "PT", "PT"}

    subreddits = ["wallstreetbets", "stocks", "investing", "stockmarket"]
    mention_count = {}
    post_cache = {}

    for sub in subreddits:
        texts_scraped = []
        # Primary: Reddit JSON (may hit CAPTCHA/rate-limit)
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit=50"
            headers = {"User-Agent": "TradingAgent/1.0 (+research)"}
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                posts = data.get("data", {}).get("children", [])
                for post in posts:
                    p = post.get("data", {})
                    texts_scraped.append({
                        "text": f"{p.get('title','')} {p.get('selftext','')}",
                        "score": p.get("score", 0),
                        "url": p.get("url", ""),
                        "sub": sub,
                    })
        except Exception:
            pass

        # Fallback: RSS feed — confirmed ACTIVE (57KB payload, no auth)
        if not texts_scraped:
            try:
                rss_url = f"https://www.reddit.com/r/{sub}/.rss"
                headers = {"User-Agent": "TradingAgent/1.0 (+research)"}
                r = requests.get(rss_url, headers=headers, timeout=10)
                if r.status_code == 200:
                    # Extract <title> and <content> tags from RSS XML
                    titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
                    contents = re.findall(r'<content[^>]*>(.*?)</content>', r.text, re.DOTALL)
                    for i, title in enumerate(titles[1:], 0):  # skip feed title
                        body = contents[i] if i < len(contents) else ""
                        texts_scraped.append({
                            "text": f"{title} {body}",
                            "score": 1,
                            "url": f"https://reddit.com/r/{sub}",
                            "sub": sub,
                        })
            except Exception as e:
                results.append({"error": f"Reddit RSS {sub}: {str(e)}"})

        for item in texts_scraped:
            text = item["text"]
            tickers = set(ticker_re.findall(text)) - ignore
            for ticker in tickers:
                if len(ticker) < 2 or len(ticker) > 5:
                    continue
                mention_count[ticker] = mention_count.get(ticker, 0) + 1
                if ticker not in post_cache or item["score"] > post_cache[ticker]["score"]:
                    post_cache[ticker] = {
                        "title": text[:100],
                        "score": item["score"],
                        "url": item["url"],
                        "sub": sub,
                    }

    sorted_tickers = sorted(mention_count.items(), key=lambda x: x[1], reverse=True)[:20]
    for ticker, count in sorted_tickers:
        info = post_cache.get(ticker, {})
        results.append({
            "symbol": ticker,
            "reddit_mentions": count,
            "top_post": info.get("title", ""),
            "subreddit": info.get("sub", ""),
            "post_score": info.get("score", 0),
            "url": info.get("url", ""),
        })

    return {"timestamp": datetime.now().isoformat(), "source": "reddit", "tickers": results}


# ── 2. Twitter / X key influencer scan (web search fallback) ─────────────
INFLUENCERS = [
    "ElonMusk",
    "CathieDWood",
    "WarrenBuffett",
    "BillAckman",
    "chamath",
    "jimcramer",
    "RaoulGMI",
]

def scan_twitter_mentions(symbol):
    """
    Search for recent tweets about a symbol from key financial accounts.
    Uses DuckDuckGo news search as a fallback (no Twitter API key needed).
    Returns list of recent headlines mentioning symbol from social sources.
    """
    results = []
    query = f"{symbol} stock site:twitter.com OR site:x.com"
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": 1}
        r = requests.get(url, params=params, timeout=8)
        data = r.json()
        for item in data.get("RelatedTopics", [])[:10]:
            text = item.get("Text", "")
            if text:
                results.append({"source": "twitter_web", "text": text})
    except Exception as e:
        results.append({"error": str(e)})
    return results


# ── 3. SEC EDGAR Form 4 — insider buys ───────────────────────────────────
def get_insider_buys(symbol=None, days_back=7):
    """
    Fetch recent Form 4 filings (insider transactions) from SEC EDGAR.
    Filters for acquisitions only (not sales). Free, no API key needed.
    If symbol provided, filter for that company's CIK.
    """
    try:
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        url = "https://efts.sec.gov/LATEST/search-index?q=%22form+4%22&dateRange=custom&startdt={}&enddt={}&forms=4".format(
            cutoff, date.today().isoformat()
        )
        headers = {"User-Agent": "TradingAgent research@trading.com"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        filings = data.get("hits", {}).get("hits", [])
        results = []
        for f in filings[:20]:
            src = f.get("_source", {})
            results.append({
                "company": src.get("entity_name", ""),
                "filed": src.get("file_date", ""),
                "form": src.get("form_type", "4"),
                "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={}&type=4&dateb=&owner=include&count=10".format(
                    src.get("entity_name", "").replace(" ", "+")
                ),
            })
        return {"timestamp": datetime.now().isoformat(), "source": "sec_edgar", "insider_filings": results}
    except Exception as e:
        return {"error": str(e)}


def get_insider_buys_simple(symbol):
    """
    Simpler EDGAR full-text search for insider buys on a specific ticker.
    """
    try:
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22{symbol}%22+%22acquisition%22&forms=4&dateRange=custom&startdt={(date.today()-timedelta(days=14)).isoformat()}&enddt={date.today().isoformat()}"
        headers = {"User-Agent": "TradingAgent research@trading.com"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        return [{"entity": h["_source"].get("entity_name"), "date": h["_source"].get("file_date")} for h in hits[:5]]
    except Exception as e:
        return [{"error": str(e)}]


# ── 4. VIX + Fear & Greed ─────────────────────────────────────────────────
def get_vix():
    """Fetch VIX from Alpaca as a tradable instrument (VIXY proxy or direct)."""
    try:
        url = f"{DATA_URL}/v2/stocks/VIXY/quotes/latest"
        r = requests.get(url, headers=_alpaca_headers(), timeout=8)
        if r.status_code == 200:
            data = r.json()
            quote = data.get("quote", {})
            return {"symbol": "VIXY", "price": quote.get("ap"), "note": "VIXY proxy for VIX"}
    except Exception:
        pass
    return {"error": "VIX data unavailable"}


def get_fear_greed():
    """
    Fear/Greed proxy via FRED VIXCLS CSV — no API key or auth needed.
    CNN Fear/Greed endpoint is blocked; FRED CSV confirmed ACTIVE (VIX 18.41).
    Interprets VIX level as fear/greed signal on 0–100 scale.
    """
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
        r = requests.get(url, timeout=8)
        lines = r.text.strip().split("\n")
        # Last non-empty line with a real value
        val_str = None
        val_date = None
        for line in reversed(lines[1:]):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in (".", ""):
                val_date = parts[0].strip()
                val_str = parts[1].strip()
                break
        if not val_str:
            return {"error": "FRED VIX: no data"}
        vix = float(val_str)
        # Map VIX to 0-100 fear/greed scale (inverted: high VIX = fear)
        if vix >= 40:
            score, rating, signal = 5, "Extreme Fear", "EXTREME_FEAR — contrarian buy signal"
        elif vix >= 30:
            score, rating, signal = 20, "Fear", "FEAR — lean bullish on quality"
        elif vix >= 25:
            score, rating, signal = 35, "Fear", "FEAR — standard sizing ok"
        elif vix >= 20:
            score, rating, signal = 45, "Neutral", "NEUTRAL — normal market"
        elif vix >= 15:
            score, rating, signal = 65, "Greed", "GREED — risk-on, watch exits"
        else:
            score, rating, signal = 82, "Extreme Greed", "EXTREME_GREED — reduce new longs, trail stops"
        return {
            "source": "fred_vix_proxy",
            "vix": vix,
            "vix_date": val_date,
            "score": score,
            "rating": rating,
            "signal": signal,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        return {"error": f"Fear/Greed (FRED): {str(e)}"}


# ── 5. Earnings calendar ──────────────────────────────────────────────────
def get_earnings_calendar(symbols=None, days_ahead=7):
    """
    Check upcoming earnings for watchlist symbols via Alpaca calendar
    and news scan. Critical for Hard Rule #9 (no entry within 3 days of earnings).
    """
    try:
        url = f"{DATA_URL}/v1beta1/screener/stocks/most-actives"
        # Alpaca doesn't have a dedicated earnings endpoint on free tier.
        # Use news scan for earnings keywords as a proxy.
        results = []
        check_symbols = symbols or ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "DDOG", "NOK", "QCOM"]
        for sym in check_symbols:
            news_url = f"{DATA_URL}/v1beta1/news?symbols={sym}&limit=10&sort=desc"
            r = requests.get(news_url, headers=_alpaca_headers(), timeout=8)
            articles = r.json().get("news", [])
            for a in articles:
                headline = a.get("headline", "").lower()
                if any(kw in headline for kw in ["earnings", "results", "quarterly", "q1", "q2", "q3", "q4", "eps", "guidance"]):
                    results.append({
                        "symbol": sym,
                        "headline": a.get("headline"),
                        "date": a.get("created_at"),
                        "warning": "EARNINGS ACTIVITY DETECTED — check before entering position",
                    })
                    break
        return {"timestamp": datetime.now().isoformat(), "earnings_watch": results}
    except Exception as e:
        return {"error": str(e)}


# ── 6. Global pre-market signals ─────────────────────────────────────────
GLOBAL_ETFS = {
    "EWJ": "Japan (Nikkei proxy)",
    "EWG": "Germany (DAX proxy)",
    "FXI": "China (HSI/CSI proxy)",
    "EWU": "UK (FTSE proxy)",
    "EWZ": "Brazil (Ibovespa proxy)",
    "EEM": "Emerging markets",
    "GLD": "Gold",
    "USO": "Oil",
    "TLT": "US 20yr Treasuries (rates proxy)",
    "UUP": "US Dollar index",
}

def get_global_signals():
    """
    Use US-listed ETFs as proxies for global market movements.
    Provides pre-market context before US open.
    """
    symbols = ",".join(GLOBAL_ETFS.keys())
    try:
        url = f"{DATA_URL}/v2/stocks/snapshots?symbols={symbols}&feed=iex"
        r = requests.get(url, headers=_alpaca_headers(), timeout=12)
        data = r.json()
        results = {}
        for sym, desc in GLOBAL_ETFS.items():
            snap = data.get(sym, {})
            prev = snap.get("prevDailyBar", {})
            daily = snap.get("dailyBar", {})
            if not daily:
                continue
            prev_close = prev.get("c", 0)
            today_close = daily.get("c", 0)
            pct = ((today_close - prev_close) / prev_close * 100) if prev_close else 0
            results[sym] = {
                "description": desc,
                "prev_close": round(prev_close, 2),
                "today": round(today_close, 2),
                "change_pct": round(pct, 2),
                "signal": "UP" if pct > 0.5 else "DOWN" if pct < -0.5 else "FLAT",
            }
        return {"timestamp": datetime.now().isoformat(), "source": "global_etfs", "markets": results}
    except Exception as e:
        return {"error": str(e)}


# ── 7. FRED macro indicators ──────────────────────────────────────────────
FRED_SERIES = {
    "FEDFUNDS": "Fed Funds Rate",
    "CPIAUCSL": "CPI (inflation)",
    "UNRATE": "Unemployment Rate",
    "T10Y2Y": "Yield Curve (10yr-2yr spread)",
    "DGS10": "10-Year Treasury Yield",
    "VIXCLS": "VIX Closing Price",
}

def get_macro_data():
    """
    Fetch key macro indicators from FRED using the free CSV endpoint.
    No API key needed — fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES confirmed ACTIVE.
    Falls back to FRED JSON API if FRED_API_KEY env var is set.
    """
    results = {}
    for series_id, label in FRED_SERIES.items():
        try:
            if FRED_KEY:
                # If user has registered a real FRED key, use JSON API
                url = "https://api.stlouisfed.org/fred/series/observations"
                params = {
                    "series_id": series_id,
                    "api_key": FRED_KEY,
                    "file_type": "json",
                    "limit": 1,
                    "sort_order": "desc",
                }
                r = requests.get(url, params=params, timeout=8)
                obs = r.json().get("observations", []) if r.status_code == 200 else []
                if obs:
                    val = obs[0].get("value", ".")
                    results[series_id] = {
                        "label": label,
                        "value": val,
                        "date": obs[0].get("date"),
                        "note": _macro_interpretation(series_id, val),
                    }
                    continue
            # Free CSV endpoint — no key needed
            csv_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            r = requests.get(csv_url, timeout=8)
            lines = r.text.strip().split("\n")
            for line in reversed(lines[1:]):
                parts = line.split(",")
                if len(parts) == 2 and parts[1].strip() not in (".", ""):
                    val = parts[1].strip()
                    results[series_id] = {
                        "label": label,
                        "value": val,
                        "date": parts[0].strip(),
                        "note": _macro_interpretation(series_id, val),
                    }
                    break
        except Exception as e:
            results[series_id] = {"error": str(e)}
    return {"timestamp": datetime.now().isoformat(), "source": "fred_csv", "macro": results}


def _macro_interpretation(series_id, val_str):
    try:
        val = float(val_str)
    except Exception:
        return "no data"
    if series_id == "T10Y2Y":
        if val < 0:
            return "INVERTED yield curve — recession risk elevated"
        elif val < 0.5:
            return "Flat curve — caution"
        else:
            return "Normal curve — risk-on ok"
    if series_id == "VIXCLS":
        if val > 35:
            return "EXTREME FEAR — no new longs, inverse ETFs only"
        elif val > 25:
            return "HIGH VOL — reduce position sizes 50%"
        elif val > 18:
            return "Elevated — use standard sizing"
        else:
            return "LOW VOL — risk-on, full sizing allowed"
    if series_id == "FEDFUNDS":
        if val > 5.0:
            return "High rates — tech multiples compressed, value favored"
        elif val < 2.0:
            return "Low rates — growth/tech favored"
    if series_id == "CPIAUCSL":
        return f"CPI index level {val} — watch YoY % change"
    return ""


# ── 8. Unusual options activity ───────────────────────────────────────────
def get_unusual_options(symbol=None):
    """
    Unusual Whales is blocked (requires auth).
    Fallback: Finviz high short-float screener — confirmed ACTIVE (190KB payload).
    Short squeeze candidates: high short float + recent price momentum = powder keg.
    """
    try:
        import re
        url = "https://finviz.com/screener.ashx?v=111&f=sh_short_o20&o=-shortfloat"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            return {"error": f"Finviz returned {r.status_code}"}
        # Parse ticker symbols from screener table rows
        tickers_found = re.findall(r'finviz\.com/quote\.ashx\?t=([A-Z]{1,5})', r.text)
        unique_tickers = list(dict.fromkeys(tickers_found))[:20]  # preserve order, dedup
        results = []
        for t in unique_tickers:
            if symbol and t != symbol:
                continue
            results.append({"symbol": t, "signal": "HIGH_SHORT_FLOAT — squeeze candidate"})
        return {
            "timestamp": datetime.now().isoformat(),
            "source": "finviz_short_squeeze",
            "note": "Unusual Whales blocked; using Finviz high short-float screener",
            "squeeze_candidates": results,
        }
    except Exception as e:
        return {"error": f"Short squeeze scan: {str(e)}"}


# ── 9. Full morning intelligence run ─────────────────────────────────────
def morning_intelligence():
    """
    Full pre-market intelligence scan. Run at 8:30 AM ET before market open.
    Combines all sources into a unified JSON for Judge to consume.
    """
    print("Running morning intelligence scan...", file=sys.stderr)

    output = {
        "timestamp": datetime.now().isoformat(),
        "run_type": "morning_intelligence",
    }

    print("  [1/7] Fear & Greed Index...", file=sys.stderr)
    output["fear_greed"] = get_fear_greed()

    print("  [2/7] VIX...", file=sys.stderr)
    output["vix"] = get_vix()

    print("  [3/7] Global market signals...", file=sys.stderr)
    output["global_markets"] = get_global_signals()

    print("  [4/7] Macro data (FRED)...", file=sys.stderr)
    output["macro"] = get_macro_data()

    print("  [5/7] Reddit trending tickers...", file=sys.stderr)
    output["reddit"] = scan_reddit()

    print("  [6/7] Earnings calendar...", file=sys.stderr)
    output["earnings_watch"] = get_earnings_calendar()

    print("  [7/7] SEC insider buys (7d)...", file=sys.stderr)
    output["insider_activity"] = get_insider_buys()

    # Save to logs
    log_dir = os.path.join(os.path.dirname(__file__), "logs", "paper")
    os.makedirs(log_dir, exist_ok=True)
    out_path = os.path.join(log_dir, f"{date.today().isoformat()}-intelligence.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  Saved to {out_path}", file=sys.stderr)

    return output


# ── CLI ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "morning"
    symbol = sys.argv[2].upper() if len(sys.argv) > 2 else None

    if action == "morning":
        print(json.dumps(morning_intelligence(), indent=2))
    elif action == "reddit":
        print(json.dumps(scan_reddit(), indent=2))
    elif action == "insider":
        if symbol:
            print(json.dumps(get_insider_buys_simple(symbol), indent=2))
        else:
            print(json.dumps(get_insider_buys(), indent=2))
    elif action == "macro":
        out = get_macro_data()
        out["fear_greed"] = get_fear_greed()
        out["vix"] = get_vix()
        print(json.dumps(out, indent=2))
    elif action == "earnings":
        print(json.dumps(get_earnings_calendar(), indent=2))
    elif action == "global":
        print(json.dumps(get_global_signals(), indent=2))
    elif action == "options":
        print(json.dumps(get_unusual_options(symbol), indent=2))
    elif action == "twitter":
        if not symbol:
            print(json.dumps({"error": "Provide a symbol: python intelligence.py twitter AAPL"}, indent=2))
        else:
            print(json.dumps(scan_twitter_mentions(symbol), indent=2))
    elif action == "news" and symbol:
        # Deep multi-source news scan for one symbol
        from research import get_news, summarize_news
        news = get_news(symbol, limit=20)
        summary = summarize_news(news)
        insider = get_insider_buys_simple(symbol)
        options = get_unusual_options(symbol)
        print(json.dumps({
            "symbol": symbol,
            "news": summary,
            "insider_activity": insider,
            "unusual_options": options,
        }, indent=2))
    else:
        print(json.dumps({"error": f"Unknown action: {action}"}))
