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

# ── Load .env ────────────────────────────────────────────────────────────────
# setup.sh writes a .env file, but nothing used to read it, so filling in keys
# there had no effect. Environment variables already set (GitHub Secrets, MCP
# config, an exporting shell) always win — load_dotenv does not override them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass  # python-dotenv is optional; env vars may be supplied by the shell

# ── Auth ─────────────────────────────────────────────────────────────────────
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = os.getenv("APCA_BASE_URL", "https://paper-api.alpaca.markets")
DATA_URL      = "https://data.alpaca.markets"

CASH_RESERVE_PCT      = 0.20   # Always keep 20% in cash
MAX_POSITION_PCT      = 0.05   # Fallback cap when no Judge score is available
DIP_BUY_POSITION_PCT  = 0.08   # Max 8% for high-conviction dip buys
LIMIT_SLIPPAGE    = 0.002  # Orders within 0.2% of ask
URGENT_EXIT_SLIPPAGE = 0.01  # Risk exits bid 1% through to ensure a fill
TAKE_PROFIT_R1    = 2.0    # T1 = entry + 2 × stop_distance (2:1 R/R)
TAKE_PROFIT_R2    = 3.0    # T2 = entry + 3 × stop_distance (3:1 R/R)

# ── Entry safety floors ───────────────────────────────────────────────────────
# A sub-$5 stock reached the executor on 2026-07-24 and 154,903 shares of a
# $0.03 ticker were bought. Enforce the documented floor at the order boundary,
# not only in the upstream screeners.
MIN_TRADE_PRICE = 5.00

# The same trade had a researched last_close of $1.24 against a live ask of
# $0.03 — a 97% gap that min(ask, target) silently accepted as a "pullback".
# Any disagreement this large means stale bars, a corporate action, or a bad
# quote. Refuse to trade rather than guess which price is real.
MAX_PRICE_DEVIATION = 0.10

# ── Tiered stop-losses (CLAUDE.md Hard Rule 3) ────────────────────────────────
# A single -8% stop was applied to every symbol regardless of tier, which forced
# blue-chip compounders out on ordinary volatility.
TIER1_SYMBOLS = {"AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"}
TIER2_SYMBOLS = {"DDOG", "TSLA", "QCOM"}
INVERSE_ETFS  = {"SQQQ", "SPXS", "SOXS"}

STOP_PCT_TIER1   = -0.20
STOP_PCT_TIER2   = -0.12
STOP_PCT_TIER3   = -0.10
STOP_PCT_INVERSE = -0.05

# ── Conviction-scaled sizing (CLAUDE.md Hard Rule 6) ──────────────────────────
SIZE_BY_SCORE = ((9.0, 0.08), (7.5, 0.05), (7.0, 0.03))

# ── Holding-period rules (CLAUDE.md Hard Rules 4, 5) ──────────────────────────
REENTRY_COOLDOWN_HOURS = 48    # No re-entry for 48h after a stop-out
MIN_HOLD_DAYS          = 30    # High-conviction positions get 30 days
MIN_HOLD_SCORE         = 8.0   # ...if the Judge scored them >= this
INVERSE_MAX_HOLD_DAYS  = 3     # Inverse ETFs decay — never hold long

# ── Partial profit-taking (CLAUDE.md Hard Rule 7) ─────────────────────────────
T1_SELL_FRACTION = 0.40        # Sell 40% at T1, 40% at T2, let 20% run

# ── Phantom equity / corporate action detection ───────────────────────────────
# On 2026-08-03 LBGJ was restated from $0.02 to $3.79 over a weekend while the
# share count (154,903) and cost basis ($0.0288) were untouched. That is a
# reverse split applied to the quote but not to the position, and it inflated
# reported portfolio value from $86,925 to $674,658 of equity that does not
# exist. A legitimate reverse split reduces shares and raises basis in the same
# ratio, leaving market value roughly unchanged.
PHANTOM_PRICE_RATIO = 10.0   # >=10x or <=1/10x vs cost basis is implausible
PHANTOM_MAX_PLPC    = 10.0   # unrealized P&L above +1000% is not a real move

LOGS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "paper")
STATE_PATH = os.path.join(LOGS_DIR, "agent-state.json")


def _headers():
    return {
        "APCA-API-KEY-ID": ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
        "Content-Type": "application/json",
    }


# ── Tier / sizing helpers ─────────────────────────────────────────────────────
def tier_for(symbol: str, is_speculative: bool = False) -> str:
    """
    Stop tier for a symbol. The options/forex work introduced an is_speculative
    flag carrying a flat -10% stop; that is exactly TIER3, so the flag maps onto
    the tier system rather than living beside it as a second mechanism.
    """
    if symbol in INVERSE_ETFS:
        return "INVERSE"
    if symbol in TIER1_SYMBOLS and not is_speculative:
        return "TIER1"
    if symbol in TIER2_SYMBOLS and not is_speculative:
        return "TIER2"
    return "TIER3"


def stop_pct_for(symbol: str) -> float:
    """Stop distance as a negative fraction, by stock tier."""
    return {
        "INVERSE": STOP_PCT_INVERSE,
        "TIER1":   STOP_PCT_TIER1,
        "TIER2":   STOP_PCT_TIER2,
        "TIER3":   STOP_PCT_TIER3,
    }[tier_for(symbol)]


def position_pct_for(judge_score) -> float:
    """Conviction-scaled position cap. Falls back to 5% without a score."""
    if judge_score is None:
        return MAX_POSITION_PCT
    for threshold, pct in SIZE_BY_SCORE:
        if judge_score >= threshold:
            return pct
    return MAX_POSITION_PCT


def load_watchlist_config() -> dict:
    """Return {symbol: entry} from watchlist.json, or {} if unreadable."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")
    try:
        with open(path) as f:
            return {i["symbol"]: i for i in json.load(f).get("watchlist", [])}
    except (OSError, ValueError, KeyError):
        return {}


def is_buyable(symbol: str, watchlist: dict) -> tuple:
    """
    Signal-only symbols (VIXY) and anything capped at 0% must never be bought.
    watchlist.json carried per-symbol max_allocation_pct values that no script
    read, so these caps were decorative until now.
    """
    entry = watchlist.get(symbol)
    if entry is None:
        return True, ""   # discovery tickers aren't on the watchlist
    if entry.get("type") == "signal_only":
        return False, f"{symbol} is signal_only — never a buy candidate"
    if entry.get("max_allocation_pct", 100) <= 0:
        return False, f"{symbol} has max_allocation_pct 0 — buying disabled"
    return True, ""


def allocation_cap_for(symbol: str, watchlist: dict, judge_score) -> float:
    """Tighter of the conviction cap and the per-symbol watchlist cap."""
    conviction = position_pct_for(judge_score)
    entry      = watchlist.get(symbol)
    if entry and entry.get("max_allocation_pct") is not None:
        return min(conviction, float(entry["max_allocation_pct"]) / 100.0)
    return conviction


# ── Persistent agent state (survives fresh CI checkouts) ──────────────────────
def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
        except ValueError:
            state = {}
    else:
        state = {}
    state.setdefault("stop_outs", {})   # symbol -> ISO timestamp of last stop-out
    state.setdefault("entries", {})     # symbol -> {date, judge_score}
    return state


def save_state(state: dict) -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def cooldown_remaining_hours(symbol: str, state: dict) -> float:
    """Hours left on the 48h post-stop-out re-entry ban. 0 if clear."""
    ts = state.get("stop_outs", {}).get(symbol)
    if not ts:
        return 0.0
    try:
        stopped_at = datetime.fromisoformat(ts)
    except ValueError:
        return 0.0
    elapsed = (datetime.now() - stopped_at).total_seconds() / 3600.0
    return max(0.0, REENTRY_COOLDOWN_HOURS - elapsed)


def min_hold_days_remaining(symbol: str, state: dict) -> float:
    """
    Days left on the minimum hold. Applies to positions the Judge scored
    >= MIN_HOLD_SCORE. Inverse ETFs get an inverted rule — a maximum hold.
    """
    entry = state.get("entries", {}).get(symbol)
    if not entry or entry.get("judge_score") is None:
        return 0.0
    if entry["judge_score"] < MIN_HOLD_SCORE:
        return 0.0
    try:
        entered = datetime.fromisoformat(entry["date"])
    except (ValueError, KeyError):
        return 0.0
    held = (datetime.now() - entered).days
    return max(0.0, MIN_HOLD_DAYS - held)


def inverse_hold_days(symbol: str, state: dict) -> float:
    """Days an inverse ETF has been held — they must be exited within 3."""
    if symbol not in INVERSE_ETFS:
        return 0.0
    entry = state.get("entries", {}).get(symbol)
    if not entry:
        return 0.0
    try:
        return (datetime.now() - datetime.fromisoformat(entry["date"])).days
    except (ValueError, KeyError):
        return 0.0

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
    """
    Current ask price. Returns None when the venue reports no usable ask —
    illiquid tickers quote 0, which previously flowed into a division and
    produced absurd share counts.
    """
    url = f"{DATA_URL}/v2/stocks/{symbol}/quotes/latest"
    r = requests.get(url, headers=_headers())
    r.raise_for_status()
    ask = float(r.json().get("quote", {}).get("ap") or 0)
    return ask if ask > 0 else None

def get_orders(status="all", after_date=None):
    params = {"status": status, "limit": 100}
    if after_date:
        params["after"] = after_date
    r = requests.get(f"{BASE_URL}/v2/orders", headers=_headers(), params=params)
    r.raise_for_status()
    return r.json()

# ── Guardrails ────────────────────────────────────────────────────────────────
def honest_portfolio_value(account, positions=None) -> float:
    """
    Portfolio value with phantom equity written back down to cost basis.

    Every portfolio-relative rule — conviction sizing, the 20% cash floor, the
    per-symbol allocation caps — divides by portfolio value. When an unadjusted
    corporate action inflates one position, all of those rules silently break in
    the same direction: sizes balloon and the cash percentage collapses. Marking
    suspect positions back to cost keeps the denominator honest.

    Falls back to the reported value if positions cannot be fetched, so a data
    outage never silently enlarges position sizes.
    """
    reported = float(account.get("portfolio_value", 0) or 0)
    if positions is None:
        try:
            positions = get_positions()
        except Exception:
            return reported

    excess = 0.0
    for flag in detect_phantom_equity(positions):
        excess += flag.get("phantom_amount", 0.0)

    return max(0.0, reported - excess)


def calculate_max_shares(symbol, account, limit_price, pos_pct, positions=None):
    """
    How many shares can we buy given the cash reserve and a position cap.
    pos_pct is the conviction-scaled fraction of portfolio value to deploy.
    """
    if limit_price <= 0:
        return 0, f"Invalid limit price ${limit_price} for {symbol}"

    # Size against honest portfolio value, not the broker's reported figure.
    # With LBGJ marked at $478,650 against a $4,461 basis, reported value was
    # $565,534 while the real figure was $91,345 — so every conviction cap came
    # out 6.2x too large (an 8% "cap" would have been $45,243 of a $91K book).
    # Phantom collateral was also being levered: buying_power read $1,286,084.
    portfolio_value = honest_portfolio_value(account, positions)
    min_cash        = portfolio_value * CASH_RESERVE_PCT

    # Use buying_power when it is the tighter constraint. Alpaca leaves `cash`
    # unchanged until a limit order settles, so sizing off `cash` alone let a
    # sequence of buys in one session commit past the 20% reserve.
    current_cash = float(account["cash"])
    available    = min(current_cash, float(account.get("buying_power", current_cash)))
    deployable_cash = available - min_cash
    max_by_pct      = portfolio_value * pos_pct

    budget = min(deployable_cash, max_by_pct)
    if budget <= 0:
        return 0, (f"Insufficient deployable cash: ${deployable_cash:.2f} "
                   f"(reserve floor: ${min_cash:.2f})")

    shares = math.floor(budget / limit_price)
    if shares == 0:
        return 0, (f"Budget ${budget:.2f} buys 0 whole shares at "
                   f"${limit_price:.2f}")
    return shares, (f"Budget ({pos_pct:.0%} cap): ${budget:.2f} → {shares} shares "
                    f"@ ${limit_price:.2f}")

def check_stop_loss(positions, state=None):
    """
    Return positions that have breached their tier's stop-loss threshold.
    Tier 1 compounders get 20%, Tier 2 growth 12%, Tier 3 10%, inverse ETFs 5%
    (CLAUDE.md Hard Rule 3) rather than a single -8% applied to everything.
    """
    state    = state if state is not None else load_state()
    triggers = []
    for pos in positions:
        sym      = pos["symbol"]
        pl_pct   = float(pos.get("unrealized_plpc", 0))
        stop_pct = stop_pct_for(sym)
        if pl_pct <= stop_pct:
            triggers.append({
                "symbol": sym,
                "qty": pos["qty"],
                "avg_entry": pos["avg_entry_price"],
                "current_price": pos["current_price"],
                "unrealized_plpc": pl_pct,
                "tier": tier_for(sym),
                "stop_pct": stop_pct,
                "reason": f"STOP-LOSS {tier_for(sym)} ({stop_pct:.0%}): "
                          f"down {pl_pct*100:.1f}%",
            })
    return triggers


def check_inverse_etf_max_hold(positions, state=None):
    """
    Inverse ETFs decay and must not be held past INVERSE_MAX_HOLD_DAYS
    (CLAUDE.md Bearish Playbook). Nothing enforced this before.
    """
    state = state if state is not None else load_state()
    stale = []
    for pos in positions:
        sym = pos["symbol"]
        if sym not in INVERSE_ETFS:
            continue
        held = inverse_hold_days(sym, state)
        if held >= INVERSE_MAX_HOLD_DAYS:
            stale.append({
                "symbol": sym, "days_held": held,
                "reason": f"INVERSE_MAX_HOLD: {sym} held {held}d "
                          f"(limit {INVERSE_MAX_HOLD_DAYS}d) — volatility decay",
            })
    return stale


def detect_phantom_equity(positions, state=None):
    """
    Flag positions whose price has moved implausibly far from its cost basis
    while the share count stayed put — the fingerprint of a corporate action
    (usually a reverse split) applied to the quote but not to the position.

    Such a position reports enormous unrealized gains that cannot be realized:
    the shares are not actually there at that price. Left unflagged it corrupts
    portfolio value, the journal, and the graduation win-rate statistics.

    Returns a list of flag dicts; empty when everything looks sane.
    """
    state = state if state is not None else load_state()
    flags = []

    for pos in positions:
        sym   = pos["symbol"]
        entry = float(pos.get("avg_entry_price") or 0)
        price = float(pos.get("current_price") or 0)
        qty   = float(pos.get("qty") or 0)
        plpc  = float(pos.get("unrealized_plpc") or 0)
        if entry <= 0 or price <= 0:
            continue

        ratio    = price / entry
        reasons  = []

        if ratio >= PHANTOM_PRICE_RATIO:
            reasons.append(f"price ${price:,.4f} is {ratio:,.0f}x its cost basis "
                           f"${entry:.4f}")
        elif ratio <= 1.0 / PHANTOM_PRICE_RATIO:
            reasons.append(f"price ${price:,.4f} is 1/{1/ratio:,.0f} of its cost "
                           f"basis ${entry:.4f}")

        if plpc >= PHANTOM_MAX_PLPC:
            reasons.append(f"unrealized P&L {plpc*100:,.0f}% exceeds the "
                           f"{PHANTOM_MAX_PLPC*100:,.0f}% plausibility ceiling")

        if not reasons:
            continue

        # Share count unchanged since entry turns suspicion into near-certainty:
        # a real split would have adjusted it.
        recorded = state.get("entries", {}).get(sym, {}).get("qty")
        if recorded is not None and float(recorded) == qty:
            reasons.append(f"share count unchanged at {qty:,.0f} since entry — a "
                           f"genuine split would have adjusted it")

        if entry < MIN_TRADE_PRICE:
            reasons.append(f"entry price ${entry:.4f} was below the "
                           f"${MIN_TRADE_PRICE:.2f} floor, where reverse splits "
                           f"are routine")

        flags.append({
            "symbol": sym,
            "qty": qty,
            "avg_entry_price": entry,
            "current_price": price,
            "price_ratio": round(ratio, 2),
            "unrealized_plpc": plpc,
            "reported_market_value": round(qty * price, 2),
            "cost_basis": round(qty * entry, 2),
            "phantom_amount": round(qty * price - qty * entry, 2),
            "reasons": reasons,
            "action": "TRADING_BLOCKED — verify the corporate action with the "
                      "broker before buying or selling this symbol",
        })

    return flags


def ensure_exits_for_positions(positions, open_orders, state=None):
    """
    Attach OCO exits to any held position that has no protective sell order.

    Buys place no exits at submission time (the day limit may never fill), so
    this runs each session and covers positions once they are actually held.
    """
    state       = state if state is not None else load_state()
    covered     = {o["symbol"] for o in open_orders if o["side"] == "sell"}
    # Never anchor exits to a price we do not trust.
    suspect     = {f["symbol"] for f in detect_phantom_equity(positions, state)}
    attached    = []

    for pos in positions:
        sym = pos["symbol"]
        if sym in covered or sym in suspect:
            continue
        qty = int(float(pos["qty"]))
        if qty <= 0:
            continue
        entry = float(pos["avg_entry_price"])
        result = place_oco_exits(sym, qty, entry)
        attached.append({"symbol": sym, "qty": qty, "entry": entry,
                         "exits": result})
    return attached

# ── Order Execution ───────────────────────────────────────────────────────────
def _post_order(order_data: dict) -> dict:
    """
    POST an order and make failures unmistakable.

    The previous code returned r.json() without inspecting the status, so a 422
    rejection body was stored in results["trades"] and rendered by the journal as
    an executed trade. Every caller now sees an explicit _ok flag.
    """
    r = requests.post(f"{BASE_URL}/v2/orders", headers=_headers(), json=order_data)
    try:
        body = r.json()
    except ValueError:
        body = {"raw_response": r.text[:500]}

    if not r.ok:
        return {
            "_ok": False,
            "error": f"Order rejected (HTTP {r.status_code})",
            "broker_message": body.get("message", body),
            "status_code": r.status_code,
            "submitted": order_data,
        }

    body["_ok"] = True
    return body


def order_succeeded(result: dict) -> bool:
    """True only for an order the broker actually accepted."""
    return bool(result) and result.get("_ok") is True and "error" not in result


def place_limit_order(symbol, qty, side, limit_price, reason="", tif="day"):
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
        "time_in_force": tif,
        "limit_price": str(round(limit_price, 2)),
        "client_order_id": f"agent-{side}-{symbol}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    }

    result = _post_order(order_data)
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

    # Risk exits need to actually fill. A 0.2% discount is often not enough in a
    # fast decline, and an unfilled day limit leaves the position exposed
    # overnight while the log says it was closed. Widen the discount for
    # stop-loss and max-hold exits, and mark them GTC so they survive the close.
    urgent      = any(k in reason.upper() for k in
                      ("STOP-LOSS", "STOP_LOSS", "MAX_HOLD", "INVERSE_MAX_HOLD"))
    slippage    = URGENT_EXIT_SLIPPAGE if urgent else LIMIT_SLIPPAGE
    limit_price = round(current_price * (1 - slippage), 2)

    if limit_price <= 0:
        return {"_ok": False,
                "error": f"Cannot price an exit for {symbol}: current_price "
                         f"${current_price}",
                "symbol": symbol}

    # Release shares held by existing sell orders before closing.
    #
    # OCO take-profit legs reserve the whole position, so a stop-loss or
    # signal-driven exit is rejected by the broker with "insufficient qty
    # available for order (requested: 25, available: 0)". That happened live on
    # 2026-08-21: NVDA hit STRONG_SELL with a confirmed MA20<MA50 thesis break,
    # the exit was correctly authorised, and the agent's own take-profit orders
    # blocked it. A position with exits attached could not be risk-managed at
    # all — the protective orders were preventing protection.
    released = cancel_sell_orders_for(symbol)

    result = place_limit_order(symbol, qty, "sell", limit_price,
                              reason=reason, tif="gtc" if urgent else "day")
    result["_urgent_exit"] = urgent
    result["_released_orders"] = released
    return result


def cancel_sell_orders_for(symbol: str) -> list:
    """Cancel every open sell order on a symbol so its shares become sellable."""
    cancelled = []
    try:
        open_orders = get_orders(status="open")
    except Exception as e:
        return [{"error": f"Could not list open orders: {e}"}]

    for order in open_orders:
        if order.get("symbol") != symbol or order.get("side") != "sell":
            continue
        try:
            r = requests.delete(f"{BASE_URL}/v2/orders/{order['id']}",
                                headers=_headers())
            cancelled.append({"order_id": order["id"],
                              "client_order_id": order.get("client_order_id"),
                              "status_code": r.status_code})
        except Exception as e:
            cancelled.append({"order_id": order.get("id"), "error": str(e)})
    return cancelled

def buy_with_guardrails(symbol, signal_strength, account, research_limit_price=None,
                        is_dip_buy=False, judge_score=None, research_last_close=None,
                        watchlist=None, size_multiplier=1.0, positions=None):
    """
    Full buy flow:
    1. Check market is open
    2. Verify the symbol is buyable and priced above the penny-stock floor
    3. Confirm the live ask agrees with the researched price
    4. Set a limit price, size by conviction, place the order
    Returns dict with order result + audit trail.
    """
    watchlist = watchlist if watchlist is not None else load_watchlist_config()

    clock = get_market_status()
    if not clock.get("is_open"):
        return {"error": "Market is closed. No order placed.", "symbol": symbol}

    buyable, why_not = is_buyable(symbol, watchlist)
    if not buyable:
        return {"error": why_not, "symbol": symbol, "action": "SKIPPED"}

    ask = get_latest_ask(symbol)
    if ask is None:
        return {"error": f"No usable ask price for {symbol} — refusing to size a trade "
                         f"against a missing quote.",
                "symbol": symbol, "action": "SKIPPED"}

    # Penny-stock floor at the order boundary.
    if ask < MIN_TRADE_PRICE:
        return {"error": f"PRICE_FLOOR: {symbol} ask ${ask:.4f} is below the "
                         f"${MIN_TRADE_PRICE:.2f} minimum.",
                "symbol": symbol, "action": "SKIPPED"}

    # The Judge scored a chart; make sure the executor is looking at the same stock.
    if research_last_close and research_last_close > 0:
        deviation = abs(ask - research_last_close) / research_last_close
        if deviation > MAX_PRICE_DEVIATION:
            return {"error": (
                        f"PRICE_MISMATCH: live ask ${ask:.4f} deviates "
                        f"{deviation:.1%} from researched close "
                        f"${research_last_close:.4f} (limit {MAX_PRICE_DEVIATION:.0%}). "
                        f"Likely stale bars or a corporate action — no order placed."),
                    "symbol": symbol, "action": "SKIPPED"}

    if is_dip_buy:
        # Never bid above the ask — that is a market order in disguise and
        # CLAUDE.md Hard Rule 2 forbids it. Take the ask at most.
        limit_price = round(ask, 2)
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

    if limit_price < MIN_TRADE_PRICE:
        return {"error": f"PRICE_FLOOR: computed limit ${limit_price:.4f} for {symbol} "
                         f"is below the ${MIN_TRADE_PRICE:.2f} minimum.",
                "symbol": symbol, "action": "SKIPPED"}

    pct = DIP_BUY_POSITION_PCT if is_dip_buy else allocation_cap_for(
        symbol, watchlist, judge_score)

    # Citadel volatility rule: halve size above VIX 25, no new positions above
    # VIX 35. The multiplier arrives from the Judge, which reads the real VIX
    # level carried through from intelligence.py.
    vix_note = ""
    if size_multiplier is not None and size_multiplier != 1.0:
        if size_multiplier <= 0:
            return {"error": "VOLATILITY BLOCK: VIX regime forbids new positions",
                    "symbol": symbol, "action": "SKIPPED"}
        pct *= size_multiplier
        vix_note = f" | VIX size x{size_multiplier:g}"

    qty, sizing_note = calculate_max_shares(symbol, account, limit_price, pct,
                                            positions)
    if qty == 0:
        return {"error": sizing_note, "symbol": symbol, "action": "SKIPPED"}

    reason = (f"Signal: {signal_strength} | {price_note} | Limit: ${limit_price:.2f} | "
              f"{sizing_note} | Tier {tier_for(symbol)} stop "
              f"{stop_pct_for(symbol):.0%}{vix_note}")
    order  = place_limit_order(symbol, qty, "buy", limit_price, reason=reason)

    if not order_succeeded(order):
        return {"symbol": symbol, "action": "BUY_REJECTED", "qty": qty,
                "limit_price": limit_price, "order": order, "note": reason}

    # Exits are deliberately NOT placed here. This is a day limit order that may
    # never fill; placing GTC sell legs against shares we do not own left naked
    # sell orders live overnight and risked a double-sell against the tiered stop.
    # ensure_exits_for_positions() attaches exits once a fill is confirmed.
    return {"symbol": symbol, "action": "BUY", "qty": qty, "limit_price": limit_price,
            "order": order, "exits": "deferred_until_fill", "note": reason}

def place_oco_exits(symbol, qty, entry_price):
    """
    Place two OCO (One-Cancels-Other) exit orders covering the full position.
    T1 takes 40% and T2 takes 40%, leaving 20% to run (CLAUDE.md Hard Rule 7).
    Stop distance is tiered by symbol rather than a flat -8%.
    """
    stop_pct   = stop_pct_for(symbol)
    stop_price = round(entry_price * (1 + stop_pct), 2)
    stop_dist  = round(entry_price - stop_price, 2)

    # Rounding to cents collapses the stop distance to zero on low-priced stocks:
    # a $0.03 entry gave stop=$0.03, stop_dist=0, and therefore T1 == T2 == entry,
    # i.e. a take-profit at the entry price and a stop that could never move.
    if stop_dist <= 0:
        return [{
            "_ok": False,
            "error": (
                f"Degenerate exit math for {symbol} at ${entry_price:.4f}: "
                f"tier {tier_for(symbol)} stop {stop_pct:.0%} rounds to a "
                f"${stop_dist:.2f} stop distance. No exits placed."
            ),
            "symbol": symbol,
        }]

    t1_price = round(entry_price + TAKE_PROFIT_R1 * stop_dist, 2)
    t2_price = round(entry_price + TAKE_PROFIT_R2 * stop_dist, 2)

    first = int(qty * T1_SELL_FRACTION)
    second = int(qty * T1_SELL_FRACTION)
    if first == 0 or second == 0:
        # Too few shares to split three ways — protect the whole lot at T1.
        first, second = qty, 0

    # Timestamped, not date-only: a date-based client_order_id collided whenever
    # exits were re-attached for the same symbol on the same day, and Alpaca
    # rejects duplicates.
    stamp   = datetime.now().strftime("%Y%m%d%H%M%S")
    results = []

    legs = [("t1", first, t1_price)]
    if second:
        legs.append(("t2", second, t2_price))

    for label, share_qty, target in legs:
        order_data = {
            "symbol":          symbol,
            "qty":             str(share_qty),
            "side":            "sell",
            "type":            "limit",
            "time_in_force":   "gtc",
            "order_class":     "oco",
            "take_profit":     {"limit_price": str(target)},
            "stop_loss":       {"stop_price":  str(stop_price)},
            "client_order_id": f"{label}-oco-{stamp}-{symbol}",
        }
        result = _post_order(order_data)
        result["_label"]        = label
        result["_target_price"] = target
        result["_stop_price"]   = stop_price
        result["_tier"]         = tier_for(symbol)
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
    state     = load_state()
    watchlist = load_watchlist_config()
    results   = {
        "timestamp": datetime.now().isoformat(),
        "trades": [],
        "skipped": [],
        "vetoed_by_judge": [],
        "stop_losses": [],
        "orphan_cancels": [],
        "exits_attached": [],
        "blocked_by_rules": [],
        "phantom_equity": [],
    }

    # Corporate-action sanity check runs before anything trades. A position whose
    # price was restated without adjusting shares reports equity that cannot be
    # realized; acting on it (selling into a phantom take-profit, or sizing new
    # buys off an inflated portfolio value) would book fictional results.
    phantom = detect_phantom_equity(positions, state)
    phantom_syms = {f["symbol"] for f in phantom}
    if phantom:
        results["phantom_equity"] = phantom

    # 0. Cancel OCO exit orders left over from day-limit buys that expired unfilled
    open_orders = get_orders(status="open")
    orphan_cancels = cancel_orphan_exits(positions, open_orders)
    if orphan_cancels:
        results["orphan_cancels"] = orphan_cancels

    # 1. Stop-losses always run first — not subject to Judge
    stop_triggers = check_stop_loss(positions, state)
    for trigger in stop_triggers:
        sym = trigger["symbol"]
        close_result = close_position(sym, reason=trigger["reason"])
        results["stop_losses"].append({"symbol": sym, "result": close_result,
                                       "trigger": trigger})
        # Record the stop-out so the 48h re-entry ban can be enforced, but only
        # if the broker accepted the closing order.
        if order_succeeded(close_result):
            state["stop_outs"][sym] = datetime.now().isoformat()
            state["entries"].pop(sym, None)

    # 1b. Inverse ETFs past their maximum hold
    for stale in check_inverse_etf_max_hold(positions, state):
        sym = stale["symbol"]
        close_result = close_position(sym, reason=stale["reason"])
        results["stop_losses"].append({"symbol": sym, "result": close_result,
                                       "trigger": stale})
        if order_succeeded(close_result):
            state["entries"].pop(sym, None)

    # 1c. Attach exits to any held position that is currently unprotected
    positions   = get_positions()
    open_orders = get_orders(status="open")
    attached    = ensure_exits_for_positions(positions, open_orders, state)
    if attached:
        results["exits_attached"] = attached

    # 2. Execute only Judge-approved buys and avg-downs
    symbols_data = research.get("symbols", {})
    open_syms    = {p["symbol"] for p in positions}

    AVG_DOWN_MIN_SCORE = 8.0  # Stricter gate for adding to existing positions

    for sym, data in symbols_data.items():
        if "error" in data:
            results["skipped"].append({"symbol": sym, "reason": data["error"]})
            continue

        if sym in phantom_syms:
            results["blocked_by_rules"].append({
                "symbol": sym, "action": "TRADING_BLOCKED",
                "reason": "Phantom equity / unverified corporate action — "
                          "no orders until the position is reconciled.",
            })
            continue

        tech   = data.get("technicals", {})
        signal = tech.get("signal", "HOLD")
        # MA20 pullback target from research — prefer patient entry
        research_limit = tech.get("limit_price")
        research_close = tech.get("last_close")

        # Sell signals bypass the Judge — risk management always wins — except
        # for Tier 1 compounders, where CLAUDE.md Hard Rule 3 requires a
        # confirmed MA20<MA50 cross before exiting. Selling blue chips on
        # short-term price noise was a primary Phase 1 loss driver.
        if signal in ("STRONG_SELL", "WEAK_SELL") and sym in open_syms:
            tier         = tier_for(sym)
            ma20         = tech.get("ma20", 0)
            ma50         = tech.get("ma50", 0)
            thesis_break = bool(ma20 and ma50 and ma20 < ma50)

            if tier == "TIER1" and not thesis_break:
                results["blocked_by_rules"].append({
                    "symbol": sym, "action": "SELL_BLOCKED", "signal": signal,
                    "reason": (f"Tier 1 compounder: {signal} on price alone is not a "
                               f"sell trigger. MA20 ${ma20:.2f} still >= MA50 "
                               f"${ma50:.2f} — no confirmed thesis break."),
                })
                continue

            hold_left = min_hold_days_remaining(sym, state)
            if hold_left > 0 and not thesis_break:
                results["blocked_by_rules"].append({
                    "symbol": sym, "action": "SELL_BLOCKED", "signal": signal,
                    "reason": (f"Minimum hold: {hold_left:.0f} of {MIN_HOLD_DAYS} days "
                               f"remaining on a high-conviction position and no "
                               f"thesis break."),
                })
                continue

            close_result = close_position(sym, reason=f"Sell signal: {signal}")
            results["trades"].append({"symbol": sym, "action": "SELL",
                                      "result": close_result})
            if order_succeeded(close_result):
                state["entries"].pop(sym, None)
            continue

        judge_result    = judge_scores.get(sym, {})
        judge_rec       = judge_result.get("recommendation", "HOLD")
        judge_score     = judge_result.get("score", 0)
        is_avg_down     = judge_result.get("avg_down_candidate", False)
        is_dip_approved = judge_result.get("dip_buy_approved", False)
        is_dip          = judge_result.get("dip_buy_candidate", False) or data.get("dip_buy_candidate", False)
        size_mult       = judge_result.get("size_multiplier", 1.0)

        # 48-hour re-entry cooling-off after a stop-out (CLAUDE.md Hard Rule 4).
        # Applies to every buy path below, including dip buys and avg-downs.
        wants_to_buy = (
            (is_dip and is_dip_approved and sym not in open_syms)
            or (signal in ("STRONG_BUY", "WEAK_BUY") and sym not in open_syms)
            or (is_avg_down and sym in open_syms)
        )
        if wants_to_buy:
            cooling = cooldown_remaining_hours(sym, state)
            if cooling > 0:
                results["blocked_by_rules"].append({
                    "symbol": sym, "action": "BUY_BLOCKED", "signal": signal,
                    "reason": (f"48h cooling-off: stopped out "
                               f"{REENTRY_COOLDOWN_HOURS - cooling:.1f}h ago, "
                               f"{cooling:.1f}h remaining before re-entry."),
                })
                continue

        def _record_entry(trade_result):
            """
            Track entry date, score and share count. The quantity is what lets
            detect_phantom_equity() tell a real split (shares adjusted) from a
            quote-only restatement (shares unchanged).
            """
            if order_succeeded(trade_result.get("order", {})):
                state["entries"][sym] = {
                    "date": datetime.now().isoformat(),
                    "judge_score": judge_score,
                    "qty": trade_result.get("qty"),
                    "entry_limit": trade_result.get("limit_price"),
                }

        if is_dip and is_dip_approved and sym not in open_syms:
            # Dip buy — stock down 25%+, fundamentals intact, high conviction
            account = get_account()
            trade   = buy_with_guardrails(sym, f"DIP_BUY_{judge_result.get('dip_pct_drop', 0):.0f}pct",
                                          account, is_dip_buy=True,
                                          judge_score=judge_score,
                                          research_last_close=research_close,
                                          watchlist=watchlist,
                                          size_multiplier=size_mult,
                                          positions=positions)
            trade["judge_score"] = judge_score
            trade["action"] = "DIP_BUY"
            trade["dip_pct_drop"] = judge_result.get("dip_pct_drop", 0)
            results["trades"].append(trade)
            _record_entry(trade)

        elif signal in ("STRONG_BUY", "WEAK_BUY") and sym not in open_syms:
            # Gate is source-aware: 7.0 for watchlist, 8.5 for discovery stocks
            gate = judge_result.get("approval_gate", 7.0)
            if judge_rec != "APPROVE" or judge_score < gate:
                results["vetoed_by_judge"].append({
                    "symbol": sym, "signal": signal,
                    "judge_recommendation": judge_rec, "judge_score": judge_score,
                    "approval_gate": gate,
                    "is_discovery": judge_result.get("is_discovery", False),
                    "rationale": judge_result.get("rationale", "No Judge data")
                })
                continue

            account = get_account()
            trade   = buy_with_guardrails(sym, signal, account,
                                          research_limit_price=research_limit,
                                          judge_score=judge_score,
                                          research_last_close=research_close,
                                          watchlist=watchlist,
                                          size_multiplier=size_mult,
                                          positions=positions)
            trade["judge_score"] = judge_score
            trade["action"] = "BUY"
            results["trades"].append(trade)
            _record_entry(trade)

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
            trade   = buy_with_guardrails(sym, signal, account,
                                          research_limit_price=research_limit,
                                          judge_score=judge_score,
                                          research_last_close=research_close,
                                          watchlist=watchlist,
                                          size_multiplier=size_mult,
                                          positions=positions)
            trade["judge_score"] = judge_score
            trade["action"] = "AVG_DOWN"
            results["trades"].append(trade)

        else:
            results["skipped"].append({"symbol": sym, "signal": signal,
                                       "reason": "HOLD or no qualifying signal"})

    save_state(state)

    # Save session results
    session_path = os.path.join(logs_dir, f"{today}-session.json")
    os.makedirs(logs_dir, exist_ok=True)
    with open(session_path, "w") as f:
        json.dump(results, f, indent=2)

    return results

def check_stops():
    """Standalone stop-loss check — can be run any time (midday routine)."""
    positions = get_positions()
    state     = load_state()
    triggers  = check_stop_loss(positions, state)
    stale     = check_inverse_etf_max_hold(positions, state)
    phantom   = detect_phantom_equity(positions, state)
    suspect   = {f["symbol"] for f in phantom}
    results   = {"checked": len(positions), "triggers": triggers,
                 "inverse_max_hold": stale, "phantom_equity": phantom,
                 "closed": [], "failed": [], "skipped_suspect": []}

    for trigger in triggers + stale:
        sym = trigger["symbol"]
        if sym in suspect:
            # Do not liquidate against a price we cannot trust.
            results["skipped_suspect"].append(
                {"symbol": sym, "reason": "phantom equity — manual reconciliation "
                                          "required before any exit"})
            continue
        close_result = close_position(sym, reason=trigger["reason"])
        entry = {"symbol": sym, "result": close_result, "trigger": trigger}
        if order_succeeded(close_result):
            results["closed"].append(entry)
            state["stop_outs"][sym] = datetime.now().isoformat()
            state["entries"].pop(sym, None)
        else:
            # Surface it loudly — a stop that silently failed to place leaves the
            # position unprotected while the log claims it was closed.
            results["failed"].append(entry)

    save_state(state)
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

    elif action == "check-phantom":
        _flags = detect_phantom_equity(get_positions())
        print(json.dumps({
            "phantom_equity": _flags,
            "total_phantom_amount": round(sum(f["phantom_amount"] for f in _flags), 2),
            "verdict": "SUSPECT — reconcile with the broker" if _flags else "clean",
        }, indent=2))
        sys.exit(2 if _flags else 0)

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
