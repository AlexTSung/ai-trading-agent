#!/usr/bin/env python3
"""
forex_trade.py — OANDA order execution for the Forex trading system.
Reads today's verdicts from forex_judge.py and places paper orders.

SECURITY: API keys ONLY via env vars. Never hardcoded. OANDA_ENV=practice enforced.

Usage:
  python forex_trade.py execute           # Execute all tradeable verdicts
  python forex_trade.py execute EUR_USD   # Execute a specific pair
  python forex_trade.py positions         # List all open positions
  python forex_trade.py close EUR_USD     # Close a specific position
  python forex_trade.py close_all         # Close all open positions
  python forex_trade.py status            # Account + open position summary
"""

import os
import sys
import json
import time
import urllib.error
from datetime import date, datetime, timezone

ROOT        = os.path.dirname(os.path.abspath(__file__))
JOURNAL_DIR = os.path.join(ROOT, "logs", "forex")
os.makedirs(JOURNAL_DIR, exist_ok=True)

from forex_screener import (
    _check_auth, get_account_summary, get_open_trades,
    get_pip_size, get_current_price, OANDA_ENV, OANDA_API_KEY, OANDA_ACCOUNT_ID,
    PRACTICE_URL, LIVE_URL,
)

# ── Safety constants (always enforced) ────────────────────────────────────────
MAX_OPEN_PAIRS      = 5       # G2
MAX_JPY_POSITIONS   = 2       # G13: max correlated JPY pairs open at once (USD/JPY, AUD/JPY, CAD/JPY etc all share BOJ risk)
COOLDOWN_HOURS      = 24      # G14: min hours before re-entering a pair after stop-loss; 48h if 2+ JPY stopped same day
MAX_RISK_PCT        = 0.01    # G3: 1% equity per trade — halved from 2% (v1.4.0: avg loss was 380K units vs 137K on wins)
MAX_JPY_RISK_PCT    = 0.0075  # G3b: 0.75% for JPY pairs — halved from 1.5% (v1.4.0)
MAX_LOSS_DOLLARS    = 600     # G3c: hard cap — no trade can risk more than $600 regardless of formula (v1.4.0)
MAX_DAILY_LOSS_PCT  = 0.02    # G4: pause if daily loss > 2% — tightened from 3% (v1.4.0)
STOP_ATR_MULT       = 2.0     # G5: stop = 2.0 × ATR(14) — give trades room to breathe (proven principle)
TP1_RR              = 1.5     # TP1 at 1.5× risk — lowered from 2.0 to capture profit before retracement (v1.3.0)
TP2_RR              = 4.0     # TP2 at 4× risk — runner target after BE move (let profits run)
GATE_SCORE          = 7.0     # G7: minimum judge score
LARGE_SIZE_SCORE    = 8.0
LARGE_SIZE_MULT     = 1.5
ELITE_SCORE         = 9.0
ELITE_MULT          = 2.0

# ── Daily outcome limit (principle: 2 same outcomes = done for the day) ───────
# After 2 wins OR 2 losses today, stop placing new trades until next session.
# This prevents revenge trading after losses and profit-taking addiction after wins.
DAILY_OUTCOME_LIMIT = 2

assert OANDA_ENV == "practice", (
    "FATAL: OANDA_ENV must be 'practice' during Phase 2/3. "
    "Set OANDA_ENV=practice in your environment."
)

BASE_URL = PRACTICE_URL if OANDA_ENV == "practice" else LIVE_URL


def _headers():
    return {
        "Authorization": f"Bearer {OANDA_API_KEY}",
        "Content-Type": "application/json",
    }


def _request(method, path, body=None):
    import urllib.request, ssl
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=_headers(), method=method.upper())
    # Build SSL context — try system certs first (macOS), fall back to default
    try:
        ctx = ssl.create_default_context()
        for cert_path in ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt"):
            import os as _os
            if _os.path.exists(cert_path):
                ctx.load_verify_locations(cert_path)
                break
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"error": f"HTTP Error {e.code}: {e.reason}", "detail": body}
    except Exception as e:
        return {"error": str(e)}


# ── Account checks ─────────────────────────────────────────────────────────────
PRACTICE_DEFAULT_EQUITY = 10_000.0  # fallback when practice account shows $0

def get_equity():
    acct = get_account_summary()
    if "error" in acct:
        return None
    # forex_screener returns flat keys: nav, balance
    nav = float(acct.get("nav") or acct.get("account", {}).get("NAV") or 0)
    if nav == 0 and OANDA_ENV == "practice":
        print(f"[WARN] Account NAV is $0 — using practice default ${PRACTICE_DEFAULT_EQUITY:,.0f}")
        return PRACTICE_DEFAULT_EQUITY
    return nav


def count_open_positions():
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        return 999
    if isinstance(trades, list):
        return len(trades)
    return len(trades.get("trades", []))


def get_open_pairs() -> set:
    """Return set of instrument names currently held (open trades)."""
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        return set()
    trade_list = trades if isinstance(trades, list) else trades.get("trades", [])
    return {t.get("instrument") for t in trade_list if t.get("instrument")}


def count_open_jpy_positions() -> int:
    """Count open positions on JPY pairs (USD/JPY, AUD/JPY, CAD/JPY, etc.) — they share BOJ risk."""
    return sum(1 for p in get_open_pairs() if "JPY" in p)


def get_cooldown_blocked_pairs() -> dict:
    """
    G14: Return pairs blocked by stop-loss cooldown.
    Reads recent closed trades from OANDA — if a pair closed at its SL within
    COOLDOWN_HOURS (24h), or within 48h if 2+ JPY pairs stopped the same day, block it.
    Returns {pair: unblock_time_utc_str}.
    """
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    blocked = {}
    try:
        result = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades?state=CLOSED&count=30")
        trades = result if isinstance(result, list) else result.get("trades", [])

        # Detect how many JPY pairs stopped out today (any in last 48h)
        jpy_stops_today = 0
        for t in trades:
            close_time_str = t.get("closeTime", "")
            if not close_time_str:
                continue
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            hours_ago = (now - close_time).total_seconds() / 3600
            if hours_ago > 48:
                continue
            # Detect stop-loss close: price == SL price
            close_price = float(t.get("averageClosePrice") or 0)
            sl_price = float((t.get("stopLossOrder") or {}).get("price") or 0)
            was_stopped = sl_price > 0 and abs(close_price - sl_price) < 0.001
            if was_stopped and "JPY" in t.get("instrument", ""):
                jpy_stops_today += 1

        cooldown_h = 48 if jpy_stops_today >= 2 else COOLDOWN_HOURS

        for t in trades:
            close_time_str = t.get("closeTime", "")
            if not close_time_str:
                continue
            close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
            hours_ago = (now - close_time).total_seconds() / 3600
            if hours_ago > cooldown_h:
                continue
            close_price = float(t.get("averageClosePrice") or 0)
            sl_price = float((t.get("stopLossOrder") or {}).get("price") or 0)
            was_stopped = sl_price > 0 and abs(close_price - sl_price) < 0.001
            if was_stopped:
                pair = t.get("instrument", "")
                unblock = close_time + timedelta(hours=cooldown_h)
                blocked[pair] = unblock.strftime("%Y-%m-%d %H:%M UTC")
    except Exception as e:
        print(f"  [G14] Cooldown check error: {e}")
    return blocked


def check_daily_outcome_limit() -> dict:
    """
    Principle: 2 wins OR 2 losses today = stop trading for the rest of the session.
    Prevents revenge trading after back-to-back losses and overtrading after wins.
    Break-evens are excluded (discretionary, as per the rule).
    """
    today = date.today().isoformat()
    journal_path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    if not os.path.exists(journal_path):
        return {"blocked": False, "wins": 0, "losses": 0, "reason": ""}

    with open(journal_path) as f:
        journal = json.load(f)

    todays_closed = [
        t for t in journal
        if t.get("status") == "closed"
        and (t.get("closed_at") or "").startswith(today)
        and t.get("realized_pl") is not None
        and abs(t.get("realized_pl", 0)) > 0.01  # exclude break-evens
    ]

    wins   = sum(1 for t in todays_closed if t["realized_pl"] > 0)
    losses = sum(1 for t in todays_closed if t["realized_pl"] < 0)

    if wins >= DAILY_OUTCOME_LIMIT:
        return {"blocked": True, "wins": wins, "losses": losses, "reason": f"{wins} wins today — protect profits"}
    if losses >= DAILY_OUTCOME_LIMIT:
        return {"blocked": True, "wins": wins, "losses": losses, "reason": f"{losses} losses today — prevent revenge trading"}
    return {"blocked": False, "wins": wins, "losses": losses, "reason": ""}


def check_daily_loss_guard():
    """Return True if daily loss limit (G4) has been breached."""
    acct = get_account_summary()
    if "error" in acct:
        return False
    a = acct.get("account", acct)  # flat keys from forex_screener
    nav   = float(a.get("NAV") or a.get("nav") or 0)
    pl    = float(a.get("unrealizedPL") or a.get("unrealized_pl") or 0)
    bal   = float(a.get("balance") or 0)
    daily_loss = min(0, pl)  # unrealized only (realized tracked via journal)
    if bal > 0 and abs(daily_loss) / bal > MAX_DAILY_LOSS_PCT:
        return True
    return False


# ── Position sizing ────────────────────────────────────────────────────────────
def compute_units(pair, stop_pips, equity, size_multiplier=1.0):
    """
    Risk-based position sizing.
    units = risk_amount / (stop_pips × pip_value_per_unit_in_USD)

    Pip value per unit in USD:
      - USD-quoted (EUR/USD, GBP/USD, AUD/USD, NZD/USD): pip_value = 0.0001 USD/unit
      - JPY-quoted  (USD/JPY, EUR/JPY, GBP/JPY, AUD/JPY): pip_value = 0.01 / price USD/unit
      - USD-base    (USD/CHF, USD/CAD): pip_value = 0.0001 / price USD/unit
    Max 50,000 units per trade as hard cap (prevents runaway sizing).
    """
    pip = get_pip_size(pair)
    if not pip or not stop_pips or stop_pips <= 0:
        return None

    risk_amount = equity * MAX_RISK_PCT * size_multiplier

    # Get current price for pairs where quote != USD
    price_data = get_current_price(pair)
    price = price_data["mid"] if price_data and "mid" in price_data else None

    base, quote = pair.split("_") if "_" in pair else (pair[:3], pair[3:])

    if quote == "USD":
        # EUR_USD, GBP_USD, AUD_USD, NZD_USD — pip directly in USD
        pip_value_per_unit = pip  # 0.0001
    elif quote == "JPY":
        # USD_JPY, EUR_JPY, GBP_JPY, AUD_JPY — pip in JPY, convert to USD
        pip_value_per_unit = (pip / price) if price else pip
    elif base == "USD":
        # USD_CHF, USD_CAD — quote is not USD, pip in quote currency
        pip_value_per_unit = (pip / price) if price else pip
    else:
        # Cross pairs: approximate via 0.0001 USD
        pip_value_per_unit = 0.0001

    # JPY pairs cap at 0.75% risk — their higher ATR produces more dollar exposure per pip (v1.4.0)
    if "JPY" in pair:
        risk_amount = equity * MAX_JPY_RISK_PCT * size_multiplier

    # G3c: hard dollar cap — no single trade risks more than MAX_LOSS_DOLLARS (v1.4.0)
    risk_amount = min(risk_amount, MAX_LOSS_DOLLARS)

    units = int(risk_amount / (stop_pips * pip_value_per_unit))
    # Cap at 200K units (2 standard lots) — tightened from 500K (v1.4.0)
    return max(1, min(units, 200_000))


# ── Place order ────────────────────────────────────────────────────────────────
def place_order(verdict: dict, equity: float) -> dict:
    """
    Place a market order with attached stop-loss and take-profit.
    Every order MUST have a stop-loss (G5 guardrail).
    """
    pair      = verdict["pair"]
    direction = verdict["direction"]
    stop_pips = verdict.get("stop_pips")
    stop_price = verdict.get("stop_price")
    tp1_price  = verdict.get("tp1_price")
    score      = verdict.get("score", 0)
    size_mult  = verdict.get("size_multiplier", 1.0)

    if not stop_price:
        return {"error": "G5_no_stop_price — order blocked"}

    units = compute_units(pair, stop_pips, equity, size_mult)
    if not units:
        return {"error": "position_sizing_failed"}

    if direction == "SHORT":
        units = -units

    # JPY pairs use 3 decimal places; all others use 5
    price_precision = 3 if "JPY" in pair else 5

    order_body = {
        "order": {
            "type": "MARKET",
            "instrument": pair,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": str(round(stop_price, price_precision)),
                "timeInForce": "GTC",
            },
        }
    }

    if tp1_price:
        order_body["order"]["takeProfitOnFill"] = {
            "price": str(round(tp1_price, price_precision)),
            "timeInForce": "GTC",
        }

    result = _request("POST", f"/v3/accounts/{OANDA_ACCOUNT_ID}/orders", order_body)

    if "error" in result or "orderFillTransaction" not in result:
        print(f"  [ORDER DEBUG] {pair} result: {json.dumps(result)[:400]}")
        return {
            "status": "failed",
            "pair": pair,
            "error": result.get("error", result),
            "detail": result.get("detail", ""),
            "attempted_units": units,
        }

    fill = result["orderFillTransaction"]
    trade_id = fill.get("tradeOpened", {}).get("tradeID") or fill.get("id")

    return {
        "status": "filled",
        "pair": pair,
        "direction": direction,
        "trade_id": trade_id,
        "units": units,
        "entry_price": float(fill.get("price", 0)),
        "stop_price": stop_price,
        "tp1_price": tp1_price,
        "score": score,
        "size_multiplier": size_mult,
        "environment": OANDA_ENV,
        "placed_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Close position ─────────────────────────────────────────────────────────────
def close_position(pair: str, reason: str = "manual") -> dict:
    result = _request(
        "PUT",
        f"/v3/accounts/{OANDA_ACCOUNT_ID}/positions/{pair}/close",
        {"longUnits": "ALL", "shortUnits": "ALL"},
    )
    if "error" in result:
        return {"status": "failed", "pair": pair, "error": result["error"]}

    pl = 0.0
    transactions = result.get("relatedTransactionIDs", [])
    for tx_key in ("longOrderFillTransaction", "shortOrderFillTransaction"):
        tx = result.get(tx_key, {})
        if tx:
            pl += float(tx.get("pl", 0))

    return {
        "status": "closed",
        "pair": pair,
        "realized_pl": round(pl, 2),
        "reason": reason,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "environment": OANDA_ENV,
    }


def close_all_positions(reason: str = "manual") -> list:
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        return [{"error": trades["error"]}]

    results = []
    seen_pairs = set()
    trade_list = trades if isinstance(trades, list) else trades.get("trades", [])
    for trade in trade_list:
        pair = trade.get("instrument")
        if pair and pair not in seen_pairs:
            seen_pairs.add(pair)
            results.append(close_position(pair, reason))
            time.sleep(0.2)

    return results


# ── Trailing stop management ───────────────────────────────────────────────────
TRAILING_TRIGGER_RR   = 0.5   # Stage 1: partial close + move SL to breakeven at 0.5:1 (half the stop distance)
BREAKEVEN_BUFFER_PIPS = 5     # Breakeven SL = entry + 5 pips buffer (cover spread + small win)
STAGE2_TRIGGER_RR     = 1.5   # Stage 2: trail starts when remaining half reaches TP1 (1.5:1) — was 2.0 (v1.3.0)
STAGE2_TRAIL_RR       = 1.5   # Stage 2: trail SL 1.5× risk_dist behind price — was 0.75, widened to stop sweep (v1.3.0)
TIME_STOP_DAYS        = 3     # Close stagnant trade at +0.5R after 72h — was 5 days (v1.3.0)
TIME_STOP_MIN_RR      = 0.5   # Time stop fires if trade hasn't reached 0.5R within TIME_STOP_DAYS (v1.3.0)

def _partial_close_trade(trade_id: str, units_to_close: int) -> dict:
    """Close a specific number of units of an open trade (partial exit)."""
    body = {"units": str(abs(units_to_close))}
    result = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close", body)
    if "orderFillTransaction" in result:
        fill = result["orderFillTransaction"]
        return {"status": "partial_closed", "pl": float(fill.get("pl", 0)), "price": float(fill.get("price", 0))}
    return {"status": "failed", "error": result.get("error", result)}


def manage_trailing_stops() -> list:
    """
    Two-stage position management per trade:
      Stage 1 — TP1 hit (1× risk profit): close 50% of position, move SL to breakeven+buffer.
                Guarantees a small win on the trade regardless of what happens next.
      Stage 2 — Already at BE: trail SL up as price extends (future enhancement).
    Philosophy: a small profit is always a win. Never let a winner turn into a loser.
    """
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        return []

    trade_list = trades if isinstance(trades, list) else trades.get("trades", [])
    if not trade_list:
        return []

    # Load journal for entry/stop reference data
    journal_path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    journal = []
    if os.path.exists(journal_path):
        try:
            with open(journal_path) as f:
                journal = json.load(f)
        except Exception:
            pass

    journal_by_id = {str(t.get("trade_id")): t for t in journal if t.get("trade_id")}
    results = []

    for trade in trade_list:
        trade_id   = str(trade.get("id"))
        pair       = trade.get("instrument")
        units      = float(trade.get("currentUnits", trade.get("initialUnits", 0)))
        direction  = "LONG" if units > 0 else "SHORT"
        entry      = float(trade.get("price", 0))
        current_sl = None
        sl_order   = trade.get("stopLossOrder")
        if sl_order:
            current_sl = float(sl_order.get("price", 0))

        # Get initial stop from journal; fallback to current SL
        jdata = journal_by_id.get(trade_id, {})
        initial_stop = jdata.get("stop_price") or current_sl

        if not initial_stop or not entry:
            continue

        # Compute initial risk distance in price
        risk_dist = abs(entry - float(initial_stop))
        if risk_dist <= 0:
            continue

        # Current price from trade
        price_data = get_current_price(pair)
        if not price_data or "error" in price_data:
            continue
        current_price = price_data["mid"]

        # How far in profit (in units of risk)?
        if direction == "LONG":
            profit_dist = current_price - entry
            new_sl = entry + BREAKEVEN_BUFFER_PIPS * get_pip_size(pair)
            already_trailed = current_sl is not None and current_sl >= new_sl
        else:
            profit_dist = entry - current_price
            new_sl = entry - BREAKEVEN_BUFFER_PIPS * get_pip_size(pair)
            already_trailed = current_sl is not None and current_sl <= new_sl

        at_1r = profit_dist >= TRAILING_TRIGGER_RR * risk_dist  # at or past 1:1

        if not at_1r:
            continue  # not in profit territory yet

        price_precision = 3 if "JPY" in pair else 5

        # Stage 2: already at breakeven — check if we should trail SL further to lock in gain
        at_2r = profit_dist >= STAGE2_TRIGGER_RR * risk_dist
        if already_trailed and at_2r:
            # Trail SL at STAGE2_TRAIL_RR × risk_dist behind current price
            if direction == "LONG":
                stage2_sl = current_price - STAGE2_TRAIL_RR * risk_dist
                # Only advance the SL (never move it backward)
                if current_sl is not None and stage2_sl > current_sl:
                    new_sl_str = str(round(stage2_sl, price_precision))
                    body = {"stopLoss": {"price": new_sl_str, "timeInForce": "GTC"}}
                    sl_result = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders", body)
                    if "error" not in sl_result:
                        print(f"  [TRAIL2] {pair} trade {trade_id}: SL trailed to {new_sl_str} (wide trail — stop sweep protection)")
                        results.append({"trade_id": trade_id, "pair": pair, "action": "stage2_trail", "new_sl": new_sl_str, "old_sl": current_sl})
            else:
                stage2_sl = current_price + STAGE2_TRAIL_RR * risk_dist
                # Only advance the SL (never move it backward for short)
                if current_sl is not None and stage2_sl < current_sl:
                    new_sl_str = str(round(stage2_sl, price_precision))
                    body = {"stopLoss": {"price": new_sl_str, "timeInForce": "GTC"}}
                    sl_result = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders", body)
                    if "error" not in sl_result:
                        print(f"  [TRAIL2] {pair} trade {trade_id}: SL trailed to {new_sl_str} (wide trail — stop sweep protection)")
                        results.append({"trade_id": trade_id, "pair": pair, "action": "stage2_trail", "new_sl": new_sl_str, "old_sl": current_sl})
            continue  # skip Stage 1 logic for already-trailed trades

        # Stage 1: if not yet trailed, do partial close (50%) + move SL to BE
        if not already_trailed:
            # Partial close — take profit on half the position
            initial_units_abs = abs(int(float(trade.get("initialUnits", units))))
            current_units_abs = abs(int(units))
            half = max(1, current_units_abs // 2)

            partial = _partial_close_trade(trade_id, half)
            if partial.get("status") == "partial_closed":
                print(f"  [TRAIL] {pair} trade {trade_id}: PARTIAL CLOSE {half} units @ {partial['price']:.5f} | PL=${partial['pl']:+.2f}")
            else:
                print(f"  [TRAIL] {pair} trade {trade_id}: partial close failed — {partial.get('error','?')}")

            # Move SL to breakeven regardless of partial success
            new_sl_str = str(round(new_sl, price_precision))
            body = {"stopLoss": {"price": new_sl_str, "timeInForce": "GTC"}}
            sl_result = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/orders", body)

            if "error" not in sl_result:
                print(f"  [TRAIL] {pair} trade {trade_id}: SL → breakeven {new_sl_str} — remaining half running; Stage 2 trails at {STAGE2_TRIGGER_RR}R")
                results.append({
                    "trade_id": trade_id, "pair": pair,
                    "action": "partial_close_and_breakeven",
                    "new_sl": new_sl_str, "old_sl": current_sl,
                    "partial_pl": partial.get("pl", 0),
                })
            else:
                print(f"  [TRAIL] {pair} trade {trade_id}: SL to BE failed — {sl_result.get('error')}")

    return results


# ── Time stop management ───────────────────────────────────────────────────────
def check_time_stops() -> list:
    """
    Close any trade that has been open longer than TIME_STOP_DAYS without reaching 1:1 RR.
    Rationale: stagnant trades mean momentum is dead — free the capital.
    A trade that hits 1:1 RR early is exempt (still has potential, trailing stop protects it).
    """
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        return []
    trade_list = trades if isinstance(trades, list) else trades.get("trades", [])
    if not trade_list:
        return []

    journal_path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    journal = []
    if os.path.exists(journal_path):
        try:
            with open(journal_path) as f:
                journal = json.load(f)
        except Exception:
            pass
    journal_by_id = {str(t.get("trade_id")): t for t in journal if t.get("trade_id")}

    now = datetime.now(timezone.utc)
    results = []

    for trade in trade_list:
        trade_id  = str(trade.get("id"))
        pair      = trade.get("instrument")
        units     = float(trade.get("currentUnits", 0))
        direction = "LONG" if units > 0 else "SHORT"
        entry     = float(trade.get("price", 0))
        unreal_pl = float(trade.get("unrealizedPL", 0))
        open_time = trade.get("openTime", "")

        # Parse open time
        try:
            opened = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
            days_open = (now - opened).days
        except Exception:
            continue

        if days_open < TIME_STOP_DAYS:
            continue  # Trade is young — give it time

        # Check if trade has reached 1:1 RR using journal stop data
        jdata = journal_by_id.get(trade_id, {})
        initial_stop = jdata.get("stop_price")

        if initial_stop:
            risk_dist = abs(entry - float(initial_stop))
            reached_target = (unreal_pl / (risk_dist * abs(units))) >= TIME_STOP_MIN_RR if risk_dist and units else False
        else:
            # No journal data — use unrealized P&L as proxy (positive = reached some profit)
            reached_target = unreal_pl > 0

        if reached_target:
            print(f"  [TIMESTOP] {pair} trade {trade_id}: {days_open}d old but reached {TIME_STOP_MIN_RR}R — keeping")
            continue

        print(f"  [TIMESTOP] {pair} trade {trade_id}: {days_open}d open, no {TIME_STOP_MIN_RR}R RR — closing (momentum dead)")
        raw = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close")
        if "orderFillTransaction" in raw or "orderCancelTransaction" in raw:
            fill = raw.get("orderFillTransaction", {})
            pl = float(fill.get("pl", 0))
            result = {"status": "closed", "pair": pair, "realized_pl": round(pl, 2), "reason": f"TIME_STOP_{days_open}d"}
        else:
            result = {"status": "failed", "pair": pair, "error": raw.get("error", raw), "detail": raw.get("detail", "")}
        results.append({
            "trade_id": trade_id, "pair": pair, "days_open": days_open,
            "unrealized_pl": unreal_pl, "result": result,
        })
        time.sleep(0.3)

    return results


# ── Main execution ─────────────────────────────────────────────────────────────
def execute_verdicts(target_pair: str = None) -> list:
    """
    Load today's verdicts and execute tradeable ones.
    Respects all G-series guardrails before placing any order.
    """
    _check_auth()
    today = date.today().isoformat()
    path  = os.path.join(JOURNAL_DIR, f"{today}_verdicts.json")

    if not os.path.exists(path):
        print(json.dumps({"error": f"No verdicts for {today}. Run: python forex_judge.py session"}))
        sys.exit(1)

    with open(path) as f:
        data = json.load(f)

    verdicts = data.get("verdicts", [])
    tradeable = [
        v for v in verdicts
        if v.get("verdict") not in ("SKIP", "DISQUALIFIED", "NEUTRAL", None)
        and v.get("score", 0) >= GATE_SCORE
    ]

    if target_pair:
        tradeable = [v for v in tradeable if v.get("pair") == target_pair.upper()]

    if not tradeable:
        print(json.dumps({"message": "No tradeable verdicts", "date": today}))
        return []

    # G4: Check daily loss guard
    if check_daily_loss_guard():
        print(json.dumps({"error": "G4_daily_loss_limit_breached — trading paused for today"}))
        return []

    # PRINCIPLE: Daily outcome limit — 2 wins OR 2 losses = done for the day
    outcome_status = check_daily_outcome_limit()
    if outcome_status["blocked"]:
        print(json.dumps({
            "error": f"DAILY_LIMIT: {outcome_status['wins']}W/{outcome_status['losses']}L today — "
                     f"{outcome_status['reason']}. No new trades until next session.",
        }))
        return []

    # G2: Check open position count
    open_count = count_open_positions()
    if open_count >= MAX_OPEN_PAIRS:
        print(json.dumps({"error": f"G2_max_positions_reached — {open_count} open, max {MAX_OPEN_PAIRS}"}))
        return []

    equity = get_equity()
    if not equity:
        print(json.dumps({"error": "cannot_fetch_equity"}))
        return []

    results = []
    slots_available = MAX_OPEN_PAIRS - open_count
    open_pairs = get_open_pairs()          # G2b: same-pair duplicate guard
    cooldown_blocked = get_cooldown_blocked_pairs()  # G14: stop-loss cooldown
    jpy_open = count_open_jpy_positions()  # G13: JPY correlation cap

    for verdict in tradeable[:slots_available]:
        pair = verdict.get("pair")

        # G2b: Skip if we already hold this pair — prevents double-entry on back-to-back runs
        if pair in open_pairs:
            print(f"  G2b: {pair} already open — skipping duplicate entry")
            continue

        # G14: Stop-loss cooldown — skip re-entry within 24h (48h if 2+ JPY stopped same day)
        if pair in cooldown_blocked:
            print(f"  G14: {pair} in cooldown after stop-loss — unblocks {cooldown_blocked[pair]}")
            continue

        # G13: JPY correlation cap — max 2 JPY positions at once
        if "JPY" in pair and jpy_open >= MAX_JPY_POSITIONS:
            print(f"  G13: JPY correlation cap reached ({jpy_open}/{MAX_JPY_POSITIONS}) — skipping {pair}")
            continue

        print(f"  Placing {verdict['direction']} on {pair} | score={verdict['score']} | env={OANDA_ENV}")
        result = place_order(verdict, equity)
        results.append(result)

        # Log to journal immediately
        _log_trade_open(result, verdict)

        if result.get("status") == "filled":
            print(f"  ✓ Filled: {pair} @ {result.get('entry_price')} | units={result.get('units')} | stop={result.get('stop_price')}")
            open_pairs.add(pair)  # G2b: block same pair in later iterations this run
            if "JPY" in pair:
                jpy_open += 1     # G13: track JPY count within this run
        else:
            print(f"  ✗ Failed: {pair} — {result.get('error')}")

        time.sleep(0.3)

    return results


def _log_trade_open(fill_result: dict, verdict: dict):
    """Append opened trade to today's trade log AND the main trade_journal.json."""
    if fill_result.get("status") != "filled":
        return

    entry = {
        "status": "open",
        "event": "open",
        "pair": fill_result.get("pair"),
        "direction": fill_result.get("direction"),
        "trade_id": fill_result.get("trade_id"),
        "units": fill_result.get("units"),
        "entry_price": fill_result.get("entry_price"),
        "stop_price": fill_result.get("stop_price"),
        "tp1_price": fill_result.get("tp1_price"),
        "score": fill_result.get("score"),
        "confluence": verdict.get("confluence") or verdict.get("entry_confluence"),
        "size_multiplier": fill_result.get("size_multiplier"),
        "factors": verdict.get("factors"),
        "environment": OANDA_ENV,
        "opened_at": fill_result.get("placed_at"),
    }

    # Write to dated trades file (daily record)
    today = date.today().isoformat()
    log_path = os.path.join(JOURNAL_DIR, f"{today}_trades.json")
    day_trades = []
    if os.path.exists(log_path):
        with open(log_path) as f:
            day_trades = json.load(f)
    day_trades.append(entry)
    with open(log_path, "w") as f:
        json.dump(day_trades, f, indent=2)

    # Also write to main trade_journal.json (needed by sync_closed_trades)
    journal_path = os.path.join(JOURNAL_DIR, "trade_journal.json")
    journal = []
    if os.path.exists(journal_path):
        with open(journal_path) as f:
            journal = json.load(f)
    # Avoid duplicate trade_ids
    existing_ids = {str(t.get("trade_id")) for t in journal}
    if str(entry.get("trade_id")) not in existing_ids:
        journal.append(entry)
        with open(journal_path, "w") as f:
            json.dump(journal, f, indent=2)


def print_positions():
    """Print all open OANDA positions."""
    _check_auth()
    trades = get_open_trades()
    if isinstance(trades, dict) and "error" in trades:
        print(json.dumps(trades))
        return

    open_trades = trades if isinstance(trades, list) else trades.get("trades", [])
    if not open_trades:
        print("No open positions.")
        return

    print(f"\n{'='*65}")
    print(f"  OPEN POSITIONS ({OANDA_ENV.upper()})  —  {len(open_trades)} open")
    print(f"{'='*65}")
    for t in open_trades:
        pair      = t.get("instrument")
        units     = int(t.get("currentUnits", 0))
        direction = "LONG" if units > 0 else "SHORT"
        entry     = float(t.get("price", 0))
        upl       = float(t.get("unrealizedPL", 0))
        trade_id  = t.get("id")
        opened    = t.get("openTime", "")[:16]
        print(f"  {pair:12s}  {direction:6s}  units={abs(units):>8,}  entry={entry:.5f}  uPL=${upl:+.2f}  id={trade_id}  {opened}")
    print()

    acct = get_account_summary()
    if "error" not in acct:
        nav = float(acct.get("nav") or acct.get("account", {}).get("NAV") or 0)
        bal = float(acct.get("balance") or acct.get("account", {}).get("balance") or 0)
        margin = float(acct.get("margin_used") or acct.get("account", {}).get("marginUsed") or 0)
        print(f"  NAV: ${nav:,.2f}  |  Balance: ${bal:,.2f}  |  Margin Used: ${margin:,.2f}")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "execute":
        pair = sys.argv[2].upper() if len(sys.argv) > 2 else None
        results = execute_verdicts(pair)
        if results:
            print(json.dumps(results, indent=2))

    elif cmd == "positions":
        print_positions()

    elif cmd == "close":
        if len(sys.argv) < 3:
            print("Usage: forex_trade.py close EUR_USD")
            sys.exit(1)
        pair = sys.argv[2].upper()
        result = close_position(pair, reason="manual_cli")
        print(json.dumps(result, indent=2))

    elif cmd == "close_all":
        results = close_all_positions(reason="manual_cli")
        print(json.dumps(results, indent=2))

    elif cmd == "status":
        _check_auth()
        print_positions()

    else:
        print(f"Unknown command. Usage: forex_trade.py [execute | positions | close EUR_USD | close_all | status]")
        sys.exit(1)
