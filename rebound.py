#!/usr/bin/env python3
"""
rebound.py — live oversold-rebound scan, shadow ledger, resolver, and the
real-money suggestion text.

The trend-following Judge cannot buy an oversold bounce: a stock that sold off
sits below its 50-day average, which the MA rule labels STRONG_SELL, so it never
reaches the Judge. This is the separate path that looks for exactly that setup.
It is PAPER-ONLY. Nothing here, or in rebound_trade.py, can place a real-money
order: real-money trades only ever happen if Alex places them himself.

Signal rules live in rebound_signal.py, shared with backtest_rebound.py so the
live scan and the backtest evaluate the same numbers.

How it learns fast. Paper trades are rare, so learning only from them is slow.
Every run logs EVERY setup it sees, across the core, the speculative sleeve, the
probation names and the broad screener universe, into a shadow ledger -- using a
deliberately wider net than the rules that actually trade. The resolver later
fills in what each one would have done (same exit rules, next-open fill). The
learner (rebound_learning.py) mines that ledger and may tighten the trading
rules on its own; loosening is only ever a proposal for Alex.

Usage:
  python3 rebound.py scan       # scan, write today's candidates + ledger rows
  python3 rebound.py resolve    # fill in outcomes for earlier ledger rows
"""

import csv
import json
import os
import statistics
import sys
from datetime import date, datetime, timedelta

import requests

import rebound_signal as rs
import universe_policy
from market_time import et_now, et_today, et_today_iso
from version import get_version

ROOT   = os.path.dirname(os.path.abspath(__file__))
LOGS   = os.path.join(ROOT, "logs", "paper")
LEARN  = os.path.join(ROOT, "logs", "learnings")
LEDGER_PATH   = os.path.join(LOGS, "rebound-ledger.csv")
LEARNED_PATH  = os.path.join(LEARN, "rebound_params.json")
DATA_URL      = "https://data.alpaca.markets"
LEARNED_SCOPE = "alpaca_equities_rebound"

# ── Live sizing and risk (paper) ──────────────────────────────────────────────
# Half the trend path's conviction sizes and a hard cap on how many can be open:
# this strategy is new and unproven, so it is small until its own record says
# otherwise.
LIVE_DEFAULTS = {
    "position_pct": 2.5,        # % of honest equity per position
    "max_open": 4,              # at most 10% of equity in the sleeve
    "max_gap_up_pct": 3.0,      # skip if the ask is >3% above the signal close
    "blocked_symbols": [],
}
FLOORS = {"position_pct": 1.0, "max_open": 1, "time_stop_sessions": 3, "stop_value_pct": 0.03}

MIN_PRICE_SHADOW = 10.0
LEDGER_MAX_AGE_DAYS = 45
HORIZONS = (3, 5, 10, 20)
EXIT_KEYS = ("stop_type", "stop_value", "target_type", "target_value", "time_stop_sessions")

# Shown with every suggestion: the backtest did not find an edge strong enough to
# rely on, and a suggestion should never read more confident than the evidence.
BACKTEST_NOTE = ("Backtest 2001-2026 (logs/backtest/rebound-backtest-2026-11-09.md): no reliable edge yet. "
                 "S&P 500 holdout +0.21% vs SPY per trade, interval includes zero. Treat this as a paper "
                 "experiment; a real-money trade on it is speculative.")

# Proof bar for calling a setup "PROVEN" in a real-money suggestion.
PROVEN_MIN_N, PROVEN_MIN_WIN, PROVEN_MIN_EXCESS = 20, 0.55, 0.0

LEDGER_FIELDS = [
    "signal_date", "symbol", "tier", "price", "rsi", "rsi_prev", "depth_pct", "atr_pct", "atr", "ma20",
    "above_ma200", "stab_rsi_up", "stab_prev_high", "spy_above_ma50", "spy_above_ma200", "vix",
    "would_trade", "paper_traded", "exit_params",
    "entry_fill", "sim_ret", "sim_reason", "sim_exit_offset", "sim_mae",
    "r3", "r5", "r10", "r20", "spy_r3", "spy_r5", "spy_r10", "spy_r20",
]


# ── Params ────────────────────────────────────────────────────────────────────
def _exit_subset(params: dict) -> dict:
    return {k: params[k] for k in EXIT_KEYS}


def tighten_only(base: dict, learned: dict) -> dict:
    """Apply learned parameters to `base`, keeping only the ones that make the
    strategy stricter. A file that tries to loosen anything is ignored for that
    key, so an edited or corrupted params file can never widen the net."""
    p = dict(base)
    if learned.get("rsi_max") is not None:
        p["rsi_max"] = min(p["rsi_max"], learned["rsi_max"])
    if learned.get("min_depth_pct") is not None:
        p["min_depth_pct"] = max(p["min_depth_pct"], learned["min_depth_pct"])
    if learned.get("require_uptrend_200"):
        p["require_uptrend_200"] = True
    if learned.get("market_filter"):
        p["market_filter"] = True
    if p["stabilization"] == "none" and learned.get("stabilization") in ("close_rsi_up", "close_gt_prev_high"):
        p["stabilization"] = learned["stabilization"]
    if learned.get("position_pct") is not None:
        p["position_pct"] = max(FLOORS["position_pct"], min(p["position_pct"], learned["position_pct"]))
    if learned.get("max_open") is not None:
        p["max_open"] = max(FLOORS["max_open"], min(p["max_open"], int(learned["max_open"])))
    if learned.get("time_stop_sessions") is not None:
        p["time_stop_sessions"] = max(FLOORS["time_stop_sessions"],
                                      min(p["time_stop_sessions"], int(learned["time_stop_sessions"])))
    if learned.get("stop_value") is not None and p["stop_type"] == "pct":
        p["stop_value"] = max(FLOORS["stop_value_pct"], min(p["stop_value"], learned["stop_value"]))
    p["blocked_symbols"] = sorted(set(p.get("blocked_symbols", [])) | set(learned.get("blocked_symbols", [])))
    return p


def load_learned(path: str = None) -> dict:
    path = path or LEARNED_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if data.get("scope") != LEARNED_SCOPE:
        return {}
    return data.get("applied") or {}


def live_params(learned_path: str = None) -> dict:
    return tighten_only({**rs.DEFAULT_PARAMS, **LIVE_DEFAULTS}, load_learned(learned_path))


def explore_params(trade_params: dict) -> dict:
    """The wide net the shadow ledger uses: never tighter than the trade rules,
    and loose enough that the learner can see what the rules are leaving out."""
    return {**trade_params, "rsi_max": max(trade_params["rsi_max"], 35), "min_depth_pct": 0.0,
            "require_uptrend_200": False, "stabilization": "none", "market_filter": False}


# ── Data ──────────────────────────────────────────────────────────────────────
def _headers():
    return {"APCA-API-KEY-ID": os.getenv("APCA_API_KEY_ID", ""),
            "APCA-API-SECRET-KEY": os.getenv("APCA_API_SECRET_KEY", "")}


def fetch_bars_batch(symbols: list, start: str, chunk: int = 50) -> dict:
    """Split-adjusted daily bars, ascending, fully paginated. (Raw bars turn a
    split into a fake 50% gap and a false oversold signal.)"""
    out = {s: [] for s in symbols}
    for i in range(0, len(symbols), chunk):
        batch, token = symbols[i:i + chunk], None
        for _ in range(40):
            params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": start, "limit": 10000,
                      "adjustment": "split", "feed": "iex", "sort": "asc"}
            if token:
                params["page_token"] = token
            r = requests.get(f"{DATA_URL}/v2/stocks/bars", headers=_headers(), params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for sym, bars in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
    return out


def completed_bars(bars: list, now=None) -> list:
    """Drop today's bar until the session is over: a half-finished bar is not
    the close the backtest evaluated."""
    now = now or et_now()
    if bars and bars[-1]["t"][:10] == now.date().isoformat() and (now.hour, now.minute) < (16, 30):
        return bars[:-1]
    return bars


def _norm(b: dict) -> dict:
    return {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}


# ── Universe ──────────────────────────────────────────────────────────────────
def build_universe(cfg: dict, logs_dir: str = LOGS) -> dict:
    """{symbol: tier}. Only 'core' is ever paper-traded; the rest is learning data."""
    tiers = {}
    cache = os.path.join(logs_dir, "universe-cache.json")
    if os.path.exists(cache):
        try:
            with open(cache) as f:
                for s in json.load(f).get("symbols", []):
                    tiers[s] = "universe"
        except (OSError, ValueError):
            pass
    for s in cfg["probation"]:
        tiers[s] = "probation"
    for s in cfg["speculative"]:
        tiers[s] = "speculative"
    for s in cfg["core"]:
        if s not in cfg["speculative"]:
            tiers[s] = "core"
    for s in cfg["hedge"]:
        tiers.pop(s, None)           # inverse/volatility ETFs decay; not a rebound candidate
    return tiers


# ── Scan ──────────────────────────────────────────────────────────────────────
def market_context(spy_features, vix) -> dict:
    ctx = {"vix": vix, "spy_above_ma50": None, "spy_above_ma200": None}
    if spy_features:
        ctx["spy_above_ma50"] = spy_features["last_close"] > spy_features["ma50"]
        ctx["spy_above_ma200"] = spy_features["last_close"] > spy_features["ma200"]
    return ctx


def _score(f: dict) -> float:
    """Ordering only: deeper oversold and deeper below MA50 rank first."""
    return round((35 - f["rsi"]) + 0.5 * f["depth_below_ma50_pct"], 2)


def evaluate(sym: str, tier: str, bars: list, params: dict, market: dict, research: dict) -> dict | None:
    f = rs.features(bars)
    if f is None or f["last_close"] < MIN_PRICE_SHADOW:
        return None
    ok_wide, wide_flags = rs.setup(f, explore_params(params), market)
    if not ok_wide:
        return None
    would_trade, flags = rs.setup(f, params, market)

    blocked = None
    if tier != "core":
        blocked = f"{tier} name: logged for learning only"
    elif sym in set(params.get("blocked_symbols", [])):
        blocked = "blocked by the learner"
    elif not would_trade:
        blocked = "fails: " + ", ".join(k for k, v in flags.items() if not v)
    else:
        intel = (research or {}).get("intelligence") or {}
        if sym in {s.upper() for s in intel.get("earnings_watch_symbols") or []}:
            blocked = "earnings blackout (Hard Rule 9)"
        cap = (((research or {}).get("symbols") or {}).get(sym) or {}).get("fundamentals", {}).get("market_cap_b")
        if blocked is None and cap is not None and cap < 2.0:
            blocked = f"market cap ${cap:.1f}B below $2B"

    levels = rs.exit_levels(f["last_close"], f, params)
    price = f["last_close"]
    return {
        "symbol": sym, "tier": tier, "signal_date": bars[-1]["t"][:10], "price": round(price, 4),
        "rsi": f["rsi"], "rsi_prev": f["rsi_prev"], "depth_pct": round(f["depth_below_ma50_pct"], 2),
        "atr_pct": round(f["atr_pct"], 2), "atr": f["atr"], "ma20": f["ma20"],
        "above_ma200": f["last_close"] > f["ma200"],
        "stab_rsi_up": f["last_close"] > f["prev_close"] and f["rsi"] > f["rsi_prev"],
        "stab_prev_high": f["last_close"] > f["prev_high"],
        "would_trade": bool(would_trade), "flags": flags,
        "tradeable": blocked is None, "blocked_reason": blocked, "score": _score(f),
        "stop_pct": round((price - levels["stop"]) / price * 100, 2),
        "target_pct": (round((levels["target"] / price - 1) * 100, 2) if levels["target"] else None),
        "time_stop_sessions": levels["time_stop_sessions"],
        "ma50": round(f["ma50"], 4),
    }


def load_research(today_iso: str, logs_dir: str = LOGS) -> dict:
    path = os.path.join(logs_dir, f"{today_iso}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scan(today: date = None, bars_by_symbol: dict = None, logs_dir: str = LOGS,
         ledger_path: str = None, cfg: dict = None, params: dict = None) -> dict:
    today = today or et_today()
    cfg = cfg or universe_policy.load_config()
    if cfg is None:
        raise SystemExit("core_universe.json not found")
    params = params or live_params()
    tiers = build_universe(cfg, logs_dir)
    research = load_research(today.isoformat(), logs_dir)
    vix = ((research.get("intelligence") or {}).get("vix"))

    symbols = sorted(set(tiers) | {"SPY"})
    if bars_by_symbol is None:
        start = (today - timedelta(days=430)).isoformat()
        bars_by_symbol = fetch_bars_batch(symbols, start)
    bars_by_symbol = {s: completed_bars([_norm(b) for b in bars]) for s, bars in bars_by_symbol.items()}

    spy_f = rs.features(bars_by_symbol.get("SPY") or [])
    market = market_context(spy_f, vix)

    rows = [r for sym, tier in tiers.items()
            if (r := evaluate(sym, tier, bars_by_symbol.get(sym) or [], params, market, research))]
    rows.sort(key=lambda r: r["score"], reverse=True)
    signal_dates = sorted({r["signal_date"] for r in rows})
    latest = max((b[-1]["t"][:10] for b in bars_by_symbol.values() if b), default=None)

    out = {
        "agent_version": get_version(), "timestamp": et_now().isoformat(), "date": today.isoformat(),
        "latest_session": latest, "vix": vix, "market": market, "params": params,
        "universe_size": len(tiers), "setups_logged": len(rows),
        "candidates": [r for r in rows if r["tier"] == "core"],
        "shadow_only": [r["symbol"] for r in rows if r["tier"] != "core"],
        "signal_dates": signal_dates,
    }
    ledger_rows = [r for r in rows if r["signal_date"] == latest]
    upsert_ledger(ledger_rows, market, params, ledger_path)
    stats = load_ledger(ledger_path)
    out["setup_record"] = setup_record(stats)
    for c in out["candidates"]:
        c["suggestion"] = build_suggestion(c, params, out["setup_record"], market)
    write_outputs(out, logs_dir)
    return out


# ── Ledger ────────────────────────────────────────────────────────────────────
def _fmt(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return repr(round(v, 6))
    return "" if v is None else str(v)


def load_ledger(path: str = None) -> list:
    path = path or LEDGER_PATH
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def save_ledger(rows: list, path: str = None) -> None:
    path = path or LEDGER_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k)) if not isinstance(r.get(k), str) else r.get(k) for k in LEDGER_FIELDS})
    os.replace(tmp, path)


def upsert_ledger(candidates: list, market: dict, params: dict, path: str = None) -> None:
    """One row per (signal_date, symbol). Re-running the same day refreshes the
    descriptive fields but never touches outcome fields once they are filled."""
    rows = load_ledger(path)
    index = {(r["signal_date"], r["symbol"]): r for r in rows}
    exit_json = json.dumps(_exit_subset(params), sort_keys=True)
    for c in candidates:
        key = (c["signal_date"], c["symbol"])
        row = index.get(key)
        if row is None:
            row = {k: "" for k in LEDGER_FIELDS}
            rows.append(row)
            index[key] = row
        row.update({
            "signal_date": c["signal_date"], "symbol": c["symbol"], "tier": c["tier"], "price": _fmt(c["price"]),
            "rsi": _fmt(c["rsi"]), "rsi_prev": _fmt(c["rsi_prev"]), "depth_pct": _fmt(c["depth_pct"]),
            "atr_pct": _fmt(c["atr_pct"]), "atr": _fmt(c["atr"]), "ma20": _fmt(c["ma20"]),
            "above_ma200": _fmt(c["above_ma200"]), "stab_rsi_up": _fmt(c["stab_rsi_up"]),
            "stab_prev_high": _fmt(c["stab_prev_high"]),
            "spy_above_ma50": _fmt(market.get("spy_above_ma50")),
            "spy_above_ma200": _fmt(market.get("spy_above_ma200")), "vix": _fmt(market.get("vix")),
            "would_trade": _fmt(c["would_trade"]),
        })
        if not row.get("exit_params"):
            row["exit_params"] = exit_json
    save_ledger(rows, path)


def mark_paper_traded(symbol: str, signal_date: str, path: str = None) -> None:
    rows = load_ledger(path)
    for r in rows:
        if r["symbol"] == symbol and r["signal_date"] == signal_date:
            r["paper_traded"] = "1"
    save_ledger(rows, path)


def _f(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def resolve(today: date = None, bars_by_symbol: dict = None, path: str = None) -> dict:
    """Fill in sim outcome and forward returns for earlier ledger rows."""
    today = today or et_today()
    rows = load_ledger(path)
    pending = [r for r in rows if (not r["sim_reason"] or not r["r20"])
               and (today - date.fromisoformat(r["signal_date"])).days <= LEDGER_MAX_AGE_DAYS
               and r["signal_date"] < today.isoformat()]
    if not pending:
        return {"resolved": 0, "pending": 0}
    if bars_by_symbol is None:
        start = (min(date.fromisoformat(r["signal_date"]) for r in pending) - timedelta(days=3)).isoformat()
        bars_by_symbol = fetch_bars_batch(sorted({r["symbol"] for r in pending} | {"SPY"}), start)
    bars_by_symbol = {s: completed_bars([_norm(b) for b in bars]) for s, bars in bars_by_symbol.items()}

    def window(sym, after):
        return [b for b in bars_by_symbol.get(sym, []) if b["t"][:10] > after]

    filled = 0
    for r in pending:
        w, spy_w = window(r["symbol"], r["signal_date"]), window("SPY", r["signal_date"])
        if not w:
            continue
        fill = w[0]["o"] * 1.001
        for h in HORIZONS:
            if len(w) >= h and not r[f"r{h}"]:
                r[f"r{h}"] = _fmt(w[h - 1]["c"] / fill - 1)
            if len(spy_w) >= h and not r[f"spy_r{h}"]:
                r[f"spy_r{h}"] = _fmt(spy_w[h - 1]["c"] / (spy_w[0]["o"] * 1.001) - 1)
        if not r["sim_reason"]:
            try:
                params = json.loads(r["exit_params"])
            except ValueError:
                continue
            sim = rs.simulate_trade(w, {"atr": _f(r["atr"]), "ma20": _f(r["ma20"])}, params)
            if sim:
                r.update({"entry_fill": _fmt(sim["entry_fill"]), "sim_ret": _fmt(sim["ret"]),
                          "sim_reason": sim["reason"], "sim_exit_offset": str(sim["exit_offset"]),
                          "sim_mae": _fmt(sim["mae"])})
                filled += 1
    save_ledger(rows, path)
    return {"resolved": filled, "pending": len([r for r in rows if not r["sim_reason"]])}


# ── Track record and suggestions ──────────────────────────────────────────────
def setup_record(rows: list) -> dict:
    """How the rules that actually trade have done, from every resolved ledger
    row that met them (traded or not)."""
    done = [r for r in rows if r.get("would_trade") == "1" and r.get("sim_ret") not in ("", None)]
    rets = [float(r["sim_ret"]) for r in done]
    excess = [float(r["r10"]) - float(r["spy_r10"]) for r in done if r.get("r10") and r.get("spy_r10")]
    n = len(rets)
    rec = {"n": n, "win_rate": (sum(x > 0 for x in rets) / n) if n else None,
           "median_ret": statistics.median(rets) if n else None,
           "mean_ret": statistics.mean(rets) if n else None,
           "n_excess": len(excess), "mean_excess_10d": statistics.mean(excess) if excess else None}
    rec["proven"] = bool(n >= PROVEN_MIN_N and rec["win_rate"] >= PROVEN_MIN_WIN and rec["median_ret"] > 0
                         and rec["mean_excess_10d"] is not None and rec["mean_excess_10d"] > PROVEN_MIN_EXCESS)
    return rec


def build_suggestion(c: dict, params: dict, record: dict, market: dict) -> dict:
    """What Alex would need to decide whether to take this one with real money.
    Information only: the agent never places a real-money order from it."""
    price, stop_pct = c["price"], c["stop_pct"]
    paper_pct = params["position_pct"]
    proven = bool(record.get("proven"))
    real_pct = paper_pct if proven else round(paper_pct / 2, 2)
    return {
        "symbol": c["symbol"], "signal_close": price,
        "entry": f"limit at or just under the ask (signal close ${price:.2f})",
        "stop": round(price * (1 - stop_pct / 100), 2), "stop_pct": stop_pct,
        "target": round(price * (1 + c["target_pct"] / 100), 2) if c.get("target_pct") else None,
        "time_stop_sessions": c["time_stop_sessions"],
        "paper_size_pct": paper_pct,
        "suggested_real_size_pct": real_pct,
        "risk_per_10k": round(10000 * real_pct / 100 * stop_pct / 100, 2),
        "status": "PROVEN" if proven else "UNPROVEN",
        "record": record,
        "market": market,
        "why": (f"RSI {c['rsi']:.0f}, {c['depth_pct']:.1f}% below its 50-day average, "
                f"{'stabilizing' if c['stab_rsi_up'] else 'not yet stabilizing'}, "
                f"{'above' if c['above_ma200'] else 'below'} its 200-day average"),
    }


def _pct(x):
    return "n/a" if x is None else f"{x * 100:+.1f}%"


def render_markdown(out: dict) -> str:
    m, rec = out["market"], out["setup_record"]
    L = [f"# Oversold-rebound scan — {out['date']}", "",
         f"Latest completed session: {out['latest_session']} | VIX {out['vix']} | SPY above 50-day: "
         f"{m['spy_above_ma50']} | above 200-day: {m['spy_above_ma200']}",
         f"Scanned {out['universe_size']} names, {out['setups_logged']} met the wide net; "
         f"{len(out['shadow_only'])} are learning-only (not core).", "",
         f"**Track record of the trading rules so far:** {rec['n']} resolved, win rate "
         f"{_pct(rec['win_rate'])}, median {_pct(rec['median_ret'])} per trade, mean excess vs SPY over 10 "
         f"sessions {_pct(rec['mean_excess_10d'])} -> **{'PROVEN' if rec['proven'] else 'UNPROVEN'}** "
         f"(needs {PROVEN_MIN_N}+ resolved, {PROVEN_MIN_WIN:.0%}+ wins, positive median and excess).",
         "", f"_{BACKTEST_NOTE}_", ""]
    tradeable = [c for c in out["candidates"] if c["tradeable"]]
    L.append("## Paper trades (core names that pass every rule)")
    if tradeable:
        for c in tradeable:
            s = c["suggestion"]
            L += [f"### {c['symbol']} — {s['status']}",
                  f"- Why: {s['why']}",
                  f"- Entry: {s['entry']}; stop ${s['stop']} (-{s['stop_pct']}%)"
                  + (f"; target ${s['target']}" if s["target"] else "") + f"; exit after {s['time_stop_sessions']} sessions",
                  f"- Paper size: {s['paper_size_pct']}% of equity.",
                  f"- **If you trade it with real money:** {s['suggested_real_size_pct']}% of the portfolio at most "
                  f"(about ${s['risk_per_10k']} at risk per $10,000 if the stop is hit). "
                  f"Real orders are yours to place; the agent will not place one.", ""]
    else:
        L += ["- None today.", ""]
    near = [c for c in out["candidates"] if not c["tradeable"]]
    if near:
        L.append("## Core names oversold but blocked")
        L += [f"- {c['symbol']}: RSI {c['rsi']:.0f}, {c['depth_pct']:.1f}% below MA50 — {c['blocked_reason']}" for c in near]
    return "\n".join(L) + "\n"


def write_outputs(out: dict, logs_dir: str = LOGS) -> None:
    os.makedirs(logs_dir, exist_ok=True)
    base = os.path.join(logs_dir, f"{out['date']}-rebound")
    with open(base + ".json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    with open(base + ".md", "w") as f:
        f.write(render_markdown(out))


# ── Alerts (called by the executor once an order is actually placed) ──────────
def alert_entries(placed: list, scan_out: dict) -> bool:
    import notify
    entries = [p for p in placed if p.get("action") == "BUY"]
    if not entries:
        return False
    by_sym = {c["symbol"]: c for c in scan_out.get("candidates", [])}
    lines = ["PAPER trade(s) placed by the oversold-rebound path. Real money is your call.", BACKTEST_NOTE]
    for p in entries:
        s = (by_sym.get(p["symbol"]) or {}).get("suggestion") or {}
        lines.append(f"\n{p['symbol']}: paper buy {p['qty']} @ limit ${p['limit']} -- {s.get('status', 'UNPROVEN')}")
        if s:
            lines.append(f"  {s['why']}")
            lines.append(f"  stop ${s['stop']} (-{s['stop_pct']}%)"
                         + (f", target ${s['target']}" if s.get("target") else "")
                         + f", out after {s['time_stop_sessions']} sessions")
            lines.append(f"  If real money: {s['suggested_real_size_pct']}% of portfolio max "
                         f"(~${s['risk_per_10k']} risk per $10k)")
    return notify.alert(f"Rebound paper trade: {', '.join(p['symbol'] for p in entries)}",
                        "\n".join(lines), priority="high", tags="chart_with_upwards_trend")


def alert_exits(exited: list) -> bool:
    import notify
    if not exited:
        return False
    lines = [f"{e['symbol']}: {e['pnl_pct']:+.1f}% ({e['reason']}), held {e['hold_sessions']} sessions"
             for e in exited]
    return notify.alert(f"Rebound paper exit: {', '.join(e['symbol'] for e in exited)}",
                        "\n".join(lines), tags="checkered_flag")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if action == "scan":
        o = scan()
        print(json.dumps({k: o[k] for k in ("date", "latest_session", "universe_size", "setups_logged")}, indent=2))
        print(render_markdown(o), file=sys.stderr)
    elif action == "resolve":
        print(json.dumps(resolve(), indent=2))
    else:
        print(__doc__)
        sys.exit(2)
