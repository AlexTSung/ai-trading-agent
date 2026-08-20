#!/usr/bin/env python3
"""
run_options.py — Options trading orchestrator for Alpaca paper account.

Pipeline:
  1. Screen watchlist via options_screener (IV rank extremes)
  2. Select strategy via options_strategy (Natenberg Ch 8 decision matrix)
  3. Validate via passes_risk_filters (risk gate)
  4. Resolve contracts via find_strategy_contracts (Alpaca contract lookup)
  5. Execute via execute_strategy (paper order placement)
  6. Journal all trades + manage existing positions

Usage:
  python run_options.py                   # full scan + trade
  python run_options.py --dry-run         # scan + strategy but no orders
  python run_options.py --manage-only     # only manage existing positions
  python run_options.py --scan-only       # print opportunities, no trade

Paper trading ONLY. ALPACA_PAPER_TRADE=true is asserted at import of options_trade.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone

# ── Safety assertion (belt + suspenders) ─────────────────────────────────────
assert os.getenv("ALPACA_PAPER_TRADE", "true").lower() == "true", \
    "FATAL: ALPACA_PAPER_TRADE must be 'true'"

from options_screener    import scan_universe
from robinhood_screener  import (
    build_screen_from_robinhood_quote, scan_universe_robinhood,
    build_iron_condor_strikes, build_butterfly_strikes, format_screen_result,
)
from options_strategy import select_strategy, passes_risk_filters, scenario_analysis
from options_trade    import (
    get_equity, find_strategy_contracts, execute_strategy,
    log_options_trade, check_positions_for_management, close_option_position,
    get_option_positions,
)
from options_monitor  import check_monthly_drawdown, load_drawdown_state

# ── Config ────────────────────────────────────────────────────────────────────

WATCHLIST = [
    # High-liquidity underlyings — tight bid-ask, ample open interest
    "SPY", "QQQ", "IWM", "GLD", "TLT",         # ETFs
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL",   # Mega-cap tech
    "META", "TSLA", "AMD", "NFLX", "COIN",      # High-vol names
    "JPM", "BAC", "GS",                          # Financials
]

MAX_POSITIONS     = 5        # cap concurrent open options positions
MAX_SCANNED       = 15       # symbols to evaluate per run (rate-limit conscious)
TOP_N_CANDIDATES  = 5        # top screener candidates to consider
DRY_RUN_DEFAULT   = False    # flip to True to default to paper-scan only

LOG_DIR  = os.path.join(os.path.dirname(__file__), "logs", "options")
JOURNAL  = os.path.join(LOG_DIR, f"trades-{datetime.now(timezone.utc).strftime('%Y-%m')}.jsonl")

os.makedirs(LOG_DIR, exist_ok=True)


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


# ── Position management ───────────────────────────────────────────────────────

def run_position_management(dry_run: bool = False):
    """
    Check all open options positions and close those that hit management rules.
    Should be called every run (before scanning for new trades).

    Rules (options_trade.check_positions_for_management):
      - DTE ≤ 21: close before gamma risk spikes (Natenberg Ch 6)
      - P&L ≥ 50% max profit: take profit early
      - P&L ≤ -100% max profit: stop loss
    """
    _log("Checking positions for management...")
    flags = check_positions_for_management()

    if not flags:
        _log("  No positions require management.")
        return

    for flag in flags:
        _log(f"  ACTION NEEDED: {flag['symbol']} — {flag['reason']}")
        if not dry_run:
            result = close_option_position(flag["symbol"])
            _log(f"    Close order: {result.get('id', result.get('error', 'unknown'))}")
        else:
            _log("    (dry run — no order placed)")


# ── New trade scan ────────────────────────────────────────────────────────────

def run_scan(dry_run: bool = False, scan_only: bool = False):
    """
    Full pipeline: screen → select → validate → execute.
    """
    # Check position count
    existing = get_option_positions()
    _log(f"Open option positions: {len(existing)}")
    if len(existing) >= MAX_POSITIONS:
        _log(f"At max positions ({MAX_POSITIONS}). Skipping new trades.")
        return

    available_slots = MAX_POSITIONS - len(existing)
    equity = get_equity()
    _log(f"Account equity: ${equity:,.2f}")

    # ── Paul Tudor Jones: monthly drawdown gate ───────────────────────────────
    # If monthly drawdown exceeds 3.6%, halt all new trades for the rest of month.
    # 'The most important rule in trading is: play great defense, not great offense.'
    dd = check_monthly_drawdown(equity)
    _log(f"Monthly drawdown: {dd['detail']}")
    if dd.get("halt"):
        _log("*** MONTHLY DRAWDOWN HALT ACTIVE — no new trades this run ***")
        return

    # ── Ed Seykota: performance-based sizing ─────────────────────────────────
    # Reduce qty when in a losing month; increase when winning.
    # 'Decrease volume when trading poorly; increase when trading well.'
    drawdown_pct = dd.get("drawdown_pct", 0.0)
    if drawdown_pct > 0.02:        # down >2% this month
        trade_qty = 1              # minimum size — Seykota: trim activity during losses
        _log(f"  Sizing reduced to 1 contract (monthly drawdown {drawdown_pct*100:.1f}%)")
    elif drawdown_pct < -0.05:     # UP >5% this month (profitable)
        trade_qty = 2              # can scale up slightly
        _log(f"  Sizing at 2 contracts (monthly gain {-drawdown_pct*100:.1f}%)")
    else:
        trade_qty = 1              # default

    # 1. Screen watchlist
    _log(f"\nScreening {min(len(WATCHLIST), MAX_SCANNED)} symbols for IV rank extremes...")
    symbols_to_scan = WATCHLIST[:MAX_SCANNED]
    candidates = scan_universe(symbols_to_scan, top_n=TOP_N_CANDIDATES)

    if not candidates:
        _log("No qualifying candidates found this run.")
        return

    _log(f"\nTop {len(candidates)} candidates:")
    for c in candidates:
        _log(f"  {c['symbol']:6s} | IVR={c['iv_rank']:.0f} | IVP={c['iv_pct']:.0f} "
             f"| HV30={c['hv30']:.1f}% | signal={c['signal']} | trend={c['trend']}")

    if scan_only:
        return

    # 2. Select strategy + risk filter
    trades_placed = 0
    for candidate in candidates:
        if trades_placed >= available_slots:
            break

        symbol = candidate["symbol"]
        _log(f"\nEvaluating {symbol}...")

        # Build strategy
        strat = select_strategy(candidate)
        if strat is None:
            _log(f"  {symbol}: no strategy selected (neutral IV zone)")
            continue

        _log(f"  Strategy: {strat['strategy']} | "
             f"IVR={strat['iv_rank']:.0f} | DTE={strat['dte']} | "
             f"max_loss=${strat.get('max_loss', '?')} | "
             f"net_credit=${strat.get('net_credit', 'N/A')}")

        # 3. Risk filter
        ok, reason = passes_risk_filters(strat, account_equity=equity)
        if not ok:
            _log(f"  SKIP — risk filter: {reason}")
            continue

        _log(f"  Risk filter: PASS")

        # 3b. Pre-trade scenario analysis (Bielfeldt + Saliba)
        # Know your worst case BEFORE entering. Never skip this step.
        scenarios = scenario_analysis(strat)
        worst = scenarios["worst_case"]
        _log(f"  Scenario analysis: worst={worst['move']} → ${worst['pl']:+.0f}/contract "
             f"| max defined loss ${scenarios['max_defined_loss_per_contract']:.0f}/contract")

        # 4. Resolve to actual Alpaca contracts
        contracts = find_strategy_contracts(strat)
        if not contracts:
            _log(f"  SKIP — could not resolve all legs to active contracts")
            continue

        _log(f"  Resolved {len(contracts)} legs:")
        for leg in contracts:
            _log(f"    {leg['side'].upper()} {leg['leg_type']} "
                 f"K={leg['strike']} → {leg['symbol']}")

        # 5. Execute (use performance-adjusted qty)
        orders = execute_strategy(strat, contracts, qty=trade_qty, dry_run=dry_run)

        # 6. Journal
        log_options_trade(strat, orders, JOURNAL)

        if not dry_run:
            trades_placed += 1
            _log(f"  Trade placed ({trades_placed}/{available_slots})")
        else:
            _log(f"  [DRY RUN] Would have placed trade for {symbol}")

    if trades_placed == 0 and not dry_run:
        _log("\nNo new trades placed this run.")


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_portfolio_summary():
    """Print a quick summary of current options positions."""
    positions = get_option_positions()
    if not positions:
        _log("No open option positions.")
        return

    _log(f"\n{'─'*60}")
    _log(f"OPEN OPTIONS POSITIONS ({len(positions)})")
    _log(f"{'─'*60}")
    total_pl = 0.0
    for pos in positions:
        sym      = pos.get("symbol", "?")
        qty      = pos.get("qty", 0)
        side     = pos.get("side", "?")
        avg_cost = float(pos.get("avg_entry_price", 0))
        curr_val = float(pos.get("market_value", 0))
        unreal   = float(pos.get("unrealized_pl", 0))
        unreal_p = float(pos.get("unrealized_plpc", 0)) * 100
        total_pl += unreal
        _log(f"  {sym:<30} {side:5} {qty:>3}  "
             f"avg={avg_cost:.2f}  P&L=${unreal:+.2f} ({unreal_p:+.1f}%)")
    _log(f"{'─'*60}")
    _log(f"Total unrealized P&L: ${total_pl:+.2f}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Options trading orchestrator (paper)")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Screen + build strategies but do not place orders")
    parser.add_argument("--manage-only", action="store_true",
                        help="Only manage existing positions (close/roll)")
    parser.add_argument("--scan-only",   action="store_true",
                        help="Print screener results only, no strategies or trades")
    parser.add_argument("--summary",     action="store_true",
                        help="Print current positions summary and exit")
    args = parser.parse_args()

    _log("=" * 60)
    _log("OPTIONS TRADING RUN — PAPER ACCOUNT ONLY")
    _log(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE PAPER'}")
    _log("=" * 60)

    if args.summary:
        print_portfolio_summary()
        return

    # Always manage existing positions first
    run_position_management(dry_run=args.dry_run)

    if not args.manage_only:
        run_scan(dry_run=args.dry_run or DRY_RUN_DEFAULT,
                 scan_only=args.scan_only)

    print_portfolio_summary()
    _log("\nRun complete.")


if __name__ == "__main__":
    main()
