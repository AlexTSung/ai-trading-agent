#!/usr/bin/env python3
"""
rebound_signal.py — oversold-rebound entry/exit rules, shared by the live path
and backtest_rebound.py. Stdlib only, no I/O, no network.

The indicators replicate research.compute_technicals() exactly (SMA20/SMA50 of
closes, RSI(14) as the repo's simple-mean version, ATR(14) as a simple mean of
true range, the MA signal rule) so a backtest of these rules measures the same
numbers the live Judge sees. SMA200, the prior day's high/close and 20-day
dollar volume are added for the rebound filters.

A bar is a dict with keys o, h, l, c, v, ascending by date.
"""

NAN = float("nan")

# Chosen by the walk-forward grid search in backtest_rebound.py using only data
# before 2023-01-01 (rank 1 by lower-bound excess return vs SPY, universe B). It
# was NOT re-tuned after the 2023+ holdout was read; see
# logs/backtest/rebound-backtest-2026-11-09.md for how thin that edge is.
DEFAULT_PARAMS = {
    # entry (the setup is always "close below MA50")
    "rsi_max": 25,
    "min_depth_pct": 12.0,
    "require_uptrend_200": True,
    "stabilization": 'none',        # none | close_rsi_up | close_gt_prev_high
    "market_filter": True,         # SPY > its SMA200 and VIX < VIX_MAX
    # exit
    "stop_type": 'atr',             # pct | atr
    "stop_value": 2.0,             # fraction of entry, or ATR multiple
    "target_type": 'pct',          # none | ma20 | pct
    "target_value": 0.08,            # fraction above entry when target_type == "pct"
    "time_stop_sessions": 15,
}

VIX_MAX = 40.0
CHASE_RSI_MAX = 60.0
CHASE_EXTENSION_MAX = 0.03
MIN_TARGET_ABOVE_ENTRY = 1.01       # an MA20 target below this is lifted to it
RSI_PERIOD = 14
ATR_PERIOD = 14
MIN_BARS = 200


# ── Indicators ────────────────────────────────────────────────────────────────
def feature_columns(o, h, l, c, v):
    """Per-bar indicator columns (lists, NaN until enough history). Prefix sums
    make each column O(n); the live path calls features() which reuses this."""
    n = len(c)
    nan_col = lambda: [NAN] * n

    def sma(window):
        out, run = nan_col(), 0.0
        for i in range(n):
            run += c[i]
            if i >= window:
                run -= c[i - window]
            if i >= window - 1:
                out[i] = run / window
        return out

    ma20, ma50, ma200 = sma(20), sma(50), sma(200)

    gain_run = loss_run = 0.0
    gains, losses = [0.0] * n, [0.0] * n
    rsi = nan_col()
    for i in range(1, n):
        d = c[i] - c[i - 1]
        gains[i], losses[i] = max(d, 0.0), max(-d, 0.0)
        gain_run += gains[i]
        loss_run += losses[i]
        if i > RSI_PERIOD:
            gain_run -= gains[i - RSI_PERIOD]
            loss_run -= losses[i - RSI_PERIOD]
        if i >= RSI_PERIOD:
            avg_gain, avg_loss = gain_run / RSI_PERIOD, loss_run / RSI_PERIOD
            if avg_loss <= 1e-12 * max(avg_gain, 1e-12):
                rsi[i] = 100.0
            else:
                rsi[i] = round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

    tr_run = 0.0
    tr = [0.0] * n
    atr = nan_col()
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        tr_run += tr[i]
        if i > ATR_PERIOD:
            tr_run -= tr[i - ATR_PERIOD]
        if i >= ATR_PERIOD:
            atr[i] = tr_run / ATR_PERIOD

    dvol = nan_col()
    run = 0.0
    for i in range(n):
        run += c[i] * v[i]
        if i >= 20:
            run -= c[i - 20] * v[i - 20]
        if i >= 19:
            dvol[i] = run / 20

    return {"ma20": ma20, "ma50": ma50, "ma200": ma200, "rsi": rsi, "atr": atr, "dvol20": dvol}


def signal_for(last, ma20, ma50, ma20_prev):
    """research.compute_technicals' MA signal rule, verbatim."""
    if ma20 > ma50 and last > ma20:
        return "STRONG_BUY"
    if ma20 > ma50 and last >= ma50:
        return "WEAK_BUY"
    if last < ma50:
        return "STRONG_SELL"
    if ma20 < ma20_prev:
        return "WEAK_SELL"
    return "HOLD"


def passes_chase_veto(rsi, last, ma20):
    """judge.check_chase_veto: True means the entry is allowed."""
    if rsi > CHASE_RSI_MAX:
        return False
    if ma20 > 0 and (last - ma20) / ma20 > CHASE_EXTENSION_MAX:
        return False
    return True


def features(bars):
    """Indicator dict for the LAST bar, or None with fewer than MIN_BARS bars."""
    if not bars or len(bars) < MIN_BARS:
        return None
    bars = bars[-(MIN_BARS + 60):]
    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    c = [b["c"] for b in bars]
    v = [b["v"] for b in bars]
    col = feature_columns(o, h, l, c, v)
    i = len(c) - 1
    last, ma20, ma50 = c[i], col["ma20"][i], col["ma50"][i]
    ma20_prev = col["ma20"][i - 1]
    atr = col["atr"][i]
    return {
        "last_close": last,
        "prev_close": c[i - 1],
        "prev_high": h[i - 1],
        "ma20": ma20,
        "ma20_prev": ma20_prev,
        "ma50": ma50,
        "ma200": col["ma200"][i],
        "rsi": col["rsi"][i],
        "rsi_prev": col["rsi"][i - 1],
        "atr": atr,
        "atr_pct": atr / last * 100 if last else NAN,
        "dvol20": col["dvol20"][i],
        "signal": signal_for(last, ma20, ma50, ma20_prev),
        "depth_below_ma50_pct": (ma50 - last) / ma50 * 100,
    }


# ── Entry ─────────────────────────────────────────────────────────────────────
def setup(f, params=None, market=None):
    """(ok, flags). `f` is a features() dict; `market` is
    {'spy_above_ma200': bool, 'vix': float} or None. Every flag is reported so
    a rejected candidate can say exactly which test failed. A requested market
    filter with no market data fails closed."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    flags = {
        "below_ma50":  f["last_close"] < f["ma50"],
        "oversold":    f["rsi"] < p["rsi_max"],
        "deep_enough": f["depth_below_ma50_pct"] >= p["min_depth_pct"],
    }
    flags["uptrend_200"] = (not p["require_uptrend_200"]) or f["last_close"] > f["ma200"]

    stab = p["stabilization"]
    if stab == "close_rsi_up":
        flags["stabilizing"] = f["last_close"] > f["prev_close"] and f["rsi"] > f["rsi_prev"]
    elif stab == "close_gt_prev_high":
        flags["stabilizing"] = f["last_close"] > f["prev_high"]
    else:
        flags["stabilizing"] = True

    if p["market_filter"]:
        flags["market_ok"] = bool(market) and bool(market.get("spy_above_ma200")) \
            and market.get("vix") is not None and market["vix"] < VIX_MAX
    else:
        flags["market_ok"] = True

    ok = all(flags.values())   # NaN comparisons are False, so thin history never passes
    return ok, flags


# ── Exit ──────────────────────────────────────────────────────────────────────
def exit_levels(entry_price, f, params=None):
    p = {**DEFAULT_PARAMS, **(params or {})}
    if p["stop_type"] == "atr":
        stop = entry_price - p["stop_value"] * f["atr"]
    else:
        stop = entry_price * (1 - p["stop_value"])
    if p["target_type"] == "ma20":
        target = max(f["ma20"], entry_price * MIN_TARGET_ABOVE_ENTRY)
    elif p["target_type"] == "pct":
        target = entry_price * (1 + p["target_value"])
    else:
        target = None
    return {"stop": stop, "target": target, "time_stop_sessions": p["time_stop_sessions"]}


def simulate_trade(window, f, params=None, slippage=0.001, cost=0.001):
    """Walk one trade through `window`, the bars from the ENTRY day forward
    (window[0] is the day entered at its open; the signal was the prior close).
    Conservative ordering: a gap through the stop fills at the open; if the stop
    and the target are both touched in one bar the stop is taken. Returns
    {'ret', 'exit_offset', 'exit_price', 'reason', 'mae', 'entry_fill'} or None
    if the window is shorter than the time stop."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    n_hold = p["time_stop_sessions"]
    if len(window) < n_hold:
        return None
    fill = window[0]["o"] * (1 + slippage)
    lv = exit_levels(fill, f, p)
    stop, target = lv["stop"], lv["target"]
    low_so_far = float("inf")
    for d in range(n_hold):
        b = window[d]
        low_so_far = min(low_so_far, b["l"])
        price = reason = None
        if d > 0 and b["o"] <= stop:
            price, reason = b["o"], "stop_gap"
        elif target is not None and d > 0 and b["o"] >= target:
            price, reason = b["o"], "target_gap"
        elif b["l"] <= stop:
            price, reason = stop, "stop"
        elif target is not None and b["h"] >= target:
            price, reason = target, "target"
        elif d == n_hold - 1:
            price, reason = b["c"], "time"
        if price is not None:
            return {"ret": price * (1 - cost) / (fill * (1 + cost)) - 1,
                    "exit_offset": d, "exit_price": price, "reason": reason,
                    "mae": low_so_far / fill - 1, "entry_fill": fill}
    return None
