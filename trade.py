#!/usr/bin/env python3
"""
trade.py — Order execution with built-in guardrails.
Usage:
  python scripts/trade.py status                          # Market clock
  python scripts/trade.py session                         # Full trading session (reads today's research)
  python scripts/trade.py order AAPL 5 buy 195.50        # Place a specific limit order
  python scripts/trade.py close NVDA                     # Close entire position in a symbol
  python scripts/trade.py cancel                         # Cancel all open orders
  python scripts/trade.py orders                         # List today's orders
  python scripts/trade.py check-stops                    # Check all positions for stop-loss triggers
"""

import os
import sys
import json
import math
import requests
from datetime import datetime, date

# ── Auth ─────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL      = "https://data.alpaca.markets"

CASH_RESERVE_PCT      = 0.20   # Always keep 20% in cash
MAX_POSITION_PCT      = 0.05   # Max 5% per position (standard)
DIP_BUY_POSITION_PCT  = 0.08   # Max 8% for high-conviction dip buys
STOP_LOSS_PCT         = -0.08  # -8% stop distance
LIMIT_SLIPPAGE    = 0.002  # Orders within 0.2% of ask
TAKE_PROFIT_R1    = 2.0    # T1 = entry + 2 × stop_distance (2:1 R/R, sell 50%)
TAKE_PROFIT_R2    = 3.0    # T2 = entry + 3 × stop_distance (3:1 R/R, sell remaining)

def _headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }

def _check_auth():
    if not ALPACA_KEY or not ALPACA_SECRET:
        print(json.dumps({"error": "Missing APCA_API_KEY_ID or APCA_API_SECRET_KEY env vars"}))
        sys.exit(1)

# ── Market & Portfolio State ──────────────────────────────────────────────────
def get_market_status():
    r = requests.get(f"{BASE_URL}/v2/clock", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_account():
    r = requests.get(f"{BASE_URL}/v2/account", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_positions():
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers())
    r.raise_for_status()
    return r.json()

def get_position(symbol):
    r = requests.get(f"{BASE_URL}/v2/positions/{symbol}", headers=_headers())
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def get_latest_ask(symbol):
    """Get the current ask price from the data API."""
    url = f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest"
    r = requests.get(url, headers=_headers())
    r.raise_for_status()
    data = r.json()
    return float(data["quote"]["ap"])  # ask price

def get_orders(status="all", after_date=None):
    params = {"status": status, "limit": 100}
    if after_date:
        params["after"] = after_date
    r = requests.get(f"{BASE_URL}/v2/orders", headers=_headers(), params=params)
    r.raise_for_status()
    return r.json()

# ── Guardrails ────────────────────────────────────────────────────────────────
def calculate_max_shares(symbol, account, limit_price, is_dip_buy=False):
    """
    How many shares can we buy given cash reserve and position size limits.
    Dip buys use 8% max sizing instead of standard 5%.
    """
    portfolio_value = float(account["portfolio_value"])
    current_cash    = float(account["cash"])
    min_cash        = portfolio_value * CASH_RESERVE_PCT
    deployable_cash = current_cash - min_cash
    pos_pct         = DIP_BUY_POSITION_PCT if is_dip_buy else MAX_POSITION_PCT
    max_by_pct      = portfolio_value * pos_pct

    budget = min(deployable_cash, max_by_pct)
    if budget <= 0:
        return 0, f"Insufficient deployable cash: ${deployable_cash:.2f} (reserve: ${min_cash:.2f})"

    shares = math.floor(budget / limit_price)
    size_label = "DIP_BUY 8%" if is_dip_buy else "5%"
    return shares, f"Budget ({size_label}): ${budget:.2f} → {shares} shares @ ${limit_price:.2f}"

def check_stop_loss(positions):
    """Return any positions that have hit the -8% stop-loss threshold."""
    triggers = []
    for pos in positions:
        pl_pct = float(pos.get("unrealized_plpc", 0))
        if pl_pct <= STOP_LOSS_PCT:
            triggers.append({
                "symbol": pos["symbol"],
                "qty": pos["qty"],
                "avg_entry": pos["avg_entry_price"],
                "current_price": pos["current_price"],
                "unrealized_plpc": pl_pct,
            })
    return triggers

# ── Order Execution ───────────────────────────────────────────────────────────
def place_limit_order(symbol, qty, side, limit_price, reason=""):
    """
    Place a limit order. All guardrails must be satisfied before calling this.
    Returns the order response JSON.
    """
    if qty <= 0:
        return {"error": f"qty must be > 0, got {qty}"}

    order_data = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,          # "buy" or "sell"
        "type": "limit",
        "time_in_force": "day",
        "limit_price": str(round(limit_price, 2)),
        "client_order_id": f"agent-{side}-{symbol}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }

    r = requests.post(f"{BASE_URL}/v2/orders", headers=_headers(), json=order_data)
    result = r.json()
    result["_agent_reason"] = reason
    result["_limit_price_used"] = limit_price
    return result

def close_position(symbol, reason=""):
    """Close an entire position using a limit order at current bid."""
    pos = get_position(symbol)
    if not pos:
        return {"error": f"No open position in {symbol}"}

    qty           = int(float(pos["qty"]))
    current_price = float(pos["current_price"])
    # Sell slightly below current for a better fill chance
    limit_price   = round(current_price * (1 - LIMIT_SLIPPAGE), 2)

    return place_limit_order(symbol, qty, "sell", limit_price, reason=reason)

def buy_with_guardrails(symbol, signal_strength, account, research_limit_price=None, is_dip_buy=False):
    """
    Full buy flow:
    1. Check market is open
    2. Set limit price at MA20 × 0.995 pullback target (from research), or ask if already there
    3. Calculate size
    4. Place order
    Returns dict with order result + audit trail.
    """
    clock = get_market_status()
    if not clock.get("is_open"):
        return {"error": "Market is closed. No order placed.", "symbol": symbol}

    ask = get_latest_ask(symbol)

    if is_dip_buy:
        # Dip buy: stock already down 25%+ — buy at current ask, don't wait for pullback
        limit_price = round(ask * (1 + LIMIT_SLIPPAGE), 2)
        price_note  = f"DIP ask ${ask:.2f}"
    elif research_limit_price and research_limit_price > 0:
        # Use MA20 pullback target from research — patient entry, avoids chasing
        # If ask is already below the target (deep pullback), use ask instead
        limit_price = round(min(ask, research_limit_price), 2)
        price_note  = f"MA20-target ${research_limit_price:.2f}"
    else:
        # Fallback: ask with small discount for patience
        limit_price = round(ask * (1 - LIMIT_SLIPPAGE), 2)
        price_note  = f"ask ${ask:.2f}"

    qty, sizing_note = calculate_max_shares(symbol, account, limit_price, is_dip_buy=is_dip_buy)
    if qty == 0:
        return {"error": sizing_note, "symbol": symbol, "action": "SKIPPED"}

    reason = f"Signal: {signal_strength} | {price_note} | Limit: ${limit_price:.2f} | {sizing_note}"
    order  = place_limit_order(symbol, qty, "buy", limit_price, reason=reason)

    # Immediately place OCO exit structure: T1+stop and T2+stop on 50/50 split.
    # Uses limit_price as entry reference — actual fill will be ≤ limit, so stop
    # is slightly more conservative (closer to fill), not looser.
    exits = place_oco_exits(symbol, qty, limit_price)

    return {"symbol": symbol, "action": "BUY", "qty": qty, "limit_price": limit_price,
            "order": order, "exits": exits, "note": reason}

def place_oco_exits(symbol, qty, entry_price):
    """
    Place two OCO (One-Cancels-Other) exit orders covering the full position.
    Split into T1 (first 50%) and T2 (remaining 50%) halves — each paired with a stop.
    When T1 limit fills, its stop-leg auto-cancels; T2 OCO continues protecting the rest.
    When the stop triggers on either half, the corresponding limit-leg auto-cancels.
    """
    stop_price = round(entry_price * (1 + STOP_LOSS_PCT), 2)
    stop_dist  = entry_price - stop_price
    t1_price   = round(entry_price + TAKE_PROFIT_R1 * stop_dist, 2)
    t2_price   = round(entry_price + TAKE_PROFIT_R2 * stop_dist, 2)

    half  = qty // 2
    rest  = qty - half  # handles odd lots: e.g. qty=7 → half=3, rest=4

    today   = date.today().isoformat()
    results = []

    for label, share_qty, target in [("t1", half, t1_price), ("t2", rest, t2_price)]:
        order_data = {
            "symbol":          symbol,
            "qty":             str(share_qty),
            "side":            "sell",
            "type":            "limit",
            "time_in_force":   "gtc",
            "order_class":     "oco",
            "take_profit":     {"limit_price": str(target)},
            "stop_loss":       {"stop_price":  str(stop_price)},
            "client_order_id": f"{label}-oco-{today}-{symbol}",
        }
        r = requests.post(f"{BASE_URL}/v2/orders", headers=_headers(), json=order_data)
        result = r.json()
        result["_label"]       = label
        result["_target_price"] = target
        result["_stop_price"]   = stop_price
        results.append(result)

    return results


def cancel_orphan_exits(positions, open_orders):
    """
    Cancel GTC sell orders for symbols where we hold no position AND have no
    pending buy — these are exit orders left over from day-limit buys that didn't fill.
    """
    held_symbols    = {p["symbol"] for p in positions}
    pending_buy_sym = {o["symbol"] for o in open_orders if o["side"] == "buy"}
    covered         = held_symbols | pending_buy_sym

    cancelled = []
    for order in open_orders:
        if order["side"] == "sell" and order["symbol"] not in covered:
            r = requests.delete(f"{BASE_URL}/v2/orders/{order['id']}", headers=_headers())
            cancelled.append({
                "order_id":        order["id"],
                "symbol":          order["symbol"],
                "client_order_id": order.get("client_order_id"),
                "status_code":     r.status_code,
            })

    return cancelled


def cancel_all_orders():
    r = requests.delete(f"{BASE_URL}/v2/orders", headers=_headers())
    return {"status_code": r.status_code, "result": "All open orders cancelled"}

# ── Trading Session ───────────────────────────────────────────────────────────
def load_judge_output(today: str) -> dict:
    """Load today's Judge scores. Returns empty dict if not found."""
    judge_path = os.path.join(os.path.dirname(__file__), "logs", "paper", f"{today}-judge.json")
    if os.path.exists(judge_path):
        with open(judge_path) as f:
            return json.load(f)
    return {}

def run_trading_session():
    """
    Called by the 10:00 AM routine.
    Reads today's research AND judge output.
    Only executes trades approved by the Judge with score >= 7.0.
    """
    today        = date.today().isoformat()
    logs_dir     = os.path.join(os.path.dirname(__file__), "logs", "paper")
    research_path = os.path.join(logs_dir, f"{today}.json")

    # Load research
    research = {}
    if os.path.exists(research_path):
        with open(research_path) as f:
            research = json.load(f)
    else:
        import subprocess
        raw = subprocess.check_output(["python", os.path.join(os.path.dirname(__file__), "research.py"), "full"])
        research = json.loads(raw)
        os.makedirs(logs_dir, exist_ok=True)
        with open(research_path, "w") as f:
            json.dump(research, f, indent=2)

    if not research.get("market_open"):
        return {"status": "MARKET_CLOSED", "no_trades": True, "timestamp": datetime.now().isoformat()}

    # Load Judge output — REQUIRED before execution
    judge = load_judge_output(today)
    judge_scores = judge.get("scores", {})
    judge_approved = set(judge.get("approved", []))

    if not judge and not judge_scores:
        return {
            "status": "JUDGE_NOT_RUN",
            "error": "No Judge output found for today. Run judge.py session first.",
            "no_trades": True,
            "timestamp": datetime.now().isoformat()
        }

    account   = get_account()
    positions = get_positions()
    results   = {
        "timestamp": datetime.now().isoformat(),
        "trades": [],
        "skipped": [],
        "vetoed_by_judge": [],
        "stop_losses": [],
        "orphan_cancels": [],
    }

    # 0. Cancel OCO exit orders left over from day-limit buys that expired unfilled
    open_orders = get_orders(status="open")
    orphan_cancels = cancel_orphan_exits(positions, open_orders)
    if orphan_cancels:
        results["orphan_cancels"] = orphan_cancels

    # 1. Stop-losses always run first — not subject to Judge
    stop_triggers = check_stop_loss(positions)
    for trigger in stop_triggers:
        sym = trigger["symbol"]
        close_result = close_position(sym, reason=f"STOP-LOSS: Position down {trigger['unrealized_plpc']*100:.1f}%")
        results["stop_losses"].append({"symbol": sym, "result": close_result, "trigger": trigger})

    # 2. Execute only Judge-approved buys and avg-downs
    symbols_data = research.get("symbols", {})
    open_syms    = {p["symbol"] for p in positions}

    AVG_DOWN_MIN_SCORE = 8.0  # Stricter gate for adding to existing positions

    for sym, data in symbols_data.items():
        if "error" in data:
            results["skipped"].append({"symbol": sym, "reason": data["error"]})
            continue

        tech   = data.get("technicals", {})
        signal = tech.get("signal", "HOLD")
        # MA20 pullback target from research — prefer patient entry
        research_limit = tech.get("limit_price")

        # Sell signals bypass Judge — risk management always wins
        if signal in ("STRONG_SELL", "WEAK_SELL") and sym in open_syms:
            close_result = close_position(sym, reason=f"Sell signal: {signal}")
            results["trades"].append({"symbol": sym, "action": "SELL", "result": close_result})
            continue

        judge_result    = judge_scores.get(sym, {})
        judge_rec       = judge_result.get("recommendation", "HOLD")
        judge_score     = judge_result.get("score", 0)
        is_avg_down     = judge_result.get("avg_down_candidate", False)
        is_dip_approved = judge_result.get("dip_buy_approved", False)
        is_dip          = judge_result.get("dip_buy_candidate", False) or data.get("dip_buy_candidate", False)

        if is_dip and is_dip_approved and sym not in open_syms:
            # Dip buy — stock down 25%+, fundamentals intact, high conviction
            account = get_account()
            trade   = buy_with_guardrails(sym, f"DIP_BUY_{judge_result.get('dip_pct_drop', 0):.0f}pct",
                                          account, is_dip_buy=True)
            trade["judge_score"] = judge_score
            trade["action"] = "DIP_BUY"
            trade["dip_pct_drop"] = judge_result.get("dip_pct_drop", 0)
            results["trades"].append(trade)

        elif signal in ("STRONG_BUY", "WEAK_BUY") and sym not in open_syms:
            # New position — standard gate: score ≥ 7.0
            if judge_rec != "APPROVE" or judge_score < 7.0:
                results["vetoed_by_judge"].append({
                    "symbol": sym, "signal": signal,
                    "judge_recommendation": judge_rec, "judge_score": judge_score,
                    "rationale": judge_result.get("rationale", "No Judge data")
                })
                continue

            account = get_account()
            trade   = buy_with_guardrails(sym, signal, account, research_limit_price=research_limit)
            trade["judge_score"] = judge_score
            trade["action"] = "BUY"
            results["trades"].append(trade)

        elif is_avg_down and sym in open_syms:
            # Avg-down — stricter gate: score ≥ 8.0 and APPROVE
            if judge_rec != "APPROVE" or judge_score < AVG_DOWN_MIN_SCORE:
                results["vetoed_by_judge"].append({
                    "symbol": sym, "signal": signal,
                    "judge_recommendation": judge_rec, "judge_score": judge_score,
                    "rationale": f"Avg-down requires score ≥ {AVG_DOWN_MIN_SCORE}, got {judge_score}. "
                                 + judge_result.get("rationale", "")
                })
                continue

            account = get_account()
            trade   = buy_with_guardrails(sym, signal, account, research_limit_price=research_limit)
            trade["judge_score"] = judge_score
            trade["action"] = "AVG_DOWN"
            results["trades"].append(trade)

        else:
            results["skipped"].append({"symbol": sym, "signal": signal,
                                       "reason": "HOLD or no qualifying signal"})

    # Save session results
    session_path = os.path.join(logs_dir, f"{today}-session.json")
    os.makedirs(logs_dir, exist_ok=True)
    with open(session_path, "w") as f:
        json.dump(results, f, indent=2)

    return results

def check_stops():
    """Standalone stop-loss check — can be run any time."""
    positions = get_positions()
    triggers  = check_stop_loss(positions)
    results   = {"checked": len(positions), "triggers": triggers, "closed": []}

    for trigger in triggers:
        sym = trigger["symbol"]
        close_result = close_position(sym, reason=f"STOP-LOSS triggered at {trigger['unrealized_plpc']*100:.1f}%")
        results["closed"].append({"symbol": sym, "result": close_result})

    return results

# ── CLI Entry Point ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    _check_auth()
    action = sys.argv[1] if len(sys.argv) > 1 else "status"

    if action == "status":
        print(json.dumps(get_market_status(), indent=2))

    elif action == "session":
        print(json.dumps(run_trading_session(), indent=2))

    elif action == "check-stops":
        print(json.dumps(check_stops(), indent=2))

    elif action == "order":
        # python scripts/trade.py order AAPL 5 buy 195.50
        sym   = sys.argv[2]
        qty   = int(sys.argv[3])
        side  = sys.argv[4]
        price = float(sys.argv[5])
        print(json.dumps(place_limit_order(sym, qty, side, price, reason="Manual order"), indent=2))

    elif action == "close":
        sym = sys.argv[2]
        print(json.dumps(close_position(sym, reason="Manual close"), indent=2))

    elif action == "cancel":
        print(json.dumps(cancel_all_orders(), indent=2))

    elif action == "orders":
        today = date.today().isoformat()
        print(json.dumps(get_orders(status="all", after_date=today), indent=2))

    elif action == "account":
        print(json.dumps(get_account(), indent=2))

    else:
        print(json.dumps({"error": f"Unknown action: {action}"}))
