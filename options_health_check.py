#!/usr/bin/env python3
"""
options_health_check.py — Daily automation health check for options trading system.

Verifies that every piece of automation ran correctly in the past 24 hours:
  1. Daily options scan (run_options.sh) — did it fire and succeed?
  2. 30-min monitor (run_options_manage.sh) — did it fire recently?
  3. Position health — any positions in danger zones?
  4. Log integrity — are logs being written to disk?
  5. Account connectivity — can we reach Alpaca?

Outputs a structured health report and writes to logs/options/health-YYYY-MM.log.
Designed to run at 8:00 AM ET daily — before market open, so you can act on alerts.

Exit codes:
  0 — all systems healthy
  1 — degraded (some checks failed, manual review needed)
  2 — critical (cron not running, Alpaca unreachable)
"""

import os
import sys
import json
import requests
from datetime import datetime, date, timezone, timedelta

LOG_DIR    = os.path.join(os.path.dirname(__file__), "logs", "options")
HEALTH_LOG = os.path.join(LOG_DIR, f"health-{datetime.now(timezone.utc).strftime('%Y-%m')}.log")
os.makedirs(LOG_DIR, exist_ok=True)

BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_KEY    = os.getenv("APCA_API_KEY_ID")
ALPACA_SECRET = os.getenv("APCA_API_SECRET_KEY")


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }


# ── Individual checks ─────────────────────────────────────────────────────────

def check_alpaca_connectivity() -> dict:
    """Verify Alpaca paper API is reachable and credentials work."""
    try:
        r = requests.get(f"{BASE_URL}/v2/account", headers=_headers(), timeout=10)
        if r.status_code == 200:
            acct = r.json()
            equity = float(acct.get("equity", 0))
            status = acct.get("status", "unknown")
            return {
                "ok": True,
                "equity": round(equity, 2),
                "account_status": status,
                "detail": f"Account active — equity ${equity:,.2f}",
            }
        return {"ok": False, "detail": f"HTTP {r.status_code}: {r.text[:100]}"}
    except Exception as e:
        return {"ok": False, "detail": f"Connection error: {e}"}


def check_cron_log(log_path: str, max_age_hours: float,
                   label: str) -> dict:
    """
    Verify a cron log was written within max_age_hours.
    This proves the cron job actually fired — not just that the script exists.
    """
    if not os.path.exists(log_path):
        return {
            "ok": False,
            "detail": f"{label}: log file not found at {log_path}",
        }

    mtime    = os.path.getmtime(log_path)
    modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
    age_hrs  = (datetime.now(timezone.utc) - modified).total_seconds() / 3600

    if age_hrs > max_age_hours:
        return {
            "ok": False,
            "detail": f"{label}: last run {age_hrs:.1f}h ago (max allowed: {max_age_hours}h)",
            "last_run": modified.isoformat(),
            "age_hours": round(age_hrs, 1),
        }

    return {
        "ok": True,
        "detail": f"{label}: last run {age_hrs:.1f}h ago — OK",
        "last_run": modified.isoformat(),
        "age_hours": round(age_hrs, 1),
    }


def check_monitor_run() -> dict:
    """
    Verify 30-min monitor ran recently by reading its JSON summary file.
    More precise than log mtime — confirms actual successful execution.
    """
    summary_path = os.path.join(LOG_DIR, "last_monitor_run.json")
    if not os.path.exists(summary_path):
        return {"ok": False, "detail": "Monitor summary not found — monitor may not have run yet"}

    try:
        with open(summary_path) as f:
            summary = json.load(f)

        ts  = datetime.fromisoformat(summary["timestamp"])
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 60  # in minutes

        if age > 60:
            return {
                "ok": False,
                "detail": f"Monitor last ran {age:.0f}min ago — expected every 30 min",
                "last_run": summary["timestamp"],
                "actions":  summary.get("actions", []),
                "warnings": summary.get("warnings", []),
            }

        return {
            "ok": True,
            "detail": f"Monitor ran {age:.0f}min ago — OK",
            "last_run":  summary["timestamp"],
            "positions": summary.get("positions", "?"),
            "actions":   summary.get("actions", []),
            "warnings":  summary.get("warnings", []),
        }
    except Exception as e:
        return {"ok": False, "detail": f"Could not parse monitor summary: {e}"}


def check_open_positions() -> dict:
    """Scan open positions for anything in a danger zone."""
    try:
        r = requests.get(f"{BASE_URL}/v2/positions", headers=_headers(), timeout=10)
        if r.status_code != 200:
            return {"ok": False, "detail": f"Positions API error: {r.status_code}"}

        positions = [p for p in r.json() if p.get("asset_class") == "us_option"]
        if not positions:
            return {"ok": True, "detail": "No open option positions", "count": 0}

        danger = []
        total_pl = 0.0

        for pos in positions:
            sym = pos.get("symbol", "")
            pl  = float(pos.get("unrealized_pl", 0))
            plp = float(pos.get("unrealized_plpc", 0)) * 100
            total_pl += pl

            # Parse DTE
            dte = None
            try:
                for i, c in enumerate(sym):
                    if c.isdigit():
                        d = sym[i:i+6]
                        from datetime import date as _d
                        exp = _d(2000+int(d[:2]), int(d[2:4]), int(d[4:6]))
                        dte = max(0, (exp - _d.today()).days)
                        break
            except Exception:
                pass

            flags = []
            if dte is not None and dte <= 7:
                flags.append(f"DTE={dte} CRITICAL")
            elif dte is not None and dte <= 21:
                flags.append(f"DTE={dte} warn")
            if plp <= -100:
                flags.append(f"P&L={plp:.0f}% STOP")
            elif plp <= -50:
                flags.append(f"P&L={plp:.0f}% warn")
            if plp >= 50:
                flags.append(f"P&L=+{plp:.0f}% target")

            if flags:
                danger.append({"symbol": sym, "dte": dte, "pl_pct": round(plp,1), "flags": flags})

        return {
            "ok": len(danger) == 0,
            "detail": (f"{len(positions)} positions, {len(danger)} need attention"
                       if danger else f"{len(positions)} positions — all healthy"),
            "count":    len(positions),
            "total_pl": round(total_pl, 2),
            "danger":   danger,
        }
    except Exception as e:
        return {"ok": False, "detail": f"Error checking positions: {e}"}


def check_log_not_empty(log_path: str, label: str) -> dict:
    """Verify a log file exists and is not empty."""
    if not os.path.exists(log_path):
        return {"ok": False, "detail": f"{label}: file not found"}
    size = os.path.getsize(log_path)
    if size == 0:
        return {"ok": False, "detail": f"{label}: file is empty"}
    return {"ok": True, "detail": f"{label}: {size/1024:.1f} KB"}


# ── Report builder ─────────────────────────────────────────────────────────────

def run_health_check() -> int:
    """
    Run all checks. Print structured report. Return exit code (0/1/2).
    """
    now     = datetime.now(timezone.utc)
    month   = now.strftime("%Y-%m")
    today   = now.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f"OPTIONS SYSTEM HEALTH CHECK — {today}")
    print(f"{'='*60}")

    checks = {}
    critical_fail = False
    any_fail      = False

    # 1. Alpaca connectivity
    checks["alpaca"] = check_alpaca_connectivity()

    # 2. Daily scan cron log (expect once per trading day — check within 28h for weekends)
    scan_log = os.path.join(LOG_DIR, f"cron-{month}.log")
    checks["daily_scan"] = check_cron_log(scan_log, max_age_hours=28,
                                           label="Daily options scan")

    # 3. 30-min monitor (most recent execution summary)
    checks["monitor"] = check_monitor_run()

    # 4. Monitor cron log freshness
    manage_log = os.path.join(LOG_DIR, f"manage-{month}.log")
    checks["monitor_log"] = check_cron_log(manage_log, max_age_hours=1,
                                            label="30-min monitor log")

    # 5. Open positions health
    checks["positions"] = check_open_positions()

    # ── Print results ──────────────────────────────────────────────────────────

    sections = [
        ("Alpaca API",          "alpaca"),
        ("Daily scan cron",     "daily_scan"),
        ("30-min monitor",      "monitor"),
        ("Monitor log fresh",   "monitor_log"),
        ("Open positions",      "positions"),
    ]

    for label, key in sections:
        result = checks[key]
        status = "✓ OK  " if result["ok"] else "✗ FAIL"
        print(f"  {status}  {label:<22} {result['detail']}")
        if not result["ok"]:
            any_fail = True
            # Alpaca unreachable or cron completely dead = critical
            if key in ("alpaca", "daily_scan"):
                critical_fail = True

    # ── Position detail ────────────────────────────────────────────────────────

    pos_check = checks["positions"]
    if pos_check.get("danger"):
        print(f"\n  POSITIONS NEEDING ATTENTION:")
        for p in pos_check["danger"]:
            print(f"    {p['symbol']:<34} {' | '.join(p['flags'])}")

    monitor_check = checks["monitor"]
    if monitor_check.get("warnings"):
        print(f"\n  MONITOR WARNINGS (from last run):")
        for w in monitor_check["warnings"]:
            print(f"    {w['symbol']}: {w['reason']}")
    if monitor_check.get("actions"):
        print(f"\n  MONITOR ACTIONS TAKEN (from last run):")
        for a in monitor_check["actions"]:
            print(f"    {a['symbol']}: {a['action']} — {a['reason']}")

    # ── Summary ────────────────────────────────────────────────────────────────

    equity = checks["alpaca"].get("equity", "?")
    total_pl = pos_check.get("total_pl", "?")
    n_pos    = pos_check.get("count", "?")

    print(f"\n  Account equity: ${equity:,.2f}" if isinstance(equity, float) else
          f"\n  Account equity: {equity}")
    print(f"  Open positions: {n_pos} | Unrealized P&L: ${total_pl:,.2f}"
          if isinstance(total_pl, float) else
          f"  Open positions: {n_pos}")

    # ── Status line ───────────────────────────────────────────────────────────

    if critical_fail:
        print(f"\n  STATUS: *** CRITICAL — manual intervention required ***")
        exit_code = 2
    elif any_fail:
        print(f"\n  STATUS: DEGRADED — review flagged items above")
        exit_code = 1
    else:
        print(f"\n  STATUS: ALL SYSTEMS HEALTHY")
        exit_code = 0

    print(f"{'='*60}\n")

    # ── Write to log ──────────────────────────────────────────────────────────

    with open(HEALTH_LOG, "a") as f:
        f.write(f"\n[{now.isoformat()}] exit={exit_code}\n")
        for key, result in checks.items():
            ok_str = "OK" if result["ok"] else "FAIL"
            f.write(f"  {key}: {ok_str} — {result['detail']}\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(run_health_check())
