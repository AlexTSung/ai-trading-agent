#!/usr/bin/env python3
"""
robinhood_trade.py — guardrails, sizing, and journaling for the real-money
Robinhood book. See CLAUDE.md's "Robinhood Execution Procedure" section for
how this module is actually used end to end.

Pure logic only — no Robinhood API or MCP calls live here. MCP tools are
only callable from an interactive agent turn, not a Python subprocess, so
every function below takes already-fetched account/position/order data as
plain dicts, the same pattern robinhood_screener.py already established in
this repo (see its module docstring).

Reuses trade.py's account-agnostic Hard Rule functions verbatim where it is
safe to (tier_for, stop_pct_for, position_pct_for, regime_adjusted_position_pct,
is_buyable, allocation_cap_for, check_stop_loss, check_inverse_etf_max_hold,
detect_phantom_equity, cooldown_remaining_hours) rather than duplicating
them — a second copy of a stop-loss tier table or a conviction-sizing
formula is exactly how the two books' Hard Rules would silently drift apart.

Two functions are deliberately NOT reused: trade.honest_portfolio_value()
and trade.calculate_max_shares() both internally call
trade.detect_phantom_equity(positions) with no state argument, which falls
back to trade.load_state() — the PAPER book's logs/paper/agent-state.json.
Calling either of those here would make a real-money position-size decision
quietly consult Alpaca's state file. honest_equity() and max_shares_for()
below run the identical formula but always pass this book's own state
explicitly, never trade.py's.

State (cooldowns, entry dates/judge scores) is never trade.py's
logs/paper/agent-state.json — it lives in the private
ai-trading-agent-real-logs checkout, passed in via a logs_dir argument that
traces back to the REAL_LOGS_DIR an interactive session points at that
checkout. There is no default path inside this repo for it: an unset or
empty logs_dir is a hard error, not a silent fallback, because the one
outcome worse than this script refusing to run is it writing real trading
data into the public repo.
"""

import csv
import json
import math
import os
from datetime import datetime

import trade  # reused: tier_for, stop_pct_for, position_pct_for,
              # regime_adjusted_position_pct, is_buyable, allocation_cap_for,
              # check_stop_loss, check_inverse_etf_max_hold, detect_phantom_equity,
              # cooldown_remaining_hours
import judge  # reused: score_news() -- same keyword classifier as the paper
              # book's own portfolio_review(), not a second copy of the word lists
from market_time import et_timestamp, et_today

CASH_RESERVE_PCT = trade.CASH_RESERVE_PCT   # Hard Rule 8's 20% floor, same number
MIN_TRADE_PRICE  = trade.MIN_TRADE_PRICE

TRADE_LOG_HEADER = ["date", "symbol", "side", "qty", "filled_price", "order_id", "status"]


def _open_positions(positions: list) -> list:
    """
    Filter out zero/negative-qty rows before anything below treats
    "present in positions" as meaningful. Confirmed live, 2026-10-30: the
    Robinhood MCP tool's position list can return a residual row for a
    fully-closed position (qty 0) instead of omitting it, which breaks fill
    detection in both directions if left unfiltered -- reconcile_fills()
    would mark a still-pending BUY as already filled (the symbol is
    technically "present"), and reconcile_sell_fills() would mark an
    actually-filled SELL_FULL as "expired" instead of filled (the symbol
    never truly disappears), silently skipping record_full_exit() and the
    48h cooldown it starts. Applied once here rather than relying on every
    caller to pre-filter live broker data.
    """
    return [p for p in positions if float(p.get("qty", 0) or 0) > 0]


class RealLogsDirNotSet(RuntimeError):
    """Raised instead of silently defaulting anywhere inside the public repo."""


def _require_logs_dir(logs_dir: str | None) -> str:
    if not logs_dir:
        raise RealLogsDirNotSet(
            "logs_dir is not set. This must be a local checkout of the private "
            "ai-trading-agent-real-logs repo -- there is no default, on purpose, "
            "because the one thing worse than this script refusing to run is it "
            "writing real trading data into the public repo."
        )
    return logs_dir


# ── state (private repo only) ─────────────────────────────────────────────────

def load_real_state(logs_dir: str) -> dict:
    logs_dir = _require_logs_dir(logs_dir)
    path = os.path.join(logs_dir, "agent-state.json")
    state = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                state = json.load(f)
        except ValueError:
            state = {}
    state.setdefault("stop_outs", {})
    state.setdefault("entries", {})
    return state


def save_real_state(logs_dir: str, state: dict) -> None:
    logs_dir = _require_logs_dir(logs_dir)
    with open(os.path.join(logs_dir, "agent-state.json"), "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def record_entry(state: dict, symbol: str, judge_score, qty: float,
                  entry_price: float = None) -> None:
    """Mirrors trade.py's post-fill bookkeeping, for this book's own
    cooldown/min-hold tracking. Never touches trade.py's state.

    entry_price is optional (existing callers and existing state files
    predate it) but required for check_profit_targets() to compute Hard
    Rule 7's T1/T2 -- a position recorded without it simply never produces
    a profit-target candidate, which is the correct "fail closed on missing
    data" behaviour rather than guessing an entry price.
    """
    state.setdefault("entries", {})[symbol] = {
        "date": datetime.now().isoformat(),
        "judge_score": judge_score,
        "qty": qty,
        "entry_price": entry_price,
    }


def record_stop_out(state: dict, symbol: str) -> None:
    """Starts this book's own 48h re-entry cooldown (Hard Rule 4)."""
    state.setdefault("stop_outs", {})[symbol] = datetime.now().isoformat()


def record_pending_order(state: dict, symbol: str, judge_score, qty: float,
                          order_id: str = None) -> None:
    """
    Call this at order-placement time (step 8), not fill time. Under
    v3.0.0 (no chat confirmation) + v4.0.0 (scheduled, ~30-min ticks), a
    placed order can sit awaiting Trade Approval across several ticks, or
    fill in a tick that isn't the one that placed it. Without this record,
    the judge_score and intended qty that justified the order are gone by
    the time a later tick notices the fill -- record_entry() would either
    never run (silent cooldown/min-hold gap) or run with no judge_score
    (defeating Hard Rule 5's min-hold, which treats a missing judge_score
    as "no min-hold applies").
    """
    state.setdefault("pending", {})[symbol] = {
        "judge_score": judge_score,
        "qty": qty,
        "order_id": order_id,
        "placed_at": datetime.now().isoformat(),
    }


def reconcile_fills(positions: list, open_orders: list, state: dict) -> dict:
    """
    Run this at the start of every tick, before building new buy
    candidates (step 7). For every symbol `record_pending_order()` is
    still tracking:

    - It now appears in `positions` -> filled since it was placed. Call
      the equivalent of record_entry() using the *saved* judge_score (the
      one that actually justified the trade) and the position's real
      qty/avg_entry_price (more accurate than the originally-requested
      qty, in case of a partial fill), then drop it from `pending`.
    - It's in neither `positions` nor `open_orders` -> resolved without
      filling (cancelled, rejected, expired). Drop it from `pending` so it
      doesn't grow unboundedly; nothing to journal.
    - It's still in `open_orders` -> still awaiting Trade Approval or
      still working. Leave it alone. `open_orders` must already include
      pending approvals (`approvals_to_open_orders()`), since an order
      awaiting the human's tap does not exist in `get_equity_orders` yet.

    Returns {"filled": [...], "expired": [...]} for the caller to journal
    and log. Mutates `state` in place (same contract as record_entry).
    """
    pending = state.get("pending", {})
    if not pending:
        return {"filled": [], "expired": []}

    positions_by_symbol = {p["symbol"]: p for p in _open_positions(positions)}
    open_order_syms = {o.get("symbol") for o in open_orders}

    filled, expired = [], []
    for symbol in list(pending.keys()):
        info = pending[symbol]
        pos = positions_by_symbol.get(symbol)

        if pos is not None:
            record_entry(state, symbol, info.get("judge_score"), pos["qty"],
                         entry_price=pos.get("avg_entry_price"))
            filled.append({
                "symbol": symbol,
                "side": "buy",
                "qty": pos["qty"],
                "filled_price": pos["avg_entry_price"],
                "order_id": info.get("order_id"),
                "judge_score": info.get("judge_score"),
                "status": "filled",
            })
            del pending[symbol]
        elif symbol not in open_order_syms:
            expired.append({"symbol": symbol, "order_id": info.get("order_id")})
            del pending[symbol]
        # else: still open, leave pending as-is

    return {"filled": filled, "expired": expired}


def record_full_exit(state: dict, symbol: str) -> None:
    """Call after a confirmed full-exit SELL fill (stop-loss or inverse-ETF
    max-hold). Starts the 48h re-entry cooldown (Hard Rule 4) -- same
    record_stop_out() the paper book's equivalent event uses -- and clears
    the position's entry record, since there is nothing left to compute a
    T1/T2 target against."""
    record_stop_out(state, symbol)
    state.get("entries", {}).pop(symbol, None)


def record_partial_exit(state: dict, symbol: str, target: str) -> None:
    """Call after a confirmed partial-exit SELL fill (Hard Rule 7's T1/T2).
    Marks the target taken so check_profit_targets() doesn't re-propose it
    on the next tick, and -- for T1 specifically -- is what makes
    check_stops() switch that position's effective stop to breakeven on the
    remainder, per "move stop to breakeven on remainder" in Hard Rule 7."""
    entry = state.setdefault("entries", {}).setdefault(symbol, {})
    if target == "T1":
        entry["t1_taken"] = True
    elif target == "T2":
        entry["t2_taken"] = True


def record_pending_sell(state: dict, symbol: str, action: str, qty: float,
                         order_id: str = None, target: str = None,
                         qty_before_order: float = None) -> None:
    """
    Sell-side counterpart to record_pending_order() -- call at sell
    order-placement time, not fill time, for the same reason: under v3.0.0
    (no chat confirmation), a sell can resolve on a later tick than the one
    that placed it.

    action: "SELL_FULL" or "SELL_PARTIAL" (matches build_sell_candidates()'s
    candidate shape). qty_before_order (the position's held qty at the
    moment this order was placed) is required for SELL_PARTIAL --
    reconcile_sell_fills() has no other signal that a partial sell filled,
    since the symbol stays in `positions` either way; a full exit's fill
    signal (the symbol disappearing from positions entirely) doesn't need it.
    """
    state.setdefault("pending_sells", {})[symbol] = {
        "action": action,
        "qty": qty,
        "order_id": order_id,
        "target": target,
        "qty_before_order": qty_before_order,
        "placed_at": datetime.now().isoformat(),
    }


def reconcile_sell_fills(positions: list, open_orders: list, state: dict) -> dict:
    """
    Sell-side counterpart to reconcile_fills() -- same problem (an order
    placed without chat confirmation can fill on a later tick), different
    fill signal. A fill is detected by a *position-state change*, not by
    order status, because open_orders may not retain a resolved order at
    all (the same assumption reconcile_fills() already makes for buys):

    - SELL_FULL fills when the symbol disappears from `positions` entirely.
    - SELL_PARTIAL fills when the held qty drops below qty_before_order.
    - Either one is left pending while still resting in `open_orders`, and
      dropped as expired (never filled -- cancelled, rejected, or Trade
      Approval declined it) once it's gone from `open_orders` without
      either fill signal having fired.

    Run this alongside reconcile_fills() at the start of every tick, before
    building new sell candidates (CLAUDE.md step 4a).
    """
    pending = state.get("pending_sells", {})
    if not pending:
        return {"filled": [], "expired": []}

    positions_by_symbol = {p["symbol"]: p for p in _open_positions(positions)}
    open_order_syms = {o.get("symbol") for o in open_orders}

    filled, expired = [], []
    for symbol in list(pending.keys()):
        info = pending[symbol]
        pos = positions_by_symbol.get(symbol)
        action = info["action"]

        if action == "SELL_FULL":
            if pos is None:
                record_full_exit(state, symbol)
                filled.append({"symbol": symbol, "side": "sell", "action": "SELL_FULL",
                               "qty": info.get("qty"), "order_id": info.get("order_id")})
                del pending[symbol]
            elif symbol not in open_order_syms:
                expired.append({"symbol": symbol, "order_id": info.get("order_id")})
                del pending[symbol]
            # else: still resting, leave pending as-is

        else:  # SELL_PARTIAL
            before = info.get("qty_before_order")
            current_qty = float(pos["qty"]) if pos is not None else 0.0
            if pos is not None and before is not None and current_qty < float(before):
                record_partial_exit(state, symbol, info.get("target"))
                filled.append({"symbol": symbol, "side": "sell", "action": "SELL_PARTIAL",
                               "target": info.get("target"), "qty": info.get("qty"),
                               "order_id": info.get("order_id")})
                del pending[symbol]
            elif symbol not in open_order_syms:
                expired.append({"symbol": symbol, "order_id": info.get("order_id")})
                del pending[symbol]
            # else: still resting, leave pending as-is

    return {"filled": filled, "expired": expired}


def check_profit_targets(positions: list, state: dict) -> list:
    """
    Hard Rule 7: sell 40% at T1 (entry + 2x stop distance), another 40% at
    T2 (entry + 3x stop distance), let the final 20% run. Reuses
    trade.py's TAKE_PROFIT_R1/R2 and T1_SELL_FRACTION rather than
    duplicating the ratios.

    Needs this book's own entry_price on record (set by record_entry()) --
    a position with no tracked entry (opened before this tracking existed,
    or outside this pipeline entirely) cannot have a target computed and is
    silently skipped here, same "fail closed on missing data" rule this
    file applies everywhere else, rather than guessing an entry price.

    Each target fires at most once per position: state["entries"][symbol]
    records t1_taken/t2_taken (set by record_partial_exit() once the sell
    actually fills) so a re-run inside the same ~30-minute tick window
    doesn't re-propose a target it already executed.
    """
    entries = state.get("entries", {})
    targets = []
    for pos in _open_positions(positions):
        sym = pos["symbol"]
        entry_record = entries.get(sym)
        if not entry_record or not entry_record.get("entry_price"):
            continue

        entry_price = float(entry_record["entry_price"])
        current_price = float(pos.get("current_price", 0))
        original_qty = entry_record.get("qty")
        held_qty = float(pos.get("qty", 0))
        if entry_price <= 0 or current_price <= 0 or not original_qty or held_qty <= 0:
            continue

        stop_distance = entry_price * abs(trade.stop_pct_for(sym))
        t1_price = entry_price + trade.TAKE_PROFIT_R1 * stop_distance
        t2_price = entry_price + trade.TAKE_PROFIT_R2 * stop_distance
        sell_qty = int(min(math.floor(original_qty * trade.T1_SELL_FRACTION), held_qty))
        if sell_qty <= 0:
            continue

        if not entry_record.get("t1_taken") and current_price >= t1_price:
            targets.append({
                "symbol": sym, "target": "T1", "qty": sell_qty,
                "current_price": current_price, "target_price": round(t1_price, 2),
                "reason": (f"Hard Rule 7: T1 (${t1_price:.2f}) reached -- sell "
                           f"{trade.T1_SELL_FRACTION:.0%} ({sell_qty} sh), move "
                           f"stop to breakeven on the remainder"),
            })
        elif (entry_record.get("t1_taken") and not entry_record.get("t2_taken")
                and current_price >= t2_price):
            targets.append({
                "symbol": sym, "target": "T2", "qty": sell_qty,
                "current_price": current_price, "target_price": round(t2_price, 2),
                "reason": (f"Hard Rule 7: T2 (${t2_price:.2f}) reached -- sell "
                           f"another {trade.T1_SELL_FRACTION:.0%} ({sell_qty} sh), "
                           f"let the remainder run"),
            })
    return targets


def build_sell_candidates(positions: list, state: dict, open_orders: list = None) -> list:
    """
    Composes every mechanical (rule-based, no judgment call) sell signal
    this book can detect into SELL candidates -- same audit discipline as
    build_buy_candidates(): a SKIP is never shown as something to confirm,
    a SELL is built fully-sized and fully-reasoned before the interactive
    session ever calls place_equity_order.

    Covers: Hard Rule 3 tiered stop-losses and the Bearish Playbook's
    inverse-ETF max-hold (both full exits, via check_stops()), and Hard
    Rule 7's T1/T2 partial profit-taking (via check_profit_targets()).
    Hard Rule 11 phantom-equity positions are excluded from every path here
    -- "blocked from all trading, excluded from exit placement" -- they
    SKIP with a reason, never a SELL.

    Deliberately does NOT cover a Judge re-evaluating open positions for
    "best opportunity to maximize profit" with a live choice of order type.
    That decision has no track record anywhere in this codebase -- even the
    paper book's own sells are a static bracket set once at entry
    (trade.place_oco_exits), never re-evaluated -- so it stays out of this
    function until it has a paper-book track record of its own.
    """
    open_orders = open_orders or []
    pending_sell_syms = {
        o.get("symbol") for o in open_orders
        if str(o.get("side", "")).lower() == "sell"
        and str(o.get("status", "")).lower() in OPEN_ORDER_STATUSES
    }
    phantom_syms = {f["symbol"] for f in phantom_audit(positions, state)}
    positions_by_symbol = {p["symbol"]: p for p in _open_positions(positions)}

    stop_results = check_stops(positions, state)
    profit_targets = check_profit_targets(positions, state)

    candidates = []
    seen_full_exit_syms = set()

    for trigger in stop_results["stop_losses"] + stop_results["inverse_etf_max_hold"]:
        sym = trigger["symbol"]
        if sym in seen_full_exit_syms:
            continue
        seen_full_exit_syms.add(sym)
        if sym in phantom_syms:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": [
                f"{sym} is phantom equity (Hard Rule 11) -- blocked from all "
                f"trading, including this exit, pending manual reconciliation"]})
            continue
        if sym in pending_sell_syms:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": [
                f"{sym} already has a pending sell order -- not stacking a second one"]})
            continue
        pos = positions_by_symbol.get(sym)
        if not pos:
            continue
        current_price = float(pos.get("current_price", 0))
        limit_price = round(current_price * (1 - trade.URGENT_EXIT_SLIPPAGE), 2)
        candidates.append({
            "symbol": sym, "action": "SELL_FULL", "qty": pos["qty"],
            "limit_price": limit_price, "tier": trigger.get("tier") or trade.tier_for(sym),
            "reason": trigger["reason"],
        })

    for target in profit_targets:
        sym = target["symbol"]
        if sym in phantom_syms:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": [
                f"{sym} is phantom equity (Hard Rule 11) -- blocked from all "
                f"trading, including this exit, pending manual reconciliation"]})
            continue
        if sym in pending_sell_syms:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": [
                f"{sym} already has a pending sell order -- not stacking a second one"]})
            continue
        limit_price = round(target["current_price"] * (1 - trade.LIMIT_SLIPPAGE), 2)
        candidates.append({
            "symbol": sym, "action": "SELL_PARTIAL", "qty": target["qty"],
            "limit_price": limit_price, "target": target["target"],
            "reason": target["reason"],
        })

    return candidates


NEAR_STOP_WARNING_PCT = 0.03  # same threshold as trade.py's own portfolio_review()


def portfolio_review(positions: list, state: dict, research: dict) -> list:
    """
    Advisory only -- never executes or proposes an order. Real-money
    counterpart to trade.py's portfolio_review(), same logic, same
    threshold, this book's own state (entry_price is already tracked here
    since v5.0.0's reconcile_fills(), so no extra plumbing was needed):

    - An early warning when unrealized P&L is closing in on the tier stop
      (check_stops()'s breakeven-after-T1 override already runs separately
      in build_sell_candidates() and will fire the actual exit -- this is
      advance notice, not a replacement).
    - Hard Rule 3's own Tier-1 thesis-break exception: "MA20 crosses below
      MA50 AND confirmed by a negative fundamental catalyst." Nothing
      currently checks this combination automatically for this book either.

    research is the equities-side research.json (account-agnostic per Hard
    Rule 4, Account Guardrails) -- this module has no way to fetch bars or
    news itself, so the interactive session passes in whatever the equities
    pipeline already produced for the day.
    """
    symbols_data = research.get("symbols", {}) if research else {}
    entries = state.get("entries", {})
    flags = []

    for pos in _open_positions(positions):
        sym = pos["symbol"]
        plpc = float(pos.get("unrealized_plpc", 0))
        tier = trade.tier_for(sym)
        stop_pct = trade.stop_pct_for(sym)
        sym_data = symbols_data.get(sym, {})
        technicals = sym_data.get("technicals", {})
        news_items = sym_data.get("news", [])

        entry_record = entries.get(sym, {})
        days_held = None
        entry_date_str = entry_record.get("date")
        if entry_date_str:
            try:
                days_held = (et_today() - datetime.fromisoformat(entry_date_str).date()).days
            except ValueError:
                days_held = None

        reasons = []

        distance_to_stop = plpc - stop_pct
        if 0 <= distance_to_stop <= NEAR_STOP_WARNING_PCT:
            reasons.append(f"within {distance_to_stop*100:.1f} pts of its {tier} stop "
                            f"({stop_pct:.0%}) -- currently {plpc*100:+.1f}%")

        if tier == "TIER1":
            ma20, ma50 = technicals.get("ma20"), technicals.get("ma50")
            technical_break = ma20 is not None and ma50 is not None and ma20 < ma50
            _, news_note = judge.score_news(news_items, sym_data)
            has_negative_catalyst = news_note.startswith("NEGATIVE")
            if technical_break and has_negative_catalyst:
                reasons.append("MA20 < MA50 AND a negative news catalyst -- Hard Rule 3's "
                                "thesis-break exception may apply")
            elif technical_break:
                reasons.append("MA20 < MA50, but no confirmed negative catalyst -- Hard "
                                "Rule 3 says a price dip alone is not a sell trigger")

        if reasons:
            flags.append({
                "symbol": sym, "tier": tier, "unrealized_plpc": plpc,
                "days_held": days_held, "reasons": reasons,
            })

    return flags


# ── entry limit price — real-money book only ──────────────────────────────────

# Alex, 2026-10-30: the pullback limits (MA20 x 0.995, sitting 2-3% under the
# market for a stock above its MA20) never triggered on KMI or AS, so he
# replaced them by hand with limits ~0.4-0.5% under the ask. The limit should
# be realistic enough to fill, at the lowest price that realistically fills.
# These numbers are calibrated to those two manual orders, not to a fill-rate
# backtest -- revisit once the real book has fills to measure against.
REALISTIC_LIMIT_ATR_FRACTION = 0.25    # discount = 25% of one ATR ...
REALISTIC_LIMIT_MIN_DISCOUNT = 0.001   # ... but at least 0.1% under the ask
REALISTIC_LIMIT_MAX_DISCOUNT = 0.005   # ... and never more than 0.5%: past that it stops filling
REALISTIC_LIMIT_FALLBACK_DISCOUNT = 0.003   # ATR unknown: Hard Rule 2's old ask x 0.997
CHASE_CEILING_ABOVE_MA20 = 0.03        # Entry Checklist #1: >3% above MA20 -> wait, do not chase


def realistic_entry_limit(bid, ask, atr=None, ma20=None) -> dict:
    """
    Limit price for a Judge-approved BUY on the real-money book, from a LIVE
    quote. Never above the ask, never more than 0.5% under it.

    Replaces Hard Rule 2's price formula for this book only (v6.0.0). It does
    not replace Hard Rule 2's intent -- do not chase -- which the old formula
    enforced only by accident: a limit far under the market simply never
    filled. With realistic prices that protection is gone, so it is checked
    explicitly: ask more than 3% above MA20 -> ok=False, place nothing and
    wait for the pullback (Entry Checklist #1). A missing MA20 fails closed
    for the same reason.

    The discount is a quarter of one ATR (a pullback well inside a normal
    day's range), clamped to 0.1%-0.5% of the ask, and rounded DOWN to the
    cent so it can only be cheaper than computed. No ATR -> the old 0.3%.

    Returns {"ok", "limit_price", "discount_pct", "basis", "reason"}. Places
    nothing and calls no MCP tool; the caller passes ok rows into
    build_buy_candidates() and journals the not-ok ones.
    """
    def no(reason):
        return {"ok": False, "limit_price": None, "discount_pct": None,
                "basis": None, "reason": reason}

    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return no(f"unusable quote bid={bid!r} ask={ask!r}")
    if bid <= 0 or ask <= 0 or ask < bid:
        return no(f"unusable quote bid={bid} ask={ask}")

    try:
        ma20 = float(ma20)
    except (TypeError, ValueError):
        ma20 = 0.0
    if ma20 <= 0:
        return no("MA20 unknown -- cannot verify this is not a chase (fail closed)")
    above = round(ask / ma20 - 1, 9)    # 103/100-1 is 0.03000000000000003 in floats
    if above > CHASE_CEILING_ABOVE_MA20:
        return no(f"ask ${ask:.2f} is {above:.1%} above MA20 ${ma20:.2f} "
                  f"(> {CHASE_CEILING_ABOVE_MA20:.0%}): wait for a pullback, do not chase")

    try:
        atr = float(atr)
    except (TypeError, ValueError):
        atr = 0.0
    if atr > 0:
        raw = REALISTIC_LIMIT_ATR_FRACTION * atr / ask
        discount = min(max(raw, REALISTIC_LIMIT_MIN_DISCOUNT), REALISTIC_LIMIT_MAX_DISCOUNT)
        basis = f"{REALISTIC_LIMIT_ATR_FRACTION:.0%} of ATR ${atr:.2f}, clamped"
    else:
        discount = REALISTIC_LIMIT_FALLBACK_DISCOUNT
        basis = "ATR unknown, fallback ask x 0.997"

    limit = math.floor(round(ask * (1 - discount) * 100, 6)) / 100
    return {"ok": True, "limit_price": limit,
            "discount_pct": round((ask - limit) / ask * 100, 3),
            "basis": basis, "reason": ""}


# ── sizing — same formulas as trade.py, state always explicit ────────────────

def honest_equity(account: dict, positions: list, state: dict) -> float:
    """Same formula as trade.honest_portfolio_value, but never defaults state."""
    reported = float(account.get("portfolio_value", 0) or 0)
    excess = sum(f.get("phantom_amount", 0.0)
                 for f in trade.detect_phantom_equity(positions, state))
    return max(0.0, reported - excess)


def max_shares_for(account: dict, positions: list, state: dict,
                    limit_price: float, pos_pct: float) -> tuple:
    """Same formula as trade.calculate_max_shares, but never defaults state."""
    if limit_price <= 0:
        return 0, f"Invalid limit price ${limit_price}"

    portfolio_value = honest_equity(account, positions, state)
    min_cash = portfolio_value * CASH_RESERVE_PCT
    current_cash = float(account["cash"])
    available = min(current_cash, float(account.get("buying_power", current_cash)))
    deployable_cash = available - min_cash
    max_by_pct = portfolio_value * pos_pct

    budget = min(deployable_cash, max_by_pct)
    if budget <= 0:
        return 0, (f"Insufficient deployable cash: ${deployable_cash:.2f} "
                   f"(reserve floor: ${min_cash:.2f})")

    shares = math.floor(budget / limit_price)
    if shares == 0:
        return 0, f"Budget ${budget:.2f} buys 0 whole shares at ${limit_price:.2f}"
    return shares, (f"Budget ({pos_pct:.0%} cap): ${budget:.2f} -> {shares} shares "
                     f"@ ${limit_price:.2f}")


# ── candidate building ─────────────────────────────────────────────────────────

OPEN_ORDER_STATUSES = {
    "queued", "new", "accepted", "pending_new", "open", "pending",
    "partially_filled", "confirmed", "unconfirmed", "pending_approval",
}

PENDING_APPROVAL_STATE = "APPROVAL_STATE_PENDING"


def approvals_to_open_orders(approvals) -> list:
    """
    Map `get_trade_approvals` items into the open-order shape every function
    here already consumes, so an order still waiting on the human's tap counts
    as open.

    Confirmed live, 2026-10-30: `place_equity_order` on an account with Trade
    Approval ON creates no order at all -- it returns an approval
    (APPROVAL_STATE_PENDING), and `get_equity_orders` stays empty until the
    human approves. A pending approval is therefore neither a position nor an
    open order, so without this mapping `reconcile_fills()` /
    `reconcile_sell_fills()` classify it `expired` and drop its pending record
    (losing judge_score and the cooldown/min-hold bookkeeping before the fill
    ever lands), and the duplicate checks in `build_buy_candidates()` /
    `build_sell_candidates()` can't see it, so a later tick could queue a
    second order for the same symbol.

    Only pending equity approvals map. Declined and expired ones must NOT --
    they are resolved, and reconcile correctly drops those as `expired`. An
    approved one has become a real order and shows up in `get_equity_orders`.

    Pass the result of the MCP call even when it is empty ([] is "nothing
    pending"). `None` raises: a failed fetch passed through as "no approvals"
    would silently reintroduce this bug, so the caller must skip the tick's
    reconcile and order placement instead (fail closed).
    """
    if approvals is None:
        raise ValueError(
            "approvals is None -- the get_trade_approvals fetch failed or was "
            "skipped. Pass [] only when it succeeded with nothing pending; "
            "otherwise skip this tick's reconcile and order placement.")
    out = []
    for a in approvals:
        if a.get("state") != PENDING_APPROVAL_STATE:
            continue
        order = a.get("proposed_order") or {}
        if order.get("asset_class", "ASSET_CLASS_EQUITY") != "ASSET_CLASS_EQUITY":
            continue
        if not order.get("symbol"):
            continue
        out.append({
            "symbol": order["symbol"],
            "side": str(order.get("side", "")).lower(),
            "status": "pending_approval",
            "order_id": a.get("approval_id"),
            "source": "trade_approval",
        })
    return out


def build_buy_candidates(judge_approvals: list, account: dict, positions: list,
                          state: dict, watchlist: dict, open_orders: list = None,
                          macro_regime: str = "NEUTRAL",
                          vix_size_multiplier: float = 1.0) -> list:
    """
    Turn today's Judge APPROVE rows into sized, guardrail-checked candidates.

    Each row in judge_approvals must already carry a `limit_price` computed
    per Hard Rule 2 (MA20 x 0.995 pullback, or ask x 0.997 value entry) from
    research.json plus a live Robinhood quote -- that price discovery is the
    interactive agent's job, not this function's, since it needs a live quote
    this module has no way to fetch.

    `open_orders` must be passed on every call once Trade Approval removes
    chat confirmation (v3.0.0) and scheduling removes human presence
    (v4.0.0): a BUY order sitting unapproved in Robinhood's Trade Approval
    queue is not yet a position, so `positions` alone misses it, and a
    re-run ~30 minutes later (v4.0.0's own cadence) would queue a second
    order for the same symbol -- a double fill if both ever get tapped
    approved. This is the same bug class v1.5.1 fixed on the Alpaca side
    (`open_buy_order_syms`); unlike that fix, no client_order_id prefix
    match is needed here, because the Robinhood MCP account is dedicated
    solely to this agent (confirmed: the only tradable account is the "MCP"
    account) -- every order on it is this pipeline's own.

    Returns a list of dicts. Places no orders, calls no MCP tool -- every
    entry is either {"action": "SKIP", "reasons": [...]} or a sized
    {"action": "BUY", ...} ready to be shown via confirmation_prompt().
    """
    open_syms = {p["symbol"] for p in _open_positions(positions)}
    open_orders = open_orders or []
    pending_buy_syms = {
        o.get("symbol") for o in open_orders
        if o.get("side") == "buy"
        and str(o.get("status", "")).lower() in OPEN_ORDER_STATUSES
    }
    candidates = []

    for row in judge_approvals:
        sym = row["symbol"]
        reasons_skip = []

        buyable, why = trade.is_buyable(sym, watchlist)
        if not buyable:
            reasons_skip.append(why)

        if sym in open_syms:
            reasons_skip.append(f"{sym} already held -- no averaging down in this book yet")

        if sym in pending_buy_syms:
            reasons_skip.append(
                f"{sym} already has a pending buy order (placed, awaiting Trade "
                f"Approval or fill) -- not stacking a second one")

        cooldown = trade.cooldown_remaining_hours(sym, state)
        if cooldown > 0:
            reasons_skip.append(f"{sym} in cooldown for {cooldown:.1f} more hours")

        limit_price = row.get("limit_price")
        if not limit_price or limit_price < MIN_TRADE_PRICE:
            reasons_skip.append(
                f"limit price {limit_price!r} missing or below ${MIN_TRADE_PRICE:.2f} floor")

        if row.get("signal") not in ("STRONG_BUY", "WEAK_BUY"):
            reasons_skip.append(f"signal {row.get('signal')} is not a buy signal")

        if macro_regime == "RISK_OFF" and row.get("signal") != "STRONG_BUY":
            reasons_skip.append("RISK_OFF regime requires STRONG_BUY only (Dalio check)")

        if reasons_skip:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": reasons_skip})
            continue

        conviction_pct = trade.allocation_cap_for(sym, watchlist, row.get("judge_score"))
        pos_pct, notes = trade.regime_adjusted_position_pct(
            conviction_pct, macro_regime, vix_size_multiplier)
        shares, size_note = max_shares_for(account, positions, state, limit_price, pos_pct)

        if shares <= 0:
            candidates.append({"symbol": sym, "action": "SKIP", "reasons": [size_note]})
            continue

        tier = trade.tier_for(sym)
        stop_pct = trade.stop_pct_for(sym)
        candidates.append({
            "symbol": sym,
            "action": "BUY",
            "qty": shares,
            "limit_price": limit_price,
            "judge_score": row.get("judge_score"),
            "tier": tier,
            "stop_price": round(limit_price * (1 + stop_pct), 2),
            "position_pct": pos_pct,
            "sizing_notes": notes + [size_note],
        })

    return candidates


def check_stops(positions: list, state: dict) -> dict:
    """Thin wrapper over trade.py's stop-loss and inverse-ETF-hold checks,
    always with this book's own state. Returns candidates to SELL, not an
    executed action -- still goes through the confirmation protocol.

    Once Hard Rule 7's T1 partial exit has fired for a position (tracked in
    state["entries"][symbol]["t1_taken"] by record_partial_exit()), the
    effective stop becomes breakeven -- current_price <= entry_price --
    replacing the tier's normal stop_pct, per "move stop to breakeven on
    remainder." trade.check_stop_loss() has no notion of this book's T1
    tracking, so the override is applied here rather than there: the paper
    book expresses the same rule via a literal broker-side trailing stop set
    at OCO placement time (trade.place_oco_exits), an order type this book
    cannot assume Robinhood's MCP tools support, so it re-derives the same
    intent from state instead. Breakeven is strictly tighter than any tier
    stop for a profitable entry, so a T1-taken position is checked against
    breakeven only, never double-counted against the (looser) tier stop.
    """
    positions = _open_positions(positions)
    entries = state.get("entries", {})
    breakeven_syms = set()
    breakeven_triggers = []
    for pos in positions:
        sym = pos["symbol"]
        entry_record = entries.get(sym, {})
        if not entry_record.get("t1_taken") or not entry_record.get("entry_price"):
            continue
        breakeven_syms.add(sym)
        entry_price = float(entry_record["entry_price"])
        current_price = float(pos.get("current_price", 0))
        if entry_price > 0 and current_price <= entry_price:
            breakeven_triggers.append({
                "symbol": sym, "qty": pos["qty"],
                "avg_entry": pos.get("avg_entry_price"),
                "current_price": current_price,
                "unrealized_plpc": float(pos.get("unrealized_plpc", 0)),
                "tier": trade.tier_for(sym), "stop_pct": None,
                "reason": (f"STOP-LOSS (breakeven after T1): {sym} at or below "
                           f"entry ${entry_price:.2f}"),
            })

    tier_triggers = [t for t in trade.check_stop_loss(positions, state)
                      if t["symbol"] not in breakeven_syms]

    return {
        "stop_losses": tier_triggers + breakeven_triggers,
        "inverse_etf_max_hold": trade.check_inverse_etf_max_hold(positions, state),
    }


def phantom_audit(positions: list, state: dict) -> list:
    """Hard Rule 11, this book's own state. A flagged symbol is blocked from
    trading regardless of which book it's in."""
    return trade.detect_phantom_equity(positions, state)


def confirmation_prompt(candidate: dict) -> str:
    """
    The exact line the Mandatory Confirmation Protocol requires, built in one
    place so it is never phrased differently from one session to the next.
    Valid for action in {"BUY", "SELL_FULL", "SELL_PARTIAL"}; a SKIP
    candidate is never shown to the user as something to confirm.

    As of the Robinhood sell-side automation, this is logged as an audit
    record, not a prompt waiting on a reply, for the same reason step 8's
    buys are: Trade Approval substitutes for chat confirmation on every
    order built through a documented, guardrail-vetted pipeline.
    """
    action = candidate.get("action")
    if action not in ("BUY", "SELL_FULL", "SELL_PARTIAL"):
        raise ValueError(
            "confirmation_prompt() is only for BUY/SELL_FULL/SELL_PARTIAL candidates")
    c = candidate
    if action == "BUY":
        return (
            "This is a REAL MONEY trade on Robinhood. Confirm?\n"
            f"  BUY {c['qty']} {c['symbol']} @ ${c['limit_price']:.2f} limit "
            f"(Judge {c.get('judge_score')}, {c['tier']}, stop ${c['stop_price']:.2f})"
        )
    verb = "SELL (full exit)" if action == "SELL_FULL" else f"SELL (partial, {c.get('target')})"
    return (
        "This is a REAL MONEY trade on Robinhood. Confirm?\n"
        f"  {verb} {c['qty']} {c['symbol']} @ ${c['limit_price']:.2f} limit -- {c['reason']}"
    )


# ── journal / trade-log writing (private repo only) ───────────────────────────

def render_journal_entry(date: str, account: dict, positions: list, state: dict,
                          fills_today: list = None, stop_losses: list = None,
                          phantom_flags: list = None, skipped: list = None,
                          reflection: dict = None) -> str:
    """
    Build the day's real-money journal markdown, following the same template
    and section order as journal-template.md / journal.py's write_eod_journal
    — same shape, deliberately not account-agnostic research (that's already
    in the public repo's logs/paper/{date}.md for the same day; duplicating
    it here would be two copies of the same Market Research section drifting
    apart, exactly the thing this repo's "Doc/code drift is a real bug, not a
    typo" lesson warns about).

    Pure string-building only — takes already-fetched/already-decided data,
    writes nothing. Pair with write_journal_file() to persist it.
    """
    fills_today   = fills_today or []
    stop_losses   = stop_losses or []
    phantom_flags = phantom_flags or []
    skipped       = skipped or []
    reflection    = reflection or {}

    cash          = float(account.get("cash", 0))
    portfolio_val = float(account.get("portfolio_value", 0))
    honest_val    = honest_equity(account, positions, state)
    reserve_pct   = (cash / honest_val * 100) if honest_val else 0.0

    positions_section = ""
    if positions:
        for p in positions:
            pl_pct = float(p.get("unrealized_plpc", 0)) * 100
            positions_section += (
                f"- {p['symbol']}: {p['qty']} shares @ ${float(p['avg_entry_price']):.2f} "
                f"| Current: ${float(p['current_price']):.2f} | P&L: {pl_pct:+.2f}%\n")
    else:
        positions_section = "- No open positions\n"

    trades_table = "| Time | Symbol | Action | Qty | Price | Reason |\n|------|--------|--------|-----|-------|--------|\n"
    if fills_today:
        for f in fills_today:
            trades_table += (
                f"| {f.get('time', '')} | {f['symbol']} | {f['side'].upper()} | "
                f"{f['qty']} | ${float(f['filled_price']):.2f} | {f.get('reason', '')} |\n")
    else:
        trades_table += "| — | — | No trades executed today | — | — | — |\n"

    stops_section = "## Stop-Losses\nNone triggered today.\n"
    if stop_losses:
        stops_section = "## ⚠️ Stop-Losses Triggered\n"
        for sl in stop_losses:
            stops_section += f"- **{sl['symbol']}** ({sl['tier']}): {sl['reason']}\n"

    phantom_section = ""
    if phantom_flags:
        phantom_section = "\n## ⚠️ Phantom Equity Flags (Hard Rule 11)\n"
        for p in phantom_flags:
            phantom_section += f"- **{p['symbol']}**: {p['action']}\n"
            for r in p["reasons"]:
                phantom_section += f"  - {r}\n"

    skipped_section = ""
    if skipped:
        skipped_section = "\n## Candidates Considered, Not Taken\n"
        for s in skipped:
            skipped_section += f"- **{s['symbol']}**: {'; '.join(s['reasons'])}\n"

    return f"""# Real-Money Trade Journal — {date}
*Generated: {et_timestamp()}*

Research and Judge scoring are account-agnostic and shared with the paper
book — see `logs/paper/{date}.md` in the public `ai-trading-agent` repo for
that day's Market Research and Judge Summary sections. This journal covers
this book's own account state, fills, and decisions only.

---

## Portfolio Status
- **Cash**: ${cash:,.2f}
- **Portfolio Value (reported)**: ${portfolio_val:,.2f}
- **Portfolio Value (honest — phantom equity at cost)**: ${honest_val:,.2f}
- **Cash Reserve**: {reserve_pct:.1f}% of honest equity (target: ≥20% — Hard Rule 8)

### Open Positions
{positions_section}
---

## Trades Executed
{trades_table}
---

{stops_section}{phantom_section}{skipped_section}
---

## End-of-Day Reflection

### What Worked
{reflection.get('what_worked', '- ')}

### What I'm Uncertain About
{reflection.get('uncertain', '- ')}

### Lessons / Pattern Observations
{reflection.get('lessons', '- ')}

### Watch List for Tomorrow
{reflection.get('watch_list', '- ')}
"""


def write_journal_file(logs_dir: str, date: str, markdown: str) -> str:
    """Persist a rendered journal entry to the private repo. Returns the path."""
    logs_dir = _require_logs_dir(logs_dir)
    path = os.path.join(logs_dir, f"{date}.md")
    with open(path, "w") as f:
        f.write(markdown)
    return path


def append_fill(logs_dir: str, fill: dict) -> None:
    """
    Append one already-confirmed, already-placed fill to the private repo's
    trade-log.csv. `fill` must reflect a real order result returned by the
    Robinhood MCP tool after the user confirmed it -- this function records
    a trade that already happened, it never places one.
    """
    logs_dir = _require_logs_dir(logs_dir)
    path = os.path.join(logs_dir, "trade-log.csv")
    new_file = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_LOG_HEADER)
        if new_file:
            writer.writeheader()
        writer.writerow({k: fill.get(k, "") for k in TRADE_LOG_HEADER})
