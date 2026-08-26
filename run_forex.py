#!/usr/bin/env python3
"""
run_forex.py — 24/7 orchestration for the Forex trading system.
Runs the full pipeline: scan → research → judge → trade → journal sync.
Designed to run every 4 hours via GitHub Actions or cron.

Usage:
  python run_forex.py                    # Full run (all steps)
  python run_forex.py --dry-run          # Run without placing orders
  python run_forex.py --scan-only        # Stop after screener
  python run_forex.py --judge-only       # Run through judge, no trades
  python run_forex.py --sync-journal     # Sync journal only
  python run_forex.py --status           # Print account + positions + Phase 3 stats
"""

import os
import sys
import json
import time
import argparse
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))

# ── Safety first ──────────────────────────────────────────────────────────────
OANDA_ENV = os.getenv("OANDA_ENV", "practice")
assert OANDA_ENV == "practice", (
    "FATAL: OANDA_ENV must be 'practice'. "
    "Real money trading is locked until Phase 4 graduation."
)


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")
    sys.stdout.flush()


def run_step(label: str, fn, *args, **kwargs):
    log(f">>> {label}")
    try:
        result = fn(*args, **kwargs)
        log(f"    {label} OK")
        return result
    except SystemExit:
        raise
    except Exception as e:
        log(f"    {label} FAILED: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Forex trading orchestrator")
    parser.add_argument("--dry-run",      action="store_true", help="Skip order placement")
    parser.add_argument("--scan-only",    action="store_true", help="Stop after screener")
    parser.add_argument("--judge-only",   action="store_true", help="Run through judge, no trades")
    parser.add_argument("--sync-journal", action="store_true", help="Sync journal only")
    parser.add_argument("--status",       action="store_true", help="Print status and exit")
    args = parser.parse_args()

    log(f"{'='*60}")
    log(f"  FOREX AGENT  —  {date.today().isoformat()}  —  {OANDA_ENV.upper()}")
    log(f"{'='*60}")

    # ── Status mode ────────────────────────────────────────────────────────
    if args.status:
        from forex_trade   import print_positions
        from forex_journal import print_summary, load_journal
        print_positions()
        print_summary(load_journal())
        return

    # ── Journal sync only ──────────────────────────────────────────────────
    if args.sync_journal:
        from forex_journal import sync_closed_trades, print_summary, load_journal
        run_step("Journal sync", sync_closed_trades)
        print_summary(load_journal())
        return

    # ── Step 1: Check session ──────────────────────────────────────────────
    from forex_screener import is_good_session, get_account_summary, _check_auth
    _check_auth()

    acct = get_account_summary()
    if "error" in acct:
        log(f"ABORT: Cannot reach OANDA — {acct['error']}")
        log("Hint: Check that OANDA_API_KEY and OANDA_ACCOUNT_ID secrets are set in GitHub → Settings → Secrets → Actions")
        sys.exit(0)  # exit 0 so GitHub Actions marks as success, not failure
    nav = float(acct.get("nav") or acct.get("account", {}).get("NAV") or 0)
    open_count = acct.get("open_trade_count") or acct.get("account", {}).get("openTradeCount", "?")
    log(f"  Account NAV: ${nav:,.2f}  |  Open positions: {open_count}")

    if not is_good_session():
        log("  Off-session hours. Syncing journal and exiting.")
        from forex_journal import sync_closed_trades
        run_step("Journal sync (off-session)", sync_closed_trades)
        return

    # ── Step 2: Screen all pairs ───────────────────────────────────────────
    from forex_screener import scan_all, MAJOR_PAIRS, CROSS_PAIRS
    scan_result = run_step("Screener", scan_all, MAJOR_PAIRS + CROSS_PAIRS)

    if not scan_result:
        log("Screener returned no results. Exiting.")
        return

    candidates = scan_result.get("candidates", [])
    log(f"  Screener: {scan_result.get('scanned', 0)} pairs scanned, {len(candidates)} candidates")

    if not candidates:
        log("  No candidates passed screener. Syncing journal and exiting.")
        from forex_journal import sync_closed_trades
        run_step("Journal sync", sync_closed_trades)
        return

    if args.scan_only:
        log("  --scan-only: stopping after screener.")
        return

    # ── Step 3: Research candidates ───────────────────────────────────────
    from forex_research import research_pair
    research_results = []
    for c in candidates:
        pair = c["pair"]
        r = run_step(f"Research {pair}", research_pair, pair, c)
        if r and "error" not in r:
            research_results.append(r)
        time.sleep(0.5)

    log(f"  Research complete: {len(research_results)} pairs analyzed")

    # ── Step 4: Judge ─────────────────────────────────────────────────────
    from forex_judge import judge_pair, GATE_TRADE
    verdicts = []
    for r in research_results:
        v = run_step(f"Judge {r['pair']}", judge_pair, r)
        if v:
            verdicts.append(v)

    verdicts.sort(key=lambda x: x.get("score", 0), reverse=True)
    tradeable = [v for v in verdicts if v.get("verdict") not in ("SKIP", "DISQUALIFIED", None)]

    log(f"  Judge: {len(verdicts)} scored, {len(tradeable)} tradeable (≥{GATE_TRADE})")
    for v in verdicts[:5]:
        log(f"    {v['pair']:12s}  {v.get('direction','?'):6s}  score={v.get('score',0):.2f}  {v.get('verdict','?')}")

    # Save verdicts
    today = date.today().isoformat()
    from forex_screener import JOURNAL_DIR
    verdicts_path = os.path.join(os.path.dirname(ROOT), "AI Trading Agent", "logs", "forex", f"{today}_verdicts.json")
    verdicts_path = os.path.join(ROOT, "logs", "forex", f"{today}_verdicts.json")
    with open(verdicts_path, "w") as f:
        json.dump({
            "session_date": today,
            "judged_at": datetime.now(timezone.utc).isoformat(),
            "environment": OANDA_ENV,
            "total_reviewed": len(verdicts),
            "tradeable": len(tradeable),
            "gate": GATE_TRADE,
            "verdicts": verdicts,
        }, f, indent=2)

    if args.judge_only or args.dry_run:
        mode = "--judge-only" if args.judge_only else "--dry-run"
        log(f"  {mode}: skipping order placement.")
        from forex_journal import sync_closed_trades
        run_step("Journal sync", sync_closed_trades)
        return

    # ── Step 4.5: Position management (trailing + time stops) ────────────────
    from forex_trade import manage_trailing_stops, check_time_stops
    run_step("Trailing stops", manage_trailing_stops)
    run_step("Time stops", check_time_stops)

    # ── Step 5: Trade ─────────────────────────────────────────────────────
    if tradeable:
        from forex_trade import execute_verdicts, check_daily_loss_guard, count_open_positions, MAX_OPEN_PAIRS

        if check_daily_loss_guard():
            log("  G4: Daily loss limit breached — skipping new orders.")
        else:
            open_count = count_open_positions()
            if open_count >= MAX_OPEN_PAIRS:
                log(f"  G2: Max open positions reached ({open_count}/{MAX_OPEN_PAIRS}) — skipping new orders.")
            else:
                log(f"  Executing top tradeable pairs... ({open_count} currently open, max {MAX_OPEN_PAIRS})")
                fills = run_step("Execute orders", execute_verdicts)
                if fills:
                    filled = [f for f in fills if f.get("status") == "filled"]
                    failed = [f for f in fills if f.get("status") != "filled"]
                    log(f"  Orders: {len(filled)} filled, {len(failed)} failed")
    else:
        log("  No tradeable pairs — no orders placed.")

    # ── Step 6: Sync journal ───────────────────────────────────────────────
    time.sleep(2)
    from forex_journal import sync_closed_trades, print_summary, load_journal, auto_log_lessons_from_journal, write_daily_reflection
    run_step("Journal sync", sync_closed_trades)
    run_step("Auto-log lessons", auto_log_lessons_from_journal)
    run_step("Daily reflection", write_daily_reflection)

    # ── Step 7: Daily optimizer ────────────────────────────────────────────
    from forex_optimizer import run_daily_optimizer, scan_patterns_all_pairs
    run_step("Daily optimizer", run_daily_optimizer)
    run_step("Pattern scan", scan_patterns_all_pairs)

    # ── Final status ───────────────────────────────────────────────────────
    from forex_briefing import print_daily_briefing
    print_daily_briefing()
    log("Run complete.")


if __name__ == "__main__":
    main()
