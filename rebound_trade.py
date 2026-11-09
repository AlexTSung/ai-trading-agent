#!/usr/bin/env python3
"""
rebound_trade.py — PAPER-ONLY executor for the oversold-rebound path.

Reads today's candidates from rebound.py (logs/paper/YYYY-MM-DD-rebound.json),
places Alpaca *paper* limit buys, attaches its own stop/target exits once a fill
is confirmed, and closes anything held past its time stop. It never touches
Robinhood and refuses to run unless the broker URL is the paper endpoint.

Why it is separate from trade.py. The trend executor re-validates resting buys
whose id starts "agent-buy-" and cancels them when MA20 < MA50 -- which is the
normal state of an oversold rebound -- and it attaches its own tiered exits to
any held position with no sell order. So every order here carries an "rb-"
prefix, and trade.ensure_exits_for_positions() skips symbols this module
manages. reviewer.py / journal.py filter the same prefix, so these fills never
enter the trend-following round-trip record that drives the Judge's learned
gates, the per-symbol stats, or the graduation decision.

Usage:
  python3 rebound_trade.py run        # one executor tick (idempotent; safe hourly)
  python3 rebound_trade.py status     # open / pending rebound positions
"""

import csv
import json
import os
import sys
from datetime import date, datetime, timedelta

import rebound
import trade
from market_time import NYSE_HOLIDAYS, et_now, et_today, et_today_iso
from version import get_version

ROOT   = os.path.dirname(os.path.abspath(__file__))
LOGS   = os.path.join(ROOT, "logs", "paper")
STATE_PATH  = os.path.join(LOGS, "rebound-state.json")
TRADES_CSV  = os.path.join(LOGS, "rebound-trades.csv")

BUY_PREFIX, OCO_PREFIX, STOP_PREFIX, SELL_PREFIX = "rb-buy-", "rb-oco-", "rb-stop-", "rb-sell-"
ORDER_PREFIX = "rb-"

# Mirrors judge.VIX_NO_NEW_POSITIONS (CLAUDE.md Volatility Position Sizing): a
# Hard Rule, so this path may not be looser than the trend path.
VIX_NO_NEW_POSITIONS = 35.0
EXIT_SLIPPAGE = 0.003

TRADES_HEADER = ["symbol", "signal_date", "entry_date", "entry", "exit_date", "exit",
                 "qty", "pnl_pct", "pnl_usd", "reason", "hold_sessions"]


# ── Safety ────────────────────────────────────────────────────────────────────
def assert_paper():
    if "paper" not in (trade.BASE_URL or ""):
        raise RuntimeError(f"rebound_trade is paper-only; broker URL is {trade.BASE_URL!r}")


# ── State ─────────────────────────────────────────────────────────────────────
def empty_state() -> dict:
    return {"pending": {}, "open": {}}


def load_state(path: str = None) -> dict:
    path = path or STATE_PATH
    if not os.path.exists(path):
        return empty_state()
    with open(path) as f:
        state = json.load(f)
    state.setdefault("pending", {})
    state.setdefault("open", {})
    return state


def save_state(state: dict, path: str = None) -> None:
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def managed_symbols(path: str = None) -> set:
    """Symbols this module owns the exits for. trade.py must leave them alone."""
    try:
        state = load_state(path)
    except (OSError, ValueError):
        return set()
    return set(state["pending"]) | set(state["open"])


def sessions_between(start: date, end: date) -> int:
    """NYSE sessions after `start` up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in NYSE_HOLIDAYS:
            n += 1
    return n


# ── Planning (pure) ───────────────────────────────────────────────────────────
def choose_entries(candidates: list, state: dict, held: set, open_buy_symbols: set,
                   params: dict, vix) -> tuple:
    """Pick which tradeable candidates to enter now. Returns (chosen, skipped)."""
    skipped = []
    if isinstance(vix, (int, float)) and vix > VIX_NO_NEW_POSITIONS:
        return [], [{"symbol": c["symbol"], "reason": f"VIX {vix:.1f} > {VIX_NO_NEW_POSITIONS:.0f}"}
                    for c in candidates]

    slots = params["max_open"] - len(state["open"]) - len(state["pending"])
    chosen = []
    for c in sorted(candidates, key=lambda c: c.get("score", 0), reverse=True):
        sym = c["symbol"]
        reason = None
        if not c.get("tradeable"):
            reason = c.get("blocked_reason") or "not tradeable"
        elif sym in state["open"] or sym in state["pending"]:
            reason = "already managed by the rebound path"
        elif sym in held or sym in open_buy_symbols:
            reason = "already held or has an open order"
        elif sym in set(params.get("blocked_symbols", [])):
            reason = "blocked by learning"
        elif len(chosen) >= slots:
            reason = f"no free slot (max_open {params['max_open']})"
        if reason:
            skipped.append({"symbol": sym, "reason": reason})
        else:
            chosen.append(c)
    return chosen, skipped


# ── Broker actions ────────────────────────────────────────────────────────────
def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def place_entry(c: dict, account: dict, positions: list, params: dict) -> dict:
    sym = c["symbol"]
    ask = trade.get_latest_ask(sym)
    if ask is None:
        return {"symbol": sym, "action": "SKIPPED", "reason": "no usable ask"}
    if ask < trade.MIN_TRADE_PRICE:
        return {"symbol": sym, "action": "SKIPPED", "reason": f"price floor: ask ${ask:.2f}"}
    max_up = params.get("max_gap_up_pct")
    if max_up is not None and c.get("price") and ask > c["price"] * (1 + max_up / 100):
        return {"symbol": sym, "action": "SKIPPED",
                "reason": f"gapped up: ask ${ask:.2f} is >{max_up}% above signal close ${c['price']:.2f}"}

    limit = round(ask, 2)          # never above the ask: Hard Rule 2
    qty, note = trade.calculate_max_shares(sym, account, limit, params["position_pct"] / 100, positions)
    if qty == 0:
        return {"symbol": sym, "action": "SKIPPED", "reason": note}

    client_id = f"{BUY_PREFIX}{_stamp()}-{sym}"
    order = trade._post_order({
        "symbol": sym, "qty": str(qty), "side": "buy", "type": "limit",
        "time_in_force": "day", "limit_price": str(limit), "client_order_id": client_id,
    })
    if not trade.order_succeeded(order):
        return {"symbol": sym, "action": "BUY_REJECTED", "order": order}
    return {"symbol": sym, "action": "BUY", "qty": qty, "limit": limit, "order_id": order.get("id"),
            "client_order_id": client_id, "note": note}


def attach_exits(sym: str, pos_meta: dict, qty: int) -> dict:
    """OCO stop+target when there is a target, otherwise a lone stop."""
    entry = pos_meta["entry_price"]
    stop = round(entry * (1 - pos_meta["stop_pct"] / 100), 2)
    target = (round(entry * (1 + pos_meta["target_pct"] / 100), 2)
              if pos_meta.get("target_pct") else None)
    if stop >= entry or (target is not None and target <= entry):
        return {"symbol": sym, "_ok": False,
                "error": f"degenerate exit math at ${entry:.4f}: stop ${stop}, target {target}"}
    if target is not None:
        body = {"symbol": sym, "qty": str(qty), "side": "sell", "type": "limit",
                "time_in_force": "gtc", "order_class": "oco",
                "take_profit": {"limit_price": str(target)}, "stop_loss": {"stop_price": str(stop)},
                "client_order_id": f"{OCO_PREFIX}{_stamp()}-{sym}"}
    else:
        body = {"symbol": sym, "qty": str(qty), "side": "sell", "type": "stop",
                "time_in_force": "gtc", "stop_price": str(stop),
                "client_order_id": f"{STOP_PREFIX}{_stamp()}-{sym}"}
    result = trade._post_order(body)
    result["_stop"], result["_target"] = stop, target
    return result


def close_for_time_stop(sym: str) -> dict:
    pos = trade.get_position(sym)
    if not pos:
        return {"symbol": sym, "action": "NO_POSITION"}
    qty = int(float(pos["qty"]))
    price = round(float(pos["current_price"]) * (1 - EXIT_SLIPPAGE), 2)
    released = trade.cancel_sell_orders_for(sym)
    order = trade._post_order({
        "symbol": sym, "qty": str(qty), "side": "sell", "type": "limit", "time_in_force": "gtc",
        "limit_price": str(price), "client_order_id": f"{SELL_PREFIX}{_stamp()}-{sym}"})
    return {"symbol": sym, "action": "TIME_STOP_SELL", "qty": qty, "limit": price,
            "released": released, "ok": trade.order_succeeded(order)}


# ── Reconcile ─────────────────────────────────────────────────────────────────
def _exit_reason(order: dict) -> str:
    cid = str(order.get("client_order_id") or "")
    if cid.startswith(SELL_PREFIX):
        return "TIME_STOP"
    if str(order.get("type") or "").startswith("stop"):
        return "STOP"
    if cid.startswith((OCO_PREFIX, STOP_PREFIX)) or order.get("order_class") in ("oco", "bracket"):
        return "TARGET" if order.get("type") == "limit" else "STOP"
    return "OTHER"


def find_exit(sym: str, since: str, closed_orders: list):
    for o in sorted(closed_orders, key=lambda o: o.get("filled_at") or ""):
        if (o.get("symbol") == sym and o.get("side") == "sell"
                and o.get("status") in ("filled", "partially_filled")
                and (o.get("filled_at") or "") >= since):
            return o
    return None


def reconcile(state: dict, positions: list, open_orders: list, closed_orders: list,
              today: date) -> dict:
    """Promote filled entries, drop dead ones, record exits. Mutates state."""
    held = {p["symbol"]: p for p in positions
            if p.get("asset_class") != "us_option" and float(p.get("qty") or 0) > 0}
    open_buy_ids = {o.get("id") for o in open_orders if o.get("side") == "buy"}
    out = {"filled": [], "expired": [], "exited": []}

    for sym, p in list(state["pending"].items()):
        pos = held.get(sym)
        if pos:
            state["open"][sym] = {
                "signal_date": p["signal_date"], "entry_date": today.isoformat(),
                "entry_price": float(pos["avg_entry_price"]), "qty": int(float(pos["qty"])),
                "stop_pct": p["stop_pct"], "target_pct": p.get("target_pct"),
                "time_stop_sessions": p["time_stop_sessions"], "exits_attached": False,
                "placed_at": p.get("placed_at"),
            }
            del state["pending"][sym]
            out["filled"].append(sym)
        elif p.get("order_id") not in open_buy_ids:
            del state["pending"][sym]
            out["expired"].append(sym)

    for sym, p in list(state["open"].items()):
        if sym in held:
            p["qty"] = int(float(held[sym]["qty"]))
            continue
        ex = find_exit(sym, p["entry_date"], closed_orders)
        if ex is None:
            continue                      # position gone but no fill visible yet
        exit_price = float(ex["filled_avg_price"])
        qty = int(float(ex.get("filled_qty") or p["qty"]))
        exit_date = (ex.get("filled_at") or today.isoformat())[:10]
        row = {
            "symbol": sym, "signal_date": p["signal_date"], "entry_date": p["entry_date"],
            "entry": round(p["entry_price"], 4), "exit_date": exit_date, "exit": round(exit_price, 4),
            "qty": qty, "pnl_pct": round((exit_price / p["entry_price"] - 1) * 100, 2),
            "pnl_usd": round((exit_price - p["entry_price"]) * qty, 2),
            "reason": _exit_reason(ex),
            "hold_sessions": sessions_between(date.fromisoformat(p["entry_date"]),
                                              date.fromisoformat(exit_date)),
        }
        out["exited"].append(row)
        del state["open"][sym]
    return out


def append_trades(rows: list, path: str = None) -> None:
    if not rows:
        return
    path = path or TRADES_CSV
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADES_HEADER)
        if new:
            w.writeheader()
        w.writerows(rows)


# ── Tick ──────────────────────────────────────────────────────────────────────
def load_candidates(today_iso: str) -> dict:
    path = os.path.join(LOGS, f"{today_iso}-rebound.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def run(today: date = None) -> dict:
    assert_paper()
    today = today or et_today()
    today_iso = today.isoformat()
    scan = load_candidates(today_iso)
    params = scan.get("params") or {}
    if not params:
        return {"status": "NO_SCAN", "agent_version": get_version(), "note":
                "no rebound scan for today -- nothing to do"}

    clock = trade.get_market_status()
    account, positions = trade.get_account(), trade.get_positions()
    open_orders = trade.get_orders(status="open")
    closed_orders = trade.get_orders(status="closed", after_date=(today - timedelta(days=30)).isoformat())
    state = load_state()
    result = {"agent_version": get_version(), "timestamp": et_now().isoformat(),
              "market_open": bool(clock.get("is_open")), "entries": [], "skipped": [],
              "exits_attached": [], "time_stops": []}

    rec = reconcile(state, positions, open_orders, closed_orders, today)
    result["reconciled"] = rec
    append_trades(rec["exited"])
    if rec["exited"]:
        rebound.alert_exits(rec["exited"])
    held_syms = {p["symbol"] for p in positions}
    sell_covered = {o["symbol"] for o in open_orders if o.get("side") == "sell"}

    if clock.get("is_open"):
        for sym, p in state["open"].items():
            if sym in held_syms and sym not in sell_covered and not p["exits_attached"]:
                r = attach_exits(sym, p, p["qty"])
                ok = trade.order_succeeded(r)
                p["exits_attached"] = ok
                result["exits_attached"].append({"symbol": sym, "ok": ok,
                                                 "stop": r.get("_stop"), "target": r.get("_target")})
            held_for = sessions_between(date.fromisoformat(p["entry_date"]), today)
            if sym in held_syms and held_for >= p["time_stop_sessions"]:
                result["time_stops"].append(close_for_time_stop(sym))

        open_buy_syms = {o["symbol"] for o in open_orders if o.get("side") == "buy"}
        chosen, skipped = choose_entries(scan.get("candidates", []), state, held_syms,
                                         open_buy_syms, params, scan.get("vix"))
        result["skipped"] = skipped
        for c in chosen:
            placed = place_entry(c, account, positions, params)
            result["entries"].append(placed)
            if placed.get("action") == "BUY":
                rebound.mark_paper_traded(c["symbol"], c["signal_date"])
                state["pending"][c["symbol"]] = {
                    "signal_date": c["signal_date"], "order_id": placed["order_id"],
                    "placed_at": et_now().isoformat(), "qty": placed["qty"],
                    "stop_pct": c["stop_pct"], "target_pct": c.get("target_pct"),
                    "time_stop_sessions": c["time_stop_sessions"]}
                account = trade.get_account()      # cash moved; size the next one honestly

    if any(e.get("action") == "BUY" for e in result["entries"]):
        rebound.alert_entries(result["entries"], scan)
    save_state(state)
    return result


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    if action == "run":
        print(json.dumps(run(), indent=2))
    elif action == "status":
        print(json.dumps(load_state(), indent=2))
    else:
        print(__doc__)
        sys.exit(2)
