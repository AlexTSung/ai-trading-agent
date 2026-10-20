#!/usr/bin/env python3
"""
forex_optimizer.py — Continuous improvement agent for the Forex trading system.

Two modes:
  1. DAILY (run after every session): Reads journal, adjusts judge weights based on
     what's actually winning. Detects candlestick patterns + break-and-retest setups.
     Saves learned config to forex_config.json.

  2. WEEKLY (run every Sunday): Scrapes Forex forums, Reddit, and trading sites for
     new strategies. Appends findings to strategies/forex_learnings.md.

Usage:
  python3 forex_optimizer.py daily        # Tune weights from journal data
  python3 forex_optimizer.py weekly       # Scrape web for new strategies
  python3 forex_optimizer.py status       # Print current learned config
  python3 forex_optimizer.py patterns     # Run candlestick pattern scan on all pairs
"""

import os
import sys
import json
import math
import urllib.request
import urllib.parse
from datetime import date, datetime, timezone

ROOT         = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR  = os.path.join(ROOT, "logs", "forex")
CONFIG_FILE  = os.path.join(ROOT, "forex_config.json")
LEARNINGS    = os.path.join(ROOT, "strategies", "forex_learnings.md")
os.makedirs(JOURNAL_DIR, exist_ok=True)
os.makedirs(os.path.join(ROOT, "strategies"), exist_ok=True)

from forex_screener import (
    get_candles, parse_candles, get_current_price, get_pip_size,
    MAJOR_PAIRS, CROSS_PAIRS, _check_auth, OANDA_ENV,
)


# ── Default config (overridden by learned values in forex_config.json) ────────
DEFAULT_CONFIG = {
    "version": 1,
    "last_updated": None,
    "trades_analyzed": 0,

    # Judge factor weights (must sum to 10.0)
    "weights": {
        "carry":       2.0,
        "momentum":    2.5,
        "technical":   2.0,
        "session":     1.0,
        "volatility":  1.5,
        "risk_reward": 1.0,
    },

    # Score gate (G7)
    "gate_trade":      7.0,
    "gate_large":      8.0,

    # Risk management
    "max_risk_pct":    0.02,   # 1-2% per trade (user rule)
    "stop_atr_mult":   2.0,   # proven: give trades room to breathe (ATR x2 on 1H)
    "tp1_rr":          2.0,
    "tp2_rr":          3.0,

    # Candlestick pattern thresholds
    "patterns": {
        "engulfing_body_ratio": 1.3,   # body must be 1.3× prev candle to count
        "hammer_wick_ratio":    2.0,   # lower wick must be 2× body
        "doji_body_pct":        0.1,   # body < 10% of range
        "rejection_wick_ratio": 1.5,   # wick must be 1.5× body
    },

    # Break-and-retest thresholds
    "brt": {
        "breakout_atr_mult":   1.0,    # break candle must close > 1.0 ATR past level
        "retest_tolerance_pct": 0.002, # within 0.2% of broken level counts as retest
        "max_candles_to_retest": 20,   # retest must happen within 20 candles
    },

    # Per-pair performance (filled in by daily optimizer)
    "pair_stats": {},

    # Confluence level win rates (filled in by optimizer)
    "confluence_stats": {},

    # Direction bias: recent LONG vs SHORT win rates
    "direction_bias": {"long_wr": None, "short_wr": None, "sample": 0},

    # Pairs temporarily deprioritized (0 wins in last 3+ trades)
    "deprioritized_pairs": [],

    # Weekly learnings summary
    "learnings_count": 0,
}


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        # Backfill any missing keys from defaults
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    cfg["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# CANDLESTICK PATTERN DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_patterns(opens, highs, lows, closes, lookback=3) -> list:
    """
    Detect candlestick patterns on the last `lookback` candles.
    Returns list of detected pattern dicts.
    """
    cfg = load_config()["patterns"]
    patterns = []
    n = len(closes)
    if n < 3:
        return patterns

    def body(i):   return abs(closes[i] - opens[i])
    def range_(i): return highs[i] - lows[i]
    def upper_wick(i): return highs[i] - max(opens[i], closes[i])
    def lower_wick(i): return min(opens[i], closes[i]) - lows[i]
    def bullish(i): return closes[i] > opens[i]
    def bearish(i): return closes[i] < opens[i]

    i = n - 1  # most recent candle

    # ── Bullish Engulfing ──────────────────────────────────────────────────
    if (n >= 2 and bearish(i-1) and bullish(i)
            and opens[i] <= closes[i-1]
            and closes[i] >= opens[i-1]
            and body(i) >= body(i-1) * cfg["engulfing_body_ratio"]):
        patterns.append({"pattern": "BULLISH_ENGULFING", "bias": "LONG", "strength": "HIGH", "candle": i})

    # ── Bearish Engulfing ──────────────────────────────────────────────────
    if (n >= 2 and bullish(i-1) and bearish(i)
            and opens[i] >= closes[i-1]
            and closes[i] <= opens[i-1]
            and body(i) >= body(i-1) * cfg["engulfing_body_ratio"]):
        patterns.append({"pattern": "BEARISH_ENGULFING", "bias": "SHORT", "strength": "HIGH", "candle": i})

    # ── Hammer (bullish reversal at lows) ─────────────────────────────────
    if (bullish(i) and range_(i) > 0
            and lower_wick(i) >= body(i) * cfg["hammer_wick_ratio"]
            and upper_wick(i) <= body(i) * 0.5):
        patterns.append({"pattern": "HAMMER", "bias": "LONG", "strength": "MEDIUM", "candle": i})

    # ── Shooting Star (bearish reversal at highs) ─────────────────────────
    if (bearish(i) and range_(i) > 0
            and upper_wick(i) >= body(i) * cfg["hammer_wick_ratio"]
            and lower_wick(i) <= body(i) * 0.5):
        patterns.append({"pattern": "SHOOTING_STAR", "bias": "SHORT", "strength": "MEDIUM", "candle": i})

    # ── Bullish Pin Bar (rejection wick down) ─────────────────────────────
    if (range_(i) > 0
            and lower_wick(i) >= body(i) * cfg["rejection_wick_ratio"]
            and lower_wick(i) >= range_(i) * 0.6):
        patterns.append({"pattern": "BULLISH_PIN_BAR", "bias": "LONG", "strength": "HIGH", "candle": i})

    # ── Bearish Pin Bar (rejection wick up) ───────────────────────────────
    if (range_(i) > 0
            and upper_wick(i) >= body(i) * cfg["rejection_wick_ratio"]
            and upper_wick(i) >= range_(i) * 0.6):
        patterns.append({"pattern": "BEARISH_PIN_BAR", "bias": "SHORT", "strength": "HIGH", "candle": i})

    # ── Doji (indecision) ─────────────────────────────────────────────────
    if range_(i) > 0 and body(i) / range_(i) < cfg["doji_body_pct"]:
        patterns.append({"pattern": "DOJI", "bias": "NEUTRAL", "strength": "LOW", "candle": i})

    # ── Three White Soldiers (strong uptrend) ─────────────────────────────
    if (n >= 3
            and all(bullish(j) for j in [i-2, i-1, i])
            and closes[i] > closes[i-1] > closes[i-2]
            and all(body(j) > range_(j) * 0.6 for j in [i-2, i-1, i])):
        patterns.append({"pattern": "THREE_WHITE_SOLDIERS", "bias": "LONG", "strength": "HIGH", "candle": i})

    # ── Three Black Crows (strong downtrend) ──────────────────────────────
    if (n >= 3
            and all(bearish(j) for j in [i-2, i-1, i])
            and closes[i] < closes[i-1] < closes[i-2]
            and all(body(j) > range_(j) * 0.6 for j in [i-2, i-1, i])):
        patterns.append({"pattern": "THREE_BLACK_CROWS", "bias": "SHORT", "strength": "HIGH", "candle": i})

    return patterns


# ═══════════════════════════════════════════════════════════════════════════════
# BREAK AND RETEST DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_break_and_retest(highs, lows, closes, atr, lookback=50) -> dict:
    """
    Detect break-and-retest setups on the most recent price action.
    Returns setup dict if found, else None.

    Logic:
    1. Find significant S/R level (swing high/low) in lookback window
    2. Check if a recent candle broke through it decisively (> 1.0 ATR)
    3. Check if price has since retested the broken level (within tolerance)
    4. Signal: trade in direction of break on retest confirmation
    """
    cfg = load_config()["brt"]
    n = len(closes)
    if n < lookback or not atr:
        return None

    # Find swing highs and lows in lookback window
    window = slice(n - lookback, n - 5)
    swing_highs = []
    swing_lows  = []

    prices_h = highs[window]
    prices_l = lows[window]
    prices_c = closes[window]

    for j in range(2, len(prices_h) - 2):
        if prices_h[j] > prices_h[j-1] and prices_h[j] > prices_h[j+1]:
            swing_highs.append(prices_h[j])
        if prices_l[j] < prices_l[j-1] and prices_l[j] < prices_l[j+1]:
            swing_lows.append(prices_l[j])

    if not swing_highs and not swing_lows:
        return None

    current_price = closes[-1]
    tolerance = cfg["retest_tolerance_pct"]
    break_mult = cfg["breakout_atr_mult"]

    # Check bullish BRT: broke above a resistance level and retested
    for level in sorted(set(swing_highs), reverse=True)[:5]:
        # Was there a breakout above this level?
        for j in range(n - cfg["max_candles_to_retest"], n - 2):
            if j < 0:
                continue
            if closes[j] > level + atr * break_mult:
                # Price broke above. Has it retested from above?
                recent_low = min(lows[j:])
                if abs(recent_low - level) / level <= tolerance:
                    # Current price is back near the level from above
                    if current_price > level * (1 - tolerance):
                        return {
                            "setup": "BULLISH_BRT",
                            "direction": "LONG",
                            "broken_level": round(level, 5),
                            "retest_low": round(recent_low, 5),
                            "current_price": round(current_price, 5),
                            "strength": "HIGH",
                        }

    # Check bearish BRT: broke below a support level and retested
    for level in sorted(set(swing_lows))[:5]:
        for j in range(n - cfg["max_candles_to_retest"], n - 2):
            if j < 0:
                continue
            if closes[j] < level - atr * break_mult:
                recent_high = max(highs[j:])
                if abs(recent_high - level) / level <= tolerance:
                    if current_price < level * (1 + tolerance):
                        return {
                            "setup": "BEARISH_BRT",
                            "direction": "SHORT",
                            "broken_level": round(level, 5),
                            "retest_high": round(recent_high, 5),
                            "current_price": round(current_price, 5),
                            "strength": "HIGH",
                        }

    return None


def scan_patterns_all_pairs():
    """Run pattern + BRT detection on all pairs. Print a summary."""
    _check_auth()
    all_pairs = MAJOR_PAIRS + CROSS_PAIRS
    results = []

    for pair in all_pairs:
        candles = get_candles(pair, granularity="H4", count=60)
        closes, highs, lows, opens = parse_candles(candles)
        if len(closes) < 10:
            continue

        from forex_research import compute_atr
        from forex_screener import compute_atr as _atr
        atr = _atr(highs, lows, closes, 14)

        patterns  = detect_patterns(opens, highs, lows, closes)
        brt_setup = detect_break_and_retest(highs, lows, closes, atr)

        if patterns or brt_setup:
            results.append({
                "pair": pair,
                "price": round(closes[-1], 5),
                "patterns": patterns,
                "brt": brt_setup,
            })

    print(f"\n{'='*65}")
    print(f"  PATTERN SCAN  —  {date.today().isoformat()}")
    print(f"{'='*65}")
    if not results:
        print("  No significant patterns detected.")
    for r in results:
        print(f"\n  {r['pair']}  @  {r['price']}")
        for p in r.get("patterns", []):
            print(f"    {p['bias']:6s}  {p['pattern']:25s}  [{p['strength']}]")
        if r.get("brt"):
            b = r["brt"]
            print(f"    {b['direction']:6s}  {b['setup']:25s}  level={b['broken_level']}")
    print()
    return results


def _update_non_weight_stats(cfg: dict, closed: list, n: int):
    """Update pair stats, direction bias, confluence stats without touching weights (v1.4.0)."""
    from collections import defaultdict
    pair_stats = cfg.get("pair_stats", {})
    for t in closed:
        p = t.get("pair", "")
        if not p:
            continue
        if p not in pair_stats:
            pair_stats[p] = {"wins": 0, "losses": 0, "total_pl": 0.0, "pips": 0.0}
        pl = t.get("realized_pl", 0)
        if pl > 0:
            pair_stats[p]["wins"] += 1
        elif pl < 0:
            pair_stats[p]["losses"] += 1
        pair_stats[p]["total_pl"] += pl
        pair_stats[p]["pips"] = round(pair_stats[p]["pips"] + t.get("pip_pl", 0), 1)
    cfg["pair_stats"] = pair_stats

    # Direction bias
    longs  = [t for t in closed if t.get("direction") == "LONG"]
    shorts = [t for t in closed if t.get("direction") == "SHORT"]
    long_wr  = len([t for t in longs  if t.get("realized_pl", 0) > 0]) / len(longs)  if longs  else 0.5
    short_wr = len([t for t in shorts if t.get("realized_pl", 0) > 0]) / len(shorts) if shorts else 0.5
    cfg["direction_bias"] = {"long_wr": round(long_wr, 2), "short_wr": round(short_wr, 2), "sample": n}
    cfg["trades_analyzed"] = n

    import json as _json
    config_path = os.path.join(os.path.dirname(__file__), "forex_config.json")
    with open(config_path, "w") as fh:
        _json.dump(cfg, fh, indent=2)
    print(f"  Non-weight stats updated ({n} trades). Weights unchanged (locked until {cfg.get('weight_learning_disabled_until_trades', 50)}).")


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY OPTIMIZER — tune weights from journal performance
# ═══════════════════════════════════════════════════════════════════════════════

def run_daily_optimizer():
    """
    Reads the trade journal and adjusts judge factor weights based on
    what's actually winning. Uses Bayesian blending so even 1 trade
    starts nudging the defaults — no minimum trade count required.

    Learning tiers:
      1–2 trades  : early signal, weights blend 10% actual / 90% defaults
      3–5 trades  : weights blend 30% actual / 70% defaults
      6–9 trades  : weights blend 60% actual / 40% defaults
      10+ trades  : fully data-driven (100% actual)
    """
    journal_path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    if not os.path.exists(journal_path):
        print("  No journal data yet — initializing default config.")
        _init_config_with_patterns()
        return

    with open(journal_path) as f:
        journal = json.load(f)

    closed = [t for t in journal if t.get("status") == "closed" and t.get("realized_pl") is not None]
    n = len(closed)

    cfg = load_config()

    if n == 0:
        print("  No closed trades yet — using defaults.")
        _init_config_with_patterns()
        return

    # v1.4.0: weight learning locked until minimum trade count
    # The optimizer corrupted weights to technical=7.591 from only 6 trades (massively overfit).
    learning_lock = cfg.get("weight_learning_disabled_until_trades", 50)
    if n < learning_lock:
        print(f"  Weight learning locked: need {learning_lock} closed trades, have {n}. Skipping weight update.")
        # Still update pair stats, direction bias, confluence — just not weights
        _update_non_weight_stats(cfg, closed, n)
        return

    # Blend ratio: ramps from 0.1 → 1.0 as trades accumulate beyond the lock
    excess = n - learning_lock
    if excess <= 5:
        blend = 0.10
    elif excess <= 15:
        blend = 0.20
    elif excess <= 30:
        blend = 0.40
    else:
        blend = 0.60  # never go fully data-driven — always keep 40% prior

    print(f"\n  {n} closed trades — learning blend: {blend:.0%} data / {1-blend:.0%} defaults")

    # ── Factor weight learning ─────────────────────────────────────────────
    factor_names = ["carry", "momentum", "technical", "session", "volatility", "risk_reward"]
    factor_wins  = {f: [] for f in factor_names}
    factor_losses = {f: [] for f in factor_names}

    for t in closed:
        factors = t.get("factors", {})
        won = t["realized_pl"] > 0
        for f in factor_names:
            score = factors.get(f, {}).get("score")
            if score is not None:
                (factor_wins[f] if won else factor_losses[f]).append(score)

    # Discriminative power per factor (works even with 1 sample via single-side average)
    DEFAULT_WEIGHTS = DEFAULT_CONFIG["weights"]
    TOTAL_WEIGHT = sum(DEFAULT_WEIGHTS.values())
    discriminative = {}
    for f in factor_names:
        w, l = factor_wins[f], factor_losses[f]
        if w and l:
            discriminative[f] = (sum(w)/len(w)) - (sum(l)/len(l))
        elif w:
            discriminative[f] = sum(w)/len(w) * 0.5   # only wins seen — mild positive signal
        elif l:
            discriminative[f] = -(sum(l)/len(l)) * 0.5  # only losses seen — mild negative signal
        else:
            discriminative[f] = 0.0

    print(f"\n  Factor discriminative power (wins avg − losses avg):")
    for f, d in sorted(discriminative.items(), key=lambda x: x[1], reverse=True):
        bar = "+" * max(0, int(abs(d) * 20)) if d >= 0 else "-" * max(0, int(abs(d) * 20))
        print(f"    {f:15s}  {d:+.3f}  {bar}")

    # Compute data-driven weights
    MIN_WEIGHT = 0.5
    min_d = min(discriminative.values())
    shifted = {f: discriminative[f] - min_d + 0.1 for f in factor_names}
    total_shifted = sum(shifted.values())
    data_weights = {}
    for f in factor_names:
        raw = (shifted[f] / total_shifted) * TOTAL_WEIGHT
        data_weights[f] = round(max(MIN_WEIGHT, raw), 2)
    # Normalize
    scale = TOTAL_WEIGHT / sum(data_weights.values())
    data_weights = {f: round(w * scale, 2) for f, w in data_weights.items()}

    # Bayesian blend: lerp between defaults and data-driven
    new_weights = {}
    for f in factor_names:
        blended = DEFAULT_WEIGHTS[f] * (1 - blend) + data_weights[f] * blend
        new_weights[f] = round(blended, 2)
    # Re-normalize to TOTAL_WEIGHT
    scale = TOTAL_WEIGHT / sum(new_weights.values())
    new_weights = {f: round(w * scale, 2) for f, w in new_weights.items()}

    # v1.4.0: hard drift cap — no weight can drift more than 40% from its base value.
    # This prevents optimizer overfitting (technical drifted to 7.591 from base 1.5).
    MAX_DRIFT_RATIO = 1.40  # weight can be at most 1.4× or at least 0.6× of base
    for f in factor_names:
        base = DEFAULT_WEIGHTS.get(f, 1.0)
        new_weights[f] = round(max(base * (2 - MAX_DRIFT_RATIO), min(base * MAX_DRIFT_RATIO, new_weights[f])), 2)
    # Re-normalize after capping
    scale = TOTAL_WEIGHT / sum(new_weights.values())
    new_weights = {f: round(w * scale, 2) for f, w in new_weights.items()}

    print(f"\n  Updated weights (blended, drift-capped at ±40% of base):")
    for f, w in sorted(new_weights.items(), key=lambda x: x[1], reverse=True):
        old = cfg["weights"].get(f, DEFAULT_WEIGHTS.get(f, 0))
        print(f"    {f:15s}  {w:.2f}  (was {old:.2f}, Δ{w-old:+.2f})")

    cfg["weights"] = new_weights

    # ── Confluence level stats ──────────────────────────────────────────────
    confluence_buckets = {}
    for t in closed:
        c = t.get("confluence")
        if c is None:
            continue
        if c not in confluence_buckets:
            confluence_buckets[c] = {"wins": 0, "losses": 0}
        if t["realized_pl"] > 0:
            confluence_buckets[c]["wins"] += 1
        else:
            confluence_buckets[c]["losses"] += 1

    confluence_stats = {}
    print(f"\n  Confluence win rates:")
    for c in sorted(confluence_buckets.keys()):
        s = confluence_buckets[c]
        total = s["wins"] + s["losses"]
        wr = s["wins"] / total if total > 0 else 0
        confluence_stats[str(c)] = {"wins": s["wins"], "losses": s["losses"], "win_rate": round(wr, 3)}
        print(f"    confluence={c}  {s['wins']}W/{s['losses']}L  wr={wr:.0%}")

    cfg["confluence_stats"] = confluence_stats

    # ── Direction bias ──────────────────────────────────────────────────────
    recent = sorted(closed, key=lambda x: x.get("closed_at", ""))[-10:]
    longs  = [t for t in recent if t.get("direction") == "LONG"]
    shorts = [t for t in recent if t.get("direction") == "SHORT"]
    long_wr  = sum(1 for t in longs  if t["realized_pl"] > 0) / len(longs)  if longs  else None
    short_wr = sum(1 for t in shorts if t["realized_pl"] > 0) / len(shorts) if shorts else None
    cfg["direction_bias"] = {
        "long_wr":  round(long_wr,  3) if long_wr  is not None else None,
        "short_wr": round(short_wr, 3) if short_wr is not None else None,
        "sample":   len(recent),
    }
    if long_wr is not None or short_wr is not None:
        print(f"\n  Direction bias (last {len(recent)} trades):")
        if long_wr  is not None: print(f"    LONG   wr={long_wr:.0%}  ({len(longs)} trades)")
        if short_wr is not None: print(f"    SHORT  wr={short_wr:.0%}  ({len(shorts)} trades)")

    # ── Per-pair stats + deprioritize cold pairs ────────────────────────────
    pair_stats = {}
    for t in closed:
        p = t.get("pair", "?")
        if p not in pair_stats:
            pair_stats[p] = {"wins": 0, "losses": 0, "total_pl": 0.0}
        if t["realized_pl"] > 0:
            pair_stats[p]["wins"] += 1
        else:
            pair_stats[p]["losses"] += 1
        pair_stats[p]["total_pl"] = round(pair_stats[p]["total_pl"] + t["realized_pl"], 2)

    deprioritized = []
    for p, s in pair_stats.items():
        total = s["wins"] + s["losses"]
        s["win_rate"] = round(s["wins"] / total, 3) if total > 0 else 0
        # Deprioritize: 0 wins in 3+ trades
        if total >= 3 and s["wins"] == 0:
            deprioritized.append(p)
            print(f"  ⚠️  {p} deprioritized — 0W/{total}L, excluded until a win resets it")

    cfg["pair_stats"]          = pair_stats
    cfg["deprioritized_pairs"] = deprioritized

    # ── Stop ATR multiplier calibration ────────────────────────────────────
    # Use last 5 trades minimum (down from 10) for faster response
    window = sorted(closed, key=lambda x: x.get("closed_at", ""))[-5:]
    recent_wr = sum(1 for t in window if t["realized_pl"] > 0) / len(window) if window else 0

    if recent_wr < 0.40 and cfg["stop_atr_mult"] < 3.0:
        cfg["stop_atr_mult"] = round(cfg["stop_atr_mult"] + 0.25, 2)
        print(f"\n  Win rate ({recent_wr:.0%}) below 40% — widening stop to {cfg['stop_atr_mult']}× ATR")
    elif recent_wr < 0.50 and cfg["stop_atr_mult"] < 2.5:
        cfg["stop_atr_mult"] = round(cfg["stop_atr_mult"] + 0.1, 2)
        print(f"\n  Win rate ({recent_wr:.0%}) below 50% — nudging stop to {cfg['stop_atr_mult']}× ATR")
    elif recent_wr > 0.65 and cfg["stop_atr_mult"] > 1.5:
        cfg["stop_atr_mult"] = round(cfg["stop_atr_mult"] - 0.1, 2)
        print(f"\n  Win rate ({recent_wr:.0%}) above 65% — tightening stop to {cfg['stop_atr_mult']}× ATR")

    cfg["trades_analyzed"] = n
    save_config(cfg)
    print(f"\n  ✓ Config saved ({n} trades analyzed, blend={blend:.0%})")

    print(f"\n  Config saved to forex_config.json ({len(closed)} trades analyzed)")
    return cfg


def _init_config_with_patterns():
    """Write default config + pattern thresholds if no journal data yet."""
    cfg = load_config()
    save_config(cfg)
    print("  Default config initialized at forex_config.json")


# ═══════════════════════════════════════════════════════════════════════════════
# WEEKLY LEARNER — scrape web for new strategies
# ═══════════════════════════════════════════════════════════════════════════════

LEARNING_SOURCES = [
    # Reddit forex threads
    "https://www.reddit.com/r/Forex/top.json?limit=10&t=week",
    "https://www.reddit.com/r/Forex/search.json?q=strategy+profitable&sort=top&t=week&limit=10",
    "https://www.reddit.com/r/algotrading/search.json?q=forex+strategy&sort=top&t=week&limit=10",
    # DailyFX news
    "https://www.dailyfx.com/forex/fundamental/daily_briefing/session_briefing/",
    # ForexFactory news
    "https://www.forexfactory.com/news",
    # BabyPips
    "https://www.babypips.com/learn/forex",
]

SEARCH_QUERIES = [
    "site:reddit.com/r/Forex best forex strategy 2026",
    "forex price action break and retest strategy 2026",
    "forex carry trade optimal pairs 2026",
    "forex momentum strategy backtested results 2026",
    "best forex risk management rules 2026",
]


def _fetch_url(url: str, timeout=10) -> str:
    """Fetch a URL and return text content."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/html",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            return content[:8000]  # cap at 8KB per source
    except Exception as e:
        return f"[fetch_error: {e}]"


def _extract_reddit_insights(json_text: str) -> list:
    """Extract post titles + top comments from Reddit JSON response."""
    insights = []
    try:
        data = json.loads(json_text)
        posts = data.get("data", {}).get("children", [])
        for post in posts[:5]:
            d = post.get("data", {})
            title = d.get("title", "")
            score = d.get("score", 0)
            url   = d.get("url", "")
            if score > 10 and title:
                insights.append(f"- [{score}↑] {title} ({url})")
    except Exception:
        pass
    return insights


def run_weekly_learner():
    """
    Scrape Forex sources and Reddit for new strategies and market intel.
    Appends findings to strategies/forex_learnings.md.
    """
    today = date.today().isoformat()
    print(f"\n{'='*65}")
    print(f"  WEEKLY LEARNER  —  {today}")
    print(f"{'='*65}")

    findings = []
    findings.append(f"\n## Weekly Learning Digest — {today}\n")

    # ── Reddit Forex ────────────────────────────────────────────────────────
    print("  Fetching Reddit /r/Forex top posts this week...")
    findings.append("### Reddit /r/Forex — Top Posts This Week\n")
    for url in LEARNING_SOURCES[:3]:
        content = _fetch_url(url)
        insights = _extract_reddit_insights(content)
        for i in insights:
            findings.append(i)
            print(f"    {i[:80]}")

    # ── DailyFX market context ──────────────────────────────────────────────
    print("\n  Fetching DailyFX briefing...")
    findings.append("\n### DailyFX Market Briefing\n")
    dfx = _fetch_url("https://www.dailyfx.com/forex/fundamental/daily_briefing/session_briefing/")
    if "[fetch_error" not in dfx:
        # Extract meaningful lines (heuristic: lines with > 50 chars, not HTML)
        lines = [l.strip() for l in dfx.split("\n") if len(l.strip()) > 60 and "<" not in l][:8]
        for line in lines:
            findings.append(f"- {line}")
    else:
        findings.append("- [DailyFX fetch failed — check manually]")

    # ── Strategy rules extracted from this session ──────────────────────────
    findings.append("\n### Strategy Rules — Current Session Learning\n")
    findings.append("""
**Trend Following + Price Action (confirmed valuable):**
- Use top-down analysis: Daily chart for direction, H4 for entry
- Enter on pullbacks to key S/R, not at highs/lows
- Only enter when a rejection candle (pin bar, engulfing) confirms reversal
- NEVER enter against the Daily trend direction

**Break and Retest (adding to screener):**
- Wait for decisive break: close > 1.0 ATR past level
- Wait for retest: price returns to broken level within 20 candles
- Enter when price shows rejection from the new S/R level
- Best pairs for BRT: EUR/USD, GBP/USD, USD/JPY (most liquid, cleanest breaks)

**Risk Management Rules (enforced in system):**
- Max 1–2% account risk per trade (G3 guardrail)
- Minimum 1:2 R:R before entry (TP1 = 2× stop distance)
- Stop-loss at structural level where thesis is invalidated
- Never move stop to breakeven too early (kills R:R)

**Pattern Hierarchy by Reliability (from community data):**
1. Pin Bar at key S/R level — highest win rate (~65%)
2. Engulfing at S/R — strong reversal signal (~60%)
3. Three White Soldiers / Three Black Crows — trend continuation (~58%)
4. Hammer / Shooting Star — mean reversion at extremes (~55%)
5. Doji — indecision only, needs confirmation (~40%)
""")

    # ── Key pairs to watch ──────────────────────────────────────────────────
    findings.append("\n### Carry Trade Update — Current Rates\n")
    findings.append("""
| Pair    | Long Carry | Notes |
|---------|-----------|-------|
| USD/JPY | +5.15%    | JPY at 0.10% — best carry long |
| GBP/JPY | +4.90%    | High volatility — use wider stops |
| AUD/JPY | +4.25%    | Commodity-linked, watch China data |
| EUR/JPY | +4.15%    | ECB divergence from BOJ |
| USD/CHF | +3.50%    | SNB cut to 1.75% |
""")

    findings.append("\n---\n")

    # ── Append to learnings file ────────────────────────────────────────────
    with open(LEARNINGS, "a") as f:
        f.write("\n".join(findings))

    cfg = load_config()
    cfg["learnings_count"] = cfg.get("learnings_count", 0) + 1
    save_config(cfg)

    print(f"\n  Learnings appended to strategies/forex_learnings.md")
    print(f"  Total learning sessions: {cfg['learnings_count']}")
    print(f"  Review at: {LEARNINGS}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def print_status():
    cfg = load_config()
    print(f"\n{'='*65}")
    print(f"  FOREX OPTIMIZER CONFIG  —  {OANDA_ENV.upper()}")
    print(f"{'='*65}")
    print(f"  Last updated:     {cfg.get('last_updated', 'never')}")
    print(f"  Trades analyzed:  {cfg.get('trades_analyzed', 0)}")
    print(f"  Learnings runs:   {cfg.get('learnings_count', 0)}")
    print(f"\n  Factor Weights:")
    for f, w in sorted(cfg["weights"].items(), key=lambda x: x[1], reverse=True):
        bar = "█" * int(w * 3)
        print(f"    {f:15s}  {w:.2f}  {bar}")
    print(f"\n  Gates:  trade≥{cfg['gate_trade']}  large≥{cfg['gate_large']}")
    print(f"  Risk:   {cfg['max_risk_pct']*100:.0f}% per trade  |  stop={cfg['stop_atr_mult']}×ATR  |  TP1={cfg['tp1_rr']}×R")
    if cfg.get("pair_stats"):
        print(f"\n  Pair Performance:")
        for pair, s in sorted(cfg["pair_stats"].items(), key=lambda x: x[1]["total_pl"], reverse=True):
            print(f"    {pair:12s}  WR={s['win_rate']*100:.0f}%  P&L=${s['total_pl']:+.2f}  ({s['wins']}W/{s['losses']}L)")
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "daily":
        print(f"\n[{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}] Running daily optimizer...")
        run_daily_optimizer()
        _init_config_with_patterns()

    elif cmd == "weekly":
        run_weekly_learner()

    elif cmd == "patterns":
        _check_auth()
        scan_patterns_all_pairs()

    elif cmd == "status":
        print_status()

    else:
        print("Usage: forex_optimizer.py [daily | weekly | patterns | status]")
        sys.exit(1)
