#!/usr/bin/env python3
"""
options_trade.py — Options order execution via Alpaca paper account.

Handles:
  - Finding actual option contracts matching strategy legs
  - Placing multi-leg option orders
  - Position monitoring (Greeks, P&L)
  - Rolling and closing positions near expiry

Paper trading ONLY. ALPACA_PAPER_TRADE=true must be set.
Never place real money orders from this module.
"""

import os
import sys
import json
import math
import requests
from datetime import datetime, date, timedelta
from typing import Optional

BASE_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets"

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
PAPER_FLAG    = os.getenv("ALPACA_PAPER_TRADE", "true").lower()

# Safety: never run against live account
assert PAPER_FLAG == "true", "FATAL: ALPACA_PAPER_TRADE must be 'true'"


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


def _log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


# ── Account ───────────────────────────────────────────────────────────────────

def get_account() -> dict:
    r = requests.get(f"{BASE_URL}/v2/account", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def get_equity() -> float:
    """
    Account equity with phantom positions marked back to cost basis.

    Every options risk control divides by this number — the 2% max-loss cap in
    passes_risk_filters and the 3.6% monthly drawdown halt in run_options.py.
    Alpaca reports equity of ~$559K against a true ~$91K because LBGJ carries
    roughly $470K from a reverse split applied to the quote but never to the
    position. Sizing off the reported figure makes a "2% cap" $11,180 on a book
    actually worth $91K — 6x too large — and lifts the drawdown halt out of reach.

    Falls back to the reported figure if positions cannot be read, so a data
    outage can never silently enlarge the risk budget.
    """
    acct = get_account()
    reported = float(acct.get("equity", 0) or 0)
    try:
        from trade import detect_phantom_equity
        r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers(), timeout=15)
        r.raise_for_status()
        excess = sum(f.get("phantom_amount", 0.0)
                     for f in detect_phantom_equity(r.json()))
    except Exception:
        return reported
    return max(0.0, reported - excess)


# ── Contract lookup ───────────────────────────────────────────────────────────

def find_contract(symbol: str, expiry: str, strike: float,
                  option_type: str) -> Optional[dict]:
    """
    Find an option contract on Alpaca by underlying, expiry, strike, and type.

    Args:
        symbol: Underlying (e.g. "AAPL")
        expiry: YYYY-MM-DD
        strike: Strike price
        option_type: "call" or "put"

    Returns:
        Contract dict or None if not found / not tradeable.
    """
    params = {
        "underlying_symbol": symbol,
        "expiration_date":   expiry,
        "type":              option_type,
        "strike_price_gte":  strike - 1,
        "strike_price_lte":  strike + 1,
        "status":            "active",
        "limit":             10,
    }
    r = requests.get(f"{BASE_URL}/v2/options/contracts",
                     headers=_headers(), params=params, timeout=10)
    if r.status_code != 200:
        return None

    contracts = r.json().get("option_contracts", [])
    if not contracts:
        return None

    # Pick closest strike
    contracts.sort(key=lambda c: abs(float(c.get("strike_price", 0)) - strike))
    return contracts[0]


def find_strategy_contracts(strat: dict) -> list[dict]:
    """
    Resolve all legs of a strategy to actual Alpaca contracts.

    Returns list of (leg_info, contract) dicts, or [] if any leg fails.
    """
    symbol = strat["symbol"]
    expiry = strat["expiry"]
    resolved = []

    for leg in strat["legs"]:
        leg_type = leg["type"]  # e.g. "short_put", "long_call"
        option_type = "call" if "call" in leg_type else "put"
        strike = leg["strike"]

        contract = find_contract(symbol, expiry, strike, option_type)
        if not contract:
            _log(f"  Contract not found: {symbol} {expiry} {option_type} {strike}")
            return []

        resolved.append({
            "leg_type":    leg_type,
            "option_type": option_type,
            "strike":      strike,
            "side":        "sell" if "short" in leg_type else "buy",
            "contract":    contract,
            "symbol":      contract["symbol"],
            "theo_price":  leg["price"],
        })

    return resolved


# ── Order placement ───────────────────────────────────────────────────────────

def place_option_order(contract_symbol: str, qty: int, side: str,
                       order_type: str = "limit", limit_price: float = None,
                       time_in_force: str = "day") -> dict:
    """
    Place a single-leg option order.

    For multi-leg strategies, Alpaca supports multi-leg orders via order_class.
    This places individual legs (simpler, more compatible with paper account).

    Args:
        contract_symbol: Option contract symbol (e.g. "AAPL250117C00150000")
        qty: Number of contracts (positive)
        side: "buy" or "sell"
        order_type: "limit" or "market"
        limit_price: Required for limit orders
        time_in_force: "day" (default)
    """
    payload = {
        "symbol":        contract_symbol,
        "qty":           str(qty),
        "side":          side,
        "type":          order_type,
        "time_in_force": time_in_force,
    }
    if order_type == "limit" and limit_price is not None:
        payload["limit_price"] = str(round(limit_price, 2))

    r = requests.post(f"{BASE_URL}/v2/orders",
                      headers=_headers(), json=payload, timeout=10)
    if r.status_code not in (200, 201):
        _log(f"  Order FAILED [{r.status_code}]: {r.text[:200]}")
        return {"error": r.text, "status_code": r.status_code}

    return r.json()


def execute_strategy(strat: dict, contracts: list[dict], qty: int = 1,
                     dry_run: bool = True) -> list[dict]:
    """
    Execute all legs of a strategy.

    Args:
        strat: Strategy dict from options_strategy.py
        contracts: Resolved contracts from find_strategy_contracts()
        qty: Number of contracts per leg
        dry_run: If True, log but don't actually place orders

    Returns:
        List of order results
    """
    results = []
    strategy_name = strat.get("strategy", "unknown")
    _log(f"Executing {strategy_name} on {strat['symbol']} x{qty} contracts "
         f"({'DRY RUN' if dry_run else 'LIVE PAPER'})")

    for leg in contracts:
        side           = leg["side"]
        contract_sym   = leg["symbol"]
        theo_price     = leg["theo_price"]

        # For credit strategies: use limit slightly inside mid to improve fill
        # For debit strategies: use limit slightly above mid
        if side == "sell":
            limit_price = round(theo_price * 0.95, 2)  # slight discount to fill
        else:
            limit_price = round(theo_price * 1.05, 2)  # slight premium to fill

        limit_price = max(limit_price, 0.01)

        _log(f"  {side.upper()} {qty}x {contract_sym} @ ${limit_price:.2f} "
             f"(theo: ${theo_price:.2f})")

        if dry_run:
            results.append({
                "dry_run": True,
                "contract": contract_sym,
                "side":     side,
                "qty":      qty,
                "price":    limit_price,
            })
        else:
            order = place_option_order(contract_sym, qty, side, "limit", limit_price)
            results.append(order)
            _log(f"    Order ID: {order.get('id', 'ERROR')}")

    return results


# ── Position monitoring ───────────────────────────────────────────────────────

def get_option_positions() -> list[dict]:
    """Get all current option positions from Alpaca."""
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers(), timeout=10)
    if r.status_code != 200:
        return []
    positions = r.json()
    return [p for p in positions if p.get("asset_class") == "us_option"]


def get_option_orders(status: str = "open") -> list[dict]:
    """Get open or all option orders."""
    params = {"status": status, "asset_class": "us_option", "limit": 100}
    r = requests.get(f"{BASE_URL}/v2/orders", headers=_headers(),
                     params=params, timeout=10)
    if r.status_code != 200:
        return []
    return r.json()


def close_option_position(contract_symbol: str, qty: int = None) -> dict:
    """
    Close an option position (buy back short, sell long).
    If qty is None, closes the full position.
    """
    # Get current position
    r = requests.get(f"{BASE_URL}/v2/positions/{contract_symbol}",
                     headers=_headers(), timeout=10)
    if r.status_code != 200:
        return {"error": f"Position not found: {contract_symbol}"}

    pos = r.json()
    pos_qty  = abs(int(pos.get("qty", 0)))
    pos_side = pos.get("side", "")  # "long" or "short"

    close_qty  = qty if qty else pos_qty
    close_side = "sell" if pos_side == "long" else "buy"

    return place_option_order(contract_symbol, close_qty, close_side, "market")


# ── DTE management ────────────────────────────────────────────────────────────

def check_positions_for_management() -> list[dict]:
    """
    Scan open option positions and flag those needing management:
      - DTE ≤ 21: roll or close (gamma risk spikes)
      - P&L ≥ 50% of max profit: take profit early
      - P&L ≤ -100% of max profit: stop loss (2x max credit lost)

    Natenberg Ch 6: gamma explodes near expiry for ATM options.
    Hull §18.2: high gamma = rapidly changing delta = difficult to hedge.
    """
    positions = get_option_positions()
    flags = []

    for pos in positions:
        sym = pos.get("symbol", "")
        # Parse expiry from option symbol (e.g. AAPL250117C00150000)
        # Format: UNDERLYING + YYMMDD + C/P + strike*1000 (8 digits)
        try:
            # Find the date portion
            underlying = ""
            for i, c in enumerate(sym):
                if c.isdigit():
                    date_str = sym[i:i+6]
                    exp_date = date(2000 + int(date_str[:2]),
                                    int(date_str[2:4]),
                                    int(date_str[4:6]))
                    break
            else:
                exp_date = None

            dte = (exp_date - date.today()).days if exp_date else 999

            unrealized_plpc = float(pos.get("unrealized_plpc", 0)) * 100  # as %
            market_value    = float(pos.get("market_value", 0))

            action = None
            reason = None

            if dte <= 21:
                action = "close"
                reason = f"DTE={dte} ≤ 21 (gamma risk)"
            elif unrealized_plpc >= 50:
                action = "close"
                reason = f"P&L={unrealized_plpc:.0f}% ≥ 50% target"
            elif unrealized_plpc <= -100:
                action = "close"
                reason = f"P&L={unrealized_plpc:.0f}% ≤ -100% stop loss"

            if action:
                flags.append({
                    "symbol":     sym,
                    "dte":        dte,
                    "pl_pct":     round(unrealized_plpc, 1),
                    "action":     action,
                    "reason":     reason,
                    "market_val": round(market_value, 2),
                })

        except Exception as e:
            _log(f"  Could not parse position {sym}: {e}")

    return flags


# ── Journal ───────────────────────────────────────────────────────────────────

def fetch_leg_quotes(contracts: list[dict]) -> Optional[list[dict]]:
    """
    Live bid/ask for each resolved leg, in leg order, for execution costing.

    options_screener.check_option_liquidity existed but was never called from
    any pipeline, so no option order had ever been costed or liquidity-checked
    against a real quote. Every price the gates saw was a Black-Scholes mid.

    Returns None if ANY leg's quote is unusable — a partial set would be
    silently blended into an "estimate" that looks measured. See
    options_strategy.execution_cost, which requires all-or-nothing.
    """
    from options_screener import check_option_liquidity

    quotes = []
    for leg in contracts:
        sym = leg.get("symbol")
        if not sym:
            return None
        try:
            q = check_option_liquidity(sym)
        except Exception as e:
            _log(f"  Quote fetch failed for {sym}: {e}")
            return None
        bid, ask = q.get("bid", 0), q.get("ask", 0)
        if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
                and bid > 0 and ask > bid):
            _log(f"  No usable quote for {sym} (bid={bid}, ask={ask})")
            return None
        quotes.append({"symbol": sym, "bid": float(bid), "ask": float(ask)})

    return quotes


def open_short_vol_positions(journal_dir: str = None) -> list[dict]:
    """
    Short-premium strategies still open, for the aggregate concentration cap.

    Alpaca reports option positions per contract, not per strategy, so a four-
    leg iron condor appears as four unrelated rows with no shared max loss. The
    strategy-level risk only exists in our own journal. This joins the two: a
    journalled strategy is still open if any of its leg contracts is still held.

    Returns [{symbol, strategy, max_loss_per_contract, qty}]. Entries whose max
    loss was never journalled are returned with max_loss_per_contract=None —
    aggregate_short_vol_check refuses rather than treating them as zero risk.
    """
    from options_strategy import SHORT_PREMIUM_STRATEGIES

    journal_dir = journal_dir or os.path.join(os.path.dirname(__file__),
                                              "logs", "options")
    held = {p.get("symbol") for p in get_option_positions() if p.get("symbol")}
    if not held:
        return []

    # This month and last — a 21-60 DTE entry cannot still be open beyond that.
    today = date.today()
    months = {today.strftime("%Y-%m"),
              (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")}

    open_positions = []
    seen = set()
    for month in sorted(months, reverse=True):
        path = os.path.join(journal_dir, f"trades-{month}.jsonl")
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("strategy") not in SHORT_PREMIUM_STRATEGIES:
                    continue

                leg_syms = {
                    o.get("symbol") for o in (entry.get("orders") or [])
                    if isinstance(o, dict) and o.get("symbol")
                }
                if not (leg_syms & held):
                    continue

                key = (entry.get("symbol"), entry.get("expiry"), entry.get("strategy"))
                if key in seen:
                    continue
                seen.add(key)

                max_loss = entry.get("max_loss_after_costs")
                if not isinstance(max_loss, (int, float)):
                    max_loss = entry.get("max_loss")

                open_positions.append({
                    "symbol":   entry.get("symbol"),
                    "strategy": entry.get("strategy"),
                    "expiry":   entry.get("expiry"),
                    "max_loss_per_contract": (
                        round(max_loss * 100, 2)
                        if isinstance(max_loss, (int, float)) else None
                    ),
                    "qty": entry.get("qty", 1),
                })

    return open_positions


def log_options_trade(strat: dict, orders: list[dict], journal_path: str,
                      costs: dict = None, scenarios: dict = None,
                      qty: int = 1):
    """
    Append trade details to the options journal.

    costs/scenarios come from options_strategy.net_of_cost_metrics and
    scenario_analysis. They are recorded because the entry-time record is the
    only place the execution cost and loss-tail shape can be compared against
    the realised round trip later — reviewer.py cannot reconstruct either from
    fills alone.
    """
    import os
    os.makedirs(os.path.dirname(journal_path), exist_ok=True)

    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "strategy":  strat.get("strategy"),
        "symbol":    strat.get("symbol"),
        "iv_rank":   strat.get("iv_rank"),
        "expiry":    strat.get("expiry"),
        "dte":       strat.get("dte"),
        "net_credit": strat.get("net_credit") or -strat.get("net_debit", 0),
        "max_loss":  strat.get("max_loss"),
        "qty":       qty,
        "legs":      strat.get("legs"),
        "orders":    orders,
        "greeks": {
            "delta": strat.get("delta"),
            "gamma": strat.get("gamma"),
            "theta": strat.get("theta"),
            "vega":  strat.get("vega"),
        }
    }

    if costs:
        entry.update({
            "exec_cost_per_contract": costs.get("exec_cost_per_contract"),
            "exec_cost_source":       costs.get("exec_cost_source"),
            "cost_pct_of_credit":     costs.get("cost_pct_of_credit"),
            "max_loss_after_costs":   costs.get("max_loss_after_costs"),
            "reward_risk_net":        costs.get("reward_risk_net"),
            "widest_leg_spread_pct":  costs.get("widest_leg_spread_pct"),
        })

    if scenarios:
        entry.update({
            "avg_shortfall_per_contract": scenarios.get("avg_shortfall_per_contract"),
            "max_shortfall_per_contract": scenarios.get("max_shortfall_per_contract"),
            "losing_scenario_fraction":   scenarios.get("losing_scenario_fraction"),
        })

    with open(journal_path, "a") as f:
        f.write(json.dumps(entry) + "\n")

    _log(f"  Journaled: {strat['strategy']} on {strat['symbol']}")


if __name__ == "__main__":
    print("Account:", get_account().get("status"))
    equity = get_equity()
    print(f"Equity: ${equity:,.2f}")

    print("\nOption positions:", len(get_option_positions()))
    flags = check_positions_for_management()
    if flags:
        print("Positions needing management:")
        for f in flags:
            print(f"  {f['symbol']} — {f['reason']}")
    else:
        print("No positions require management.")
