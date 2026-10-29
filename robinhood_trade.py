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
from market_time import et_timestamp

CASH_RESERVE_PCT = trade.CASH_RESERVE_PCT   # Hard Rule 8's 20% floor, same number
MIN_TRADE_PRICE  = trade.MIN_TRADE_PRICE

TRADE_LOG_HEADER = ["date", "symbol", "side", "qty", "filled_price", "order_id", "status"]


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


def record_entry(state: dict, symbol: str, judge_score, qty: float) -> None:
    """Mirrors trade.py's post-fill bookkeeping, for this book's own
    cooldown/min-hold tracking. Never touches trade.py's state."""
    state.setdefault("entries", {})[symbol] = {
        "date": datetime.now().isoformat(),
        "judge_score": judge_score,
        "qty": qty,
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
      still working. Leave it alone.

    Returns {"filled": [...], "expired": [...]} for the caller to journal
    and log. Mutates `state` in place (same contract as record_entry).
    """
    pending = state.get("pending", {})
    if not pending:
        return {"filled": [], "expired": []}

    positions_by_symbol = {p["symbol"]: p for p in positions}
    open_order_syms = {o.get("symbol") for o in open_orders}

    filled, expired = [], []
    for symbol in list(pending.keys()):
        info = pending[symbol]
        pos = positions_by_symbol.get(symbol)

        if pos is not None:
            record_entry(state, symbol, info.get("judge_score"), pos["qty"])
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
    "partially_filled", "confirmed", "unconfirmed",
}


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
    open_syms = {p["symbol"] for p in positions}
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
    executed action -- still goes through the confirmation protocol."""
    return {
        "stop_losses": trade.check_stop_loss(positions, state),
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
    Only valid for action == "BUY"; a SKIP candidate is never shown to the
    user as something to confirm.
    """
    if candidate.get("action") != "BUY":
        raise ValueError("confirmation_prompt() is only for BUY candidates")
    c = candidate
    return (
        "This is a REAL MONEY trade on Robinhood. Confirm?\n"
        f"  BUY {c['qty']} {c['symbol']} @ ${c['limit_price']:.2f} limit "
        f"(Judge {c.get('judge_score')}, {c['tier']}, stop ${c['stop_price']:.2f})"
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
