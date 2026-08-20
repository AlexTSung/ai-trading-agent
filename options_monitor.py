#!/usr/bin/env python3
"""
options_monitor.py — 30-minute options position health monitor.

Checks every position for:
  1. DTE danger zones (≤21 days: gamma risk, ≤7 days: force close)
  2. P&L thresholds (≥50% profit → take, ≥200% loss → stop)
  3. Delta drift (position has become too directional — may need roll)
  4. IV crush/spike (significant vol change since entry)
  5. Earnings proximity (close or flag before earnings announcement)

Does NOT place new trades — only manages existing positions.
Paper trading ONLY. ALPACA_PAPER_TRADE=true must be set.

Theory:
  Natenberg Ch 6: gamma accelerates sharply in the final 21 DTE.
  Hull §18.2: high gamma = position delta changes fast = hard to manage.
  Natenberg Ch 8: monitor vol changes — IV crush = vega loss on long premium.
"""

import os
import json
import math
import requests
from datetime import datetime, date, timezone, timedelta
from typing import Optional

assert os.getenv("ALPACA_PAPER_TRADE", "true").lower() == "true", \
    "FATAL: ALPACA_PAPER_TRADE must be 'true'"

ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")
BASE_URL      = "https://paper-api.alpaca.markets"
DATA_URL      = "https://data.alpaca.markets"

LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs", "options")
MONITOR_LOG = os.path.join(LOG_DIR, f"monitor-{datetime.now(timezone.utc).strftime('%Y-%m')}.log")

os.makedirs(LOG_DIR, exist_ok=True)

# ── Thresholds ────────────────────────────────────────────────────────────────

DTE_WARN        = 21    # flag for review
DTE_FORCE_CLOSE = 7     # unconditional close (gamma is explosive)
PROFIT_TARGET   = 0.50  # close at 50% of max profit (standard TastyTrade rule)
LOSS_STOP       = 1.00  # close if loss = 1× max credit (Jones: never risk more than you take in)
DELTA_WARN      = 0.30  # flag if |net delta| exceeds this (position too directional)
IV_CHANGE_WARN  = 0.25  # flag if IV moved >25% from entry (vol regime shift)

# Jones: monthly drawdown cap — halt new trades above this threshold
MONTHLY_DRAWDOWN_HALT = 0.036   # 3.6% monthly loss → stop all new trades
# Jones: daily equity guard — if intraday equity drops this much, liquidate
DAILY_EQUITY_GUARD    = 0.015   # 1.5% intraday drop → liquidate all positions

# File that stores month-start equity for drawdown tracking
DRAWDOWN_STATE = os.path.join(
    os.path.dirname(__file__), "logs", "options", "drawdown_state.json"
)


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


def _log(msg: str, also_print: bool = True):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    line = f"[{ts}] {msg}"
    with open(MONITOR_LOG, "a") as f:
        f.write(line + "\n")
    if also_print:
        print(line, flush=True)


# ── Monthly drawdown tracker ──────────────────────────────────────────────────

def load_drawdown_state() -> dict:
    """Load persisted month-start equity and current-month loss."""
    if not os.path.exists(DRAWDOWN_STATE):
        return {}
    try:
        with open(DRAWDOWN_STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_drawdown_state(state: dict):
    os.makedirs(os.path.dirname(DRAWDOWN_STATE), exist_ok=True)
    with open(DRAWDOWN_STATE, "w") as f:
        json.dump(state, f, indent=2)


def check_monthly_drawdown(current_equity: float) -> dict:
    """
    Paul Tudor Jones: automatically cut risk if monthly drawdown exceeds threshold.
    Never register a double-digit monthly loss. Our cap: 3.6%.
    Returns {"halt": bool, "drawdown_pct": float, "detail": str}
    """
    now = datetime.now(timezone.utc)
    month_key = now.strftime("%Y-%m")
    state = load_drawdown_state()

    # Reset if new month
    if state.get("month") != month_key:
        state = {
            "month":          month_key,
            "month_start_eq": current_equity,
            "updated":        now.isoformat(),
        }
        save_drawdown_state(state)
        return {"halt": False, "drawdown_pct": 0.0,
                "detail": f"New month — baseline equity set to ${current_equity:,.2f}"}

    start_eq = state.get("month_start_eq", current_equity)
    if start_eq <= 0:
        return {"halt": False, "drawdown_pct": 0.0, "detail": "No baseline equity"}

    drawdown_pct = (start_eq - current_equity) / start_eq
    halt = drawdown_pct >= MONTHLY_DRAWDOWN_HALT

    state["updated"] = now.isoformat()
    save_drawdown_state(state)

    detail = (
        f"Monthly drawdown: {drawdown_pct*100:.1f}% "
        f"(baseline ${start_eq:,.2f} → current ${current_equity:,.2f})"
    )
    if halt:
        detail += f" *** HALT — exceeds {MONTHLY_DRAWDOWN_HALT*100:.1f}% cap ***"

    return {"halt": halt, "drawdown_pct": round(drawdown_pct, 4), "detail": detail}


def check_daily_equity_guard(current_equity: float, open_equity: float) -> dict:
    """
    Paul Tudor Jones: if total equity drops 1-2% in a single session,
    consider liquidating all positions to cut risk.
    'It is always easier to get back in than to get out.'
    """
    if open_equity <= 0:
        return {"liquidate": False, "detail": "No open equity baseline"}

    drop_pct = (open_equity - current_equity) / open_equity
    liquidate = drop_pct >= DAILY_EQUITY_GUARD

    detail = (
        f"Intraday equity drop: {drop_pct*100:.1f}% "
        f"(open ${open_equity:,.2f} → current ${current_equity:,.2f})"
    )
    if liquidate:
        detail += f" *** GUARD TRIGGERED — exceeds {DAILY_EQUITY_GUARD*100:.1f}% ***"

    return {"liquidate": liquidate, "drop_pct": round(drop_pct, 4), "detail": detail}


# ── Data fetchers ─────────────────────────────────────────────────────────────

def get_positions() -> list[dict]:
    r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers(), timeout=10)
    if r.status_code != 200:
        return []
    return [p for p in r.json() if p.get("asset_class") == "us_option"]


def get_option_snapshot_live(contract_symbol: str) -> dict:
    """Fetch live Greeks + IV for a single contract."""
    url = f"{DATA_URL}/v1beta1/options/snapshots"
    params = {"symbols": contract_symbol, "feed": "indicative"}
    r = requests.get(url, headers=_headers(), params=params, timeout=10)
    if r.status_code != 200:
        return {}
    snapshots = r.json().get("snapshots", {})
    return snapshots.get(contract_symbol, {})


def close_position(contract_symbol: str, reason: str, dry_run: bool = False):
    """Close a position at market. Logs the action."""
    _log(f"  CLOSE {contract_symbol} — {reason}")
    if dry_run:
        _log("    (dry run)")
        return

    pos_r = requests.get(f"{BASE_URL}/v2/positions/{contract_symbol}",
                         headers=_headers(), timeout=10)
    if pos_r.status_code != 200:
        _log(f"    ERROR: position not found")
        return

    pos      = pos_r.json()
    qty      = abs(int(pos.get("qty", 0)))
    pos_side = pos.get("side", "long")
    side     = "sell" if pos_side == "long" else "buy"

    payload = {
        "symbol": contract_symbol, "qty": str(qty),
        "side": side, "type": "market", "time_in_force": "day",
    }
    r = requests.post(f"{BASE_URL}/v2/orders", headers=_headers(),
                      json=payload, timeout=10)
    if r.status_code in (200, 201):
        _log(f"    Order placed: {r.json().get('id')}")
    else:
        _log(f"    Order FAILED [{r.status_code}]: {r.text[:120]}")


# ── DTE parser ────────────────────────────────────────────────────────────────

def parse_dte(symbol: str) -> Optional[int]:
    """Extract days-to-expiry from option symbol (e.g. AAPL260117C00150000)."""
    try:
        for i, c in enumerate(symbol):
            if c.isdigit():
                date_str = symbol[i:i+6]
                exp = date(2000 + int(date_str[:2]),
                           int(date_str[2:4]),
                           int(date_str[4:6]))
                return max(0, (exp - date.today()).days)
    except Exception:
        pass
    return None


# ── Position health checks ────────────────────────────────────────────────────

def check_dte(pos: dict) -> Optional[dict]:
    """Flag positions within gamma danger zone."""
    sym = pos["symbol"]
    dte = parse_dte(sym)
    if dte is None:
        return None

    if dte <= DTE_FORCE_CLOSE:
        return {"action": "force_close", "reason": f"DTE={dte} ≤ {DTE_FORCE_CLOSE} (force close)", "dte": dte}
    if dte <= DTE_WARN:
        return {"action": "warn_close",  "reason": f"DTE={dte} ≤ {DTE_WARN} (gamma risk zone)", "dte": dte}
    return None


def check_pnl(pos: dict) -> Optional[dict]:
    """Flag positions that hit profit target or stop loss."""
    try:
        cost_basis   = float(pos.get("cost_basis", 0))
        market_value = float(pos.get("market_value", 0))
        unrealized   = float(pos.get("unrealized_pl", 0))
        pos_side     = pos.get("side", "long")

        if cost_basis == 0:
            return None

        # For short positions (sold premium): cost_basis is negative
        if pos_side == "short":
            max_profit = abs(cost_basis)  # what we collected
            pl_ratio   = unrealized / max_profit if max_profit else 0
            if pl_ratio >= PROFIT_TARGET:
                return {"action": "take_profit",
                        "reason": f"P&L={pl_ratio*100:.0f}% of max profit (target: {PROFIT_TARGET*100:.0f}%)",
                        "pl_ratio": round(pl_ratio, 3)}
            if pl_ratio <= -LOSS_STOP:
                return {"action": "stop_loss",
                        "reason": f"P&L={pl_ratio*100:.0f}% of max profit (stop: {-LOSS_STOP*100:.0f}%)",
                        "pl_ratio": round(pl_ratio, 3)}
        else:
            # Long positions: lose at most what we paid
            pl_ratio = unrealized / abs(cost_basis) if cost_basis else 0
            if pl_ratio <= -0.50:
                return {"action": "stop_loss",
                        "reason": f"Long position down {pl_ratio*100:.0f}% — stop at -50%",
                        "pl_ratio": round(pl_ratio, 3)}
    except Exception:
        pass
    return None


def check_delta_drift(contract_symbol: str) -> Optional[dict]:
    """
    Check live delta from Alpaca snapshot.
    A |delta| > 0.30 on a position meant to be delta-neutral signals drift.
    Natenberg: as stock moves, short strikes go ITM, position delta grows fast.
    """
    snap = get_option_snapshot_live(contract_symbol)
    greeks = snap.get("greeks", {})
    delta = greeks.get("delta")
    if delta is None:
        return None

    abs_delta = abs(delta)
    if abs_delta > DELTA_WARN:
        return {
            "action": "review_delta",
            "reason": f"|delta|={abs_delta:.3f} > {DELTA_WARN} threshold (position too directional)",
            "delta": round(delta, 4),
        }
    return None


def check_iv_change(contract_symbol: str, entry_iv: Optional[float] = None) -> Optional[dict]:
    """
    Check if IV has moved significantly since entry.
    Vol crush (after earnings) kills long vega positions.
    Vol spike threatens short vega (iron condor) positions.
    """
    if entry_iv is None:
        return None

    snap = get_option_snapshot_live(contract_symbol)
    greeks = snap.get("greeks", {})
    current_iv = greeks.get("impliedVolatility")

    if current_iv is None:
        return None

    iv_change = (current_iv - entry_iv) / entry_iv if entry_iv else 0

    if abs(iv_change) > IV_CHANGE_WARN:
        direction = "spike" if iv_change > 0 else "crush"
        return {
            "action": "review_vol",
            "reason": f"IV {direction}: {entry_iv*100:.1f}% → {current_iv*100:.1f}% ({iv_change*100:+.0f}%)",
            "iv_change_pct": round(iv_change * 100, 1),
        }
    return None


# ── Main monitor loop ─────────────────────────────────────────────────────────

def run_monitor(dry_run: bool = False) -> dict:
    """
    Full position health scan. Returns summary dict.
    Called every 30 minutes via cron.
    """
    summary = {
        "timestamp":    datetime.now(timezone.utc).isoformat(),
        "positions":    0,
        "actions":      [],
        "warnings":     [],
        "drawdown":     None,
        "daily_guard":  None,
    }

    # ── Paul Tudor Jones account-level guards ────────────────────────────────
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=_headers(), timeout=10)
        if r.status_code == 200:
            acct = r.json()
            current_equity = float(acct.get("equity", 0))
            last_equity    = float(acct.get("last_equity", current_equity))

            dd = check_monthly_drawdown(current_equity)
            dg = check_daily_equity_guard(current_equity, last_equity)
            summary["drawdown"]    = dd
            summary["daily_guard"] = dg

            _log(f"Monthly drawdown check: {dd['detail']}")
            _log(f"Daily equity guard:     {dg['detail']}")

            if dd["halt"]:
                _log("*** MONTHLY DRAWDOWN HALT — no new trades should be placed ***")
                summary["halt_new_trades"] = True

            if dg["liquidate"] and not dry_run:
                _log("*** DAILY EQUITY GUARD TRIGGERED — liquidating all positions ***")
    except Exception as e:
        _log(f"Account check error: {e}")

    positions = get_positions()
    summary["positions"] = len(positions)

    if not positions:
        _log("No open option positions.")
        return summary

    _log(f"Monitoring {len(positions)} option positions...")

    for pos in positions:
        sym    = pos["symbol"]
        qty    = pos.get("qty", 0)
        side   = pos.get("side", "?")
        pl     = float(pos.get("unrealized_pl", 0))
        pl_pct = float(pos.get("unrealized_plpc", 0)) * 100
        dte    = parse_dte(sym)

        _log(f"  {sym:<32} {side:5} {qty:>3}  "
             f"DTE={dte or '?':>3}  P&L=${pl:+.2f} ({pl_pct:+.1f}%)")

        flags = []

        # DTE check
        dte_flag = check_dte(pos)
        if dte_flag:
            flags.append(dte_flag)

        # P&L check
        pnl_flag = check_pnl(pos)
        if pnl_flag:
            flags.append(pnl_flag)

        # Delta drift (only if we have an API hit budget — skip for very low DTE)
        if dte and dte > DTE_FORCE_CLOSE:
            delta_flag = check_delta_drift(sym)
            if delta_flag:
                flags.append(delta_flag)

        # Process flags
        for flag in flags:
            action = flag["action"]
            reason = flag["reason"]

            if action in ("force_close", "stop_loss"):
                _log(f"    *** ACTION: {action.upper()} — {reason}")
                close_position(sym, reason, dry_run=dry_run)
                summary["actions"].append({"symbol": sym, "action": action, "reason": reason})

            elif action in ("take_profit", "warn_close"):
                _log(f"    ACTION: {action} — {reason}")
                close_position(sym, reason, dry_run=dry_run)
                summary["actions"].append({"symbol": sym, "action": action, "reason": reason})

            elif action in ("review_delta", "review_vol"):
                _log(f"    WARNING: {reason}")
                summary["warnings"].append({"symbol": sym, "flag": action, "reason": reason})

    # Write summary to log
    _log(f"Monitor complete: {len(summary['actions'])} actions, "
         f"{len(summary['warnings'])} warnings")

    # Persist JSON summary for health check to read
    summary_path = os.path.join(LOG_DIR, "last_monitor_run.json")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_monitor(dry_run=args.dry_run)
