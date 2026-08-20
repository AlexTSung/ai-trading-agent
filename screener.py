#!/usr/bin/env python3
"""
screener.py — Full market screener. Scans S&P 500 + Nasdaq 100 + Russell 1000
every morning and surfaces the top candidates for the Judge pipeline.

Usage:
  python3 screener.py scan          # Full scan, outputs top candidates
  python3 screener.py sp500         # Download fresh S&P 500 universe
  python3 screener.py sector        # Sector rotation report
  python3 screener.py congressional # Congressional trading activity
  python3 screener.py news          # Multi-source news firehose
"""

import os
import sys
import json
import requests
from datetime import datetime, date, timedelta
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

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
DATA_URL      = "https://data.alpaca.markets"
JOURNAL_DIR   = os.path.join(os.path.dirname(__file__), "logs", "paper")

def _headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


# ── Universe: S&P 500 + Nasdaq 100 via Wikipedia ─────────────────────────────
SP500_URL  = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NDX100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

def get_sp500_symbols():
    """Scrape S&P 500 tickers from Wikipedia. Returns list of symbols."""
    try:
        import re
        r = requests.get(SP500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        tickers = re.findall(r'<td><a[^>]*>([A-Z]{1,5})</a></td>', r.text)
        # Wikipedia table has symbol in first column of each row
        symbols = list(dict.fromkeys(tickers))[:505]
        return [s.replace(".", "-") for s in symbols if len(s) <= 5]
    except Exception as e:
        print(f"  [screener] S&P 500 fetch failed: {e}", file=sys.stderr)
        return []

def get_cached_universe():
    """
    Load universe from cache file (refreshed weekly) or fetch fresh.
    Returns list of symbols.
    """
    cache_path = os.path.join(JOURNAL_DIR, "universe-cache.json")
    os.makedirs(JOURNAL_DIR, exist_ok=True)

    # Use cache if less than 7 days old
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            cache = json.load(f)
        age_days = (date.today() - date.fromisoformat(cache.get("date", "2000-01-01"))).days
        if age_days < 7:
            print(f"  [screener] Using cached universe: {len(cache['symbols'])} symbols", file=sys.stderr)
            return cache["symbols"]

    print("  [screener] Fetching fresh S&P 500 universe...", file=sys.stderr)
    sp500 = get_sp500_symbols()

    # Supplement with Nasdaq 100 stalwarts
    ndx_extras = ["AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","COST",
                  "NFLX","AMD","QCOM","INTC","CSCO","TXN","AMAT","LRCX","MU","ADI",
                  "KLAC","SNPS","CDNS","MRVL","ON","MPWR","FTNT","PANW","CRWD",
                  "DDOG","ZS","SNOW","TEAM","WDAY","VEEV","HUBS","MDB","ESTC"]
    universe = list(dict.fromkeys(sp500 + ndx_extras))

    with open(cache_path, "w") as f:
        json.dump({"date": date.today().isoformat(), "symbols": universe}, f)

    print(f"  [screener] Universe: {len(universe)} symbols cached", file=sys.stderr)
    return universe


# ── Pre-filter: fast snapshot scan ───────────────────────────────────────────
def batch_snapshots(symbols, batch_size=100):
    """
    Fetch Alpaca snapshots in batches of 100.
    Returns dict of {symbol: snapshot_data}.
    """
    results = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i+batch_size]
        sym_str = ",".join(batch)
        try:
            url = f"{DATA_URL}/v2/stocks/snapshots?symbols={sym_str}&feed=iex"
            r = requests.get(url, headers=_headers(), timeout=15)
            if r.status_code == 200:
                results.update(r.json())
        except Exception as e:
            print(f"  [screener] Snapshot batch error: {e}", file=sys.stderr)
    return results


def pre_filter(snapshots, min_price=5.0, min_volume=500_000):
    """
    Fast pre-filter on snapshot data before pulling full bars.
    Keeps stocks above price floor and volume threshold.
    Returns filtered list of symbols worth deeper research.
    """
    candidates = []
    for sym, snap in snapshots.items():
        try:
            daily = snap.get("dailyBar", {})
            prev  = snap.get("prevDailyBar", {})
            price = daily.get("c", 0)
            vol   = daily.get("v", 0)
            prev_close = prev.get("c", 0)

            if price < min_price or vol < min_volume:
                continue

            pct_change = ((price - prev_close) / prev_close * 100) if prev_close else 0

            # Skip extreme movers (>15% = likely news catalyst or pump)
            if abs(pct_change) > 15:
                continue

            candidates.append({
                "symbol":     sym,
                "price":      round(price, 2),
                "volume":     int(vol),
                "pct_change": round(pct_change, 2),
            })
        except Exception:
            continue

    # Sort by volume (institutional interest proxy)
    candidates.sort(key=lambda x: x["volume"], reverse=True)
    return candidates


# ── Sector rotation tracker (all 11 GICS sectors) ────────────────────────────
SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Healthcare",
    "XLE":  "Energy",
    "XLI":  "Industrials",
    "XLC":  "Communication Services",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLRE": "Real Estate",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "SPY":  "S&P 500 (benchmark)",
    "QQQ":  "Nasdaq 100",
    "IWM":  "Russell 2000 (small cap)",
    "DIA":  "Dow Jones",
}

def get_sector_rotation():
    """
    Snapshot all 11 sector ETFs + benchmarks.
    Returns ranked sectors by momentum with buy/avoid signals.
    """
    syms = ",".join(SECTOR_ETFS.keys())
    try:
        url = f"{DATA_URL}/v2/stocks/snapshots?symbols={syms}&feed=iex"
        r = requests.get(url, headers=_headers(), timeout=12)
        if r.status_code != 200:
            return {"error": f"Sector ETF fetch {r.status_code}"}
        data = r.json()
        sectors = []
        for sym, label in SECTOR_ETFS.items():
            snap = data.get(sym, {})
            daily = snap.get("dailyBar", {})
            prev  = snap.get("prevDailyBar", {})
            price = daily.get("c", 0)
            prev_close = prev.get("c", 0)
            pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
            sectors.append({
                "symbol":  sym,
                "sector":  label,
                "price":   round(price, 2),
                "change_pct": pct,
                "signal":  "STRONG" if pct > 1.0 else "WEAK" if pct < -1.0 else "NEUTRAL",
            })
        sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        top_sectors    = [s["sector"] for s in sectors[:3] if s["change_pct"] > 0]
        avoid_sectors  = [s["sector"] for s in sectors[-3:] if s["change_pct"] < 0]
        return {
            "timestamp":    datetime.now().isoformat(),
            "sectors":      sectors,
            "top_sectors":  top_sectors,
            "avoid_sectors": avoid_sectors,
            "rotation_note": f"Money flowing into: {', '.join(top_sectors) or 'none'}. Avoiding: {', '.join(avoid_sectors) or 'none'}",
        }
    except Exception as e:
        return {"error": str(e)}


# ── News firehose (multi-source) ──────────────────────────────────────────────
NEWS_RSS_FEEDS = {
    "Yahoo Finance Markets": "https://finance.yahoo.com/rss/topstories",
    "Yahoo Finance Tech":    "https://finance.yahoo.com/rss/industry?ind=tech",
    "CNBC":                  "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch":           "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Seeking Alpha":         "https://seekingalpha.com/market_currents.xml",
    "Business Wire":         "https://feed.businesswire.com/rss/home/?rss=G1&rssid=20",
    "PR Newswire Finance":   "https://www.prnewswire.com/rss/financial-news-releases-list.rss",
    "Motley Fool":           "https://www.fool.com/feeds/index.aspx",
    "Barrons":               "https://www.barrons.com/xml/rss/3_7514.xml",
    "Investopedia":          "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline",
}

def fetch_news_firehose(max_per_feed=10):
    """
    Pull headlines from 10 financial news RSS feeds.
    Extracts tickers mentioned, surfaces breaking catalysts.
    """
    import re
    ticker_re = re.compile(r'\b([A-Z]{2,5})\b')
    ignore = {"THE","AND","FOR","ARE","NOT","BUT","ITS","NEW","ALL","ONE","CAN",
              "HOW","WHO","WHY","NOW","GET","SET","USE","SAY","CEO","CFO","IPO",
              "ETF","SEC","FED","GDP","CPI","AI","US","UK","EU","UN","DOJ","FTC",
              "EPS","PE","YOY","QOQ","MOM","EOD","ATH","ATL","VC","IPO","SPY",
              "NYSE","NASDAQ","DJIA","HFT"}

    all_headlines = []
    ticker_mentions = {}

    for source, url in NEWS_RSS_FEEDS.items():
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code != 200:
                continue
            # Parse RSS titles
            titles = re.findall(r'<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', r.text)
            for title in titles[1:max_per_feed+1]:  # skip feed title
                clean = re.sub(r'<[^>]+>', '', title).strip()
                if not clean or len(clean) < 10:
                    continue
                all_headlines.append({"source": source, "headline": clean})
                # Extract tickers mentioned in headline
                for ticker in set(ticker_re.findall(clean)) - ignore:
                    if 2 <= len(ticker) <= 5:
                        ticker_mentions[ticker] = ticker_mentions.get(ticker, 0) + 1
        except Exception:
            continue

    # Top mentioned tickers across all feeds
    hot_tickers = sorted(ticker_mentions.items(), key=lambda x: x[1], reverse=True)[:20]

    return {
        "timestamp":    datetime.now().isoformat(),
        "total_headlines": len(all_headlines),
        "sources_hit":  len([h for h in all_headlines]),
        "hot_tickers":  [{"symbol": t, "mentions": c} for t, c in hot_tickers],
        "headlines":    all_headlines[:50],
    }


# ── Congressional trading (Quiver Quantitative — free) ───────────────────────
def get_congressional_trades(days_back=14):
    """
    Fetch recent congressional stock trades from Quiver Quantitative.
    Congress members buying = strong conviction signal (they have information edge).
    """
    try:
        url = "https://api.quiverquant.com/beta/live/congresstrading"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            # Fallback: Capitol Trades RSS
            rss_url = "https://www.capitoltrades.com/trades?recent=true"
            r2 = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            return {"error": f"Quiver {r.status_code}", "fallback_status": r2.status_code}

        trades = r.json()[:50] if isinstance(r.json(), list) else []
        buys = []
        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        for t in trades:
            if t.get("Transaction", "").lower() == "purchase" and t.get("Date", "") >= cutoff:
                buys.append({
                    "symbol":    t.get("Ticker", ""),
                    "member":    t.get("Representative", ""),
                    "amount":    t.get("Amount", ""),
                    "date":      t.get("Date", ""),
                    "committee": t.get("Committee", ""),
                })
        buys.sort(key=lambda x: x["date"], reverse=True)
        return {"timestamp": datetime.now().isoformat(), "source": "quiver_quant", "congressional_buys": buys[:20]}
    except Exception as e:
        return {"error": str(e)}


# ── Dip opportunity detector ─────────────────────────────────────────────────
def detect_dip_opportunities(snapshots, min_drop_pct=15.0, min_price=5.0, min_volume=200_000):
    """
    Find stocks that dropped 25%+ today vs previous close.
    These are high-conviction dip buy candidates — quality stocks on panic selling.
    Runs against the snapshot data already fetched (no extra API calls).
    """
    dips = []
    for sym, snap in snapshots.items():
        try:
            daily      = snap.get("dailyBar", {})
            prev       = snap.get("prevDailyBar", {})
            price      = daily.get("c", 0) or snap.get("latestTrade", {}).get("p", 0)
            volume     = daily.get("v", 0)
            prev_close = prev.get("c", 0)

            if price < min_price or volume < min_volume or not prev_close:
                continue

            pct_change = (price - prev_close) / prev_close * 100

            if pct_change <= -min_drop_pct:
                dips.append({
                    "symbol":        sym,
                    "price":         round(price, 2),
                    "prev_close":    round(prev_close, 2),
                    "pct_change":    round(pct_change, 2),
                    "drop_pct":      round(abs(pct_change), 2),
                    "volume":        int(volume),
                    "dip_buy_candidate": True,
                })
        except Exception:
            continue

    dips.sort(key=lambda x: x["drop_pct"], reverse=True)
    if dips:
        print(f"  [screener] DIP ALERTS: {len(dips)} stocks down ≥{min_drop_pct:.0f}% today: "
              f"{[d['symbol'] for d in dips[:10]]}", file=sys.stderr)
    return dips


# ── Full morning scan ─────────────────────────────────────────────────────────
def full_scan(top_n=30):
    """
    Complete market scan:
    1. Load S&P 500 + Nasdaq 100 universe
    2. Batch snapshot pre-filter
    3. Return top N candidates by volume for deep research
    4. Sector rotation report
    5. News firehose
    6. Congressional trades
    """
    print(f"[screener] Starting full market scan...", file=sys.stderr)

    output = {
        "timestamp": datetime.now().isoformat(),
        "scan_type":  "full_morning",
    }

    # Universe
    universe = get_cached_universe()
    print(f"  [screener] Scanning {len(universe)} symbols...", file=sys.stderr)

    # Batch snapshots (500+ symbols in ~5 API calls)
    snapshots = batch_snapshots(universe)
    print(f"  [screener] Got snapshots for {len(snapshots)} symbols", file=sys.stderr)

    # Pre-filter
    candidates = pre_filter(snapshots)
    print(f"  [screener] {len(candidates)} passed pre-filter (price>$5, vol>500K, move<15%)", file=sys.stderr)

    output["top_candidates"]   = candidates[:top_n]
    output["total_scanned"]    = len(universe)
    output["passed_prefilter"] = len(candidates)

    # Dip opportunities (25%+ single-day drops) — uses snapshots already fetched
    dips = detect_dip_opportunities(snapshots)
    output["dip_opportunities"] = dips
    if dips:
        print(f"  [screener] {len(dips)} dip buy candidates flagged", file=sys.stderr)

    # Sector rotation
    print("  [screener] Sector rotation...", file=sys.stderr)
    output["sector_rotation"] = get_sector_rotation()

    # News firehose
    print("  [screener] News firehose (10 sources)...", file=sys.stderr)
    output["news_firehose"] = fetch_news_firehose()

    # Congressional trades
    print("  [screener] Congressional trades...", file=sys.stderr)
    output["congressional"] = get_congressional_trades()

    # Save output
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    out_path = os.path.join(JOURNAL_DIR, f"{date.today().isoformat()}-screener.json")
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"  [screener] Saved to {out_path}", file=sys.stderr)

    return output


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "scan"

    if action == "scan":
        out = full_scan()
        print(f"\nScanned: {out['total_scanned']} symbols")
        print(f"Passed filter: {out['passed_prefilter']}")
        print(f"\nTop candidates:")
        for c in out["top_candidates"][:10]:
            print(f"  {c['symbol']:6} ${c['price']:8.2f}  {c['pct_change']:+.1f}%  vol {c['volume']:>12,}")
        sr = out.get("sector_rotation", {})
        print(f"\nHot sectors: {', '.join(sr.get('top_sectors', []))}")
        print(f"Avoid:       {', '.join(sr.get('avoid_sectors', []))}")
        nf = out.get("news_firehose", {})
        print(f"\nNews firehose: {nf.get('total_headlines', 0)} headlines")
        print(f"Hot tickers in news: {[t['symbol'] for t in nf.get('hot_tickers', [])[:10]]}")

    elif action == "sp500":
        syms = get_sp500_symbols()
        print(f"S&P 500: {len(syms)} symbols")
        print(syms[:20])

    elif action == "sector":
        out = get_sector_rotation()
        for s in out.get("sectors", []):
            print(f"  {s['symbol']:5} {s['sector']:30} {s['change_pct']:+.2f}%  {s['signal']}")

    elif action == "congressional":
        out = get_congressional_trades()
        for t in out.get("congressional_buys", []):
            print(f"  {t['date']}  {t['symbol']:6}  {t['member']}  {t['amount']}")

    elif action == "news":
        out = fetch_news_firehose()
        print(f"Headlines: {out['total_headlines']}")
        print(f"Hot tickers: {[t['symbol'] for t in out['hot_tickers'][:15]]}")
        for h in out["headlines"][:20]:
            print(f"  [{h['source']}] {h['headline']}")
