#!/usr/bin/env python3
"""
forex_notify.py — Hourly opportunity scanner with email alert.

Runs judge-only scan. If any pair scores >= 7.0, sends an HTML email
via Gmail SMTP. Requires GMAIL_APP_PASSWORD in env.

Usage:
  python3 forex_notify.py          # Scan + email if opportunities found
  python3 forex_notify.py --check  # Print verdicts, no email
"""

import os, sys, json, smtplib
from datetime import date, datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

ROOT    = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(ROOT, "logs", "forex")
OPP_LOG = os.path.join(LOG_DIR, f"opportunities-{date.today().strftime('%Y-%m')}.jsonl")
os.makedirs(LOG_DIR, exist_ok=True)

NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "alex.sung@youversion.com")
GMAIL_USER   = os.getenv("GMAIL_USER",   "alex.sung@youversion.com")
GMAIL_APP_PW = os.getenv("GMAIL_APP_PASSWORD", "")
GATE         = 7.0
DRY_CHECK    = "--check" in sys.argv
BRIEFING     = "--briefing" in sys.argv

assert os.getenv("OANDA_ENV", "practice") == "practice", "FATAL: OANDA_ENV must be practice"


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def run_scan() -> list:
    from forex_screener import screen_all_pairs
    from forex_research  import research_pair
    from forex_judge     import judge_pair

    candidates = screen_all_pairs()
    results = []
    for c in candidates.get("candidates", []):
        pair = c["pair"]
        try:
            r = research_pair(pair)
            v = judge_pair(r)
            if v.get("score", 0) >= GATE and v.get("verdict") in ("BUY", "SELL", "STRONG_BUY", "STRONG_SELL"):
                results.append({
                    "pair":      pair,
                    "direction": v.get("direction"),
                    "score":     v.get("score"),
                    "verdict":   v.get("verdict"),
                    "price":     r.get("current_price"),
                    "stop":      r.get("stop_price"),
                    "stop_pips": r.get("stop_pips"),
                    "tp1":       r.get("tp1_price"),
                    "tp1_pips":  r.get("tp1_pips"),
                    "carry":     r.get("carry_pct_annual"),
                    "rsi14":     r.get("rsi14"),
                    "atr_pips":  r.get("atr14_h4_pips"),
                    "spread":    r.get("spread_pips"),
                    "reason":    v.get("reason", ""),
                    "scanned_at": now_utc(),
                })
        except Exception as e:
            print(f"  [{pair}] error: {e}")
    return results


def send_email(opportunities: list):
    if not GMAIL_APP_PW:
        print("  [EMAIL] GMAIL_APP_PASSWORD not set — skipping.")
        return

    n = len(opportunities)
    subject = f"Forex Agent: {n} setup{'s' if n > 1 else ''} — {date.today().strftime('%b %d %Y')}"

    rows = "".join(f"""
        <tr>
          <td style="padding:8px 12px;font-weight:bold">{'🟢' if o['direction']=='LONG' else '🔴'} {o['pair']}</td>
          <td style="padding:8px 12px">{o['direction']}</td>
          <td style="padding:8px 12px;color:#1a7f4b;font-weight:bold">{o['score']}</td>
          <td style="padding:8px 12px">{o['price']}</td>
          <td style="padding:8px 12px">{o['stop']} ({o['stop_pips']} pips)</td>
          <td style="padding:8px 12px">{o['tp1']} ({o['tp1_pips']} pips)</td>
          <td style="padding:8px 12px">{o['carry']}%/yr</td>
        </tr>""" for o in opportunities)

    details = "".join(f"""
      <div style="border:1px solid #e0e0e0;border-radius:6px;padding:12px;margin-bottom:10px">
        <strong>{o['pair']} {o['direction']}</strong> &nbsp;Score {o['score']} ({o['verdict']})<br>
        Entry: {o['price']} &nbsp;|&nbsp; Stop: {o['stop']} ({o['stop_pips']} pips) &nbsp;|&nbsp; TP1: {o['tp1']} ({o['tp1_pips']} pips)<br>
        Carry: {o['carry']}%/yr &nbsp;|&nbsp; RSI14: {o['rsi14']} &nbsp;|&nbsp; ATR: {o['atr_pips']} pips &nbsp;|&nbsp; Spread: {o['spread']} pips<br>
        <span style="color:#888;font-size:0.88em">{o['reason']}</span>
      </div>""" for o in opportunities)

    html = f"""<html><body style="font-family:sans-serif;max-width:680px;margin:0 auto;color:#222">
      <h2 style="color:#1a7f4b;margin-bottom:4px">Forex Agent — {n} Setup{'s' if n>1 else ''} Found</h2>
      <p style="color:#888;margin-top:0">{now_utc()} &nbsp;·&nbsp; OANDA Practice &nbsp;·&nbsp; Score ≥ {GATE}</p>
      <table border="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #e0e0e0;margin-bottom:20px">
        <thead><tr style="background:#f5f5f5;text-align:left">
          <th style="padding:8px 12px">Pair</th><th style="padding:8px 12px">Side</th>
          <th style="padding:8px 12px">Score</th><th style="padding:8px 12px">Entry</th>
          <th style="padding:8px 12px">Stop</th><th style="padding:8px 12px">TP1</th>
          <th style="padding:8px 12px">Carry</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      {details}
      <p style="color:#bbb;font-size:0.82em;border-top:1px solid #eee;padding-top:10px">
        Practice account · Paper trading only · Not financial advice
      </p>
    </body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PW)
            smtp.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"  [EMAIL] Sent to {NOTIFY_EMAIL} ✅")
    except Exception as e:
        print(f"  [EMAIL] Failed: {e}")


def log_opportunities(opportunities: list):
    with open(OPP_LOG, "a") as f:
        for o in opportunities:
            f.write(json.dumps(o) + "\n")
    print(f"  [LOG] {len(opportunities)} opportunities → {OPP_LOG}")


def send_briefing_email(positions: list, journal: dict, verdicts: list):
    """Daily morning briefing — always sends, even if no new trades."""
    if not GMAIL_APP_PW:
        print("  [EMAIL] GMAIL_APP_PASSWORD not set — skipping briefing.")
        return

    today = date.today().strftime("%b %d, %Y")
    nav   = journal.get("nav", "—")
    pl    = journal.get("total_pl", 0)
    wr    = journal.get("win_rate", 0)
    trades = journal.get("closed_trades", 0)
    pf    = journal.get("profit_factor", 0)

    # Graduation progress rows
    grad  = journal.get("graduation", {})
    def row(label, val, target, pass_):
        icon = "✅" if pass_ else "❌"
        return f"<tr><td style='padding:4px 8px'>{icon} {label}</td><td style='padding:4px 8px'>{val}</td><td style='padding:4px 8px;color:#888'>{target}</td></tr>"

    grad_rows = "".join([
        row("Closed trades",  trades,        "≥ 30",   trades >= 30),
        row("Win rate",       f"{wr:.1%}",   "≥ 55%",  wr >= 0.55),
        row("Profit factor",  f"{pf:.2f}",   "≥ 1.3",  pf >= 1.3),
        row("Net P&L",        f"${pl:+.2f}", "> $0",   pl > 0),
    ])

    # Open positions rows
    pos_rows = "".join(
        f"<tr><td style='padding:4px 8px'><b>{p['pair']}</b></td>"
        f"<td style='padding:4px 8px'>{p.get('direction','?')}</td>"
        f"<td style='padding:4px 8px'>{p.get('entry','—')}</td>"
        f"<td style='padding:4px 8px;color:{'#1a7f4b' if float(p.get('upl',0))>=0 else '#c0392b'}'>"
        f"${float(p.get('upl',0)):+.2f}</td></tr>"
        for p in positions
    ) or "<tr><td colspan='4' style='padding:4px 8px;color:#888'>No open positions</td></tr>"

    # Top verdicts
    top_v = sorted(verdicts, key=lambda x: x.get("score", 0), reverse=True)[:5]
    verdict_rows = "".join(
        f"<tr><td style='padding:4px 8px'><b>{v['pair']}</b></td>"
        f"<td style='padding:4px 8px'>{v.get('direction','?')}</td>"
        f"<td style='padding:4px 8px;font-weight:bold'>{v.get('score',0):.2f}</td>"
        f"<td style='padding:4px 8px;color:#888'>{v.get('verdict','?')}"
        f"{' — ' + v.get('disqualify_reason','')[:40] if v.get('disqualify_reason') else ''}</td></tr>"
        for v in top_v
    ) or "<tr><td colspan='4' style='color:#888;padding:4px 8px'>No verdicts yet</td></tr>"

    html = f"""<html><body style="font-family:sans-serif;max-width:680px;margin:0 auto;color:#222">
  <h2 style="color:#1a7f4b;margin-bottom:2px">📊 Forex Daily Briefing — {today}</h2>
  <p style="color:#888;margin-top:0">{now_utc()} · OANDA Practice · Account NAV: <b>${nav:,}</b></p>

  <h3 style="margin-bottom:4px">Open Positions</h3>
  <table border="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #e0e0e0;margin-bottom:16px">
    <thead><tr style="background:#f5f5f5">
      <th style="padding:4px 8px;text-align:left">Pair</th>
      <th style="padding:4px 8px;text-align:left">Side</th>
      <th style="padding:4px 8px;text-align:left">Entry</th>
      <th style="padding:4px 8px;text-align:left">Unreal. P&L</th>
    </tr></thead>
    <tbody>{pos_rows}</tbody>
  </table>

  <h3 style="margin-bottom:4px">Top Pairs — Today's Judge Scores</h3>
  <table border="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #e0e0e0;margin-bottom:16px">
    <thead><tr style="background:#f5f5f5">
      <th style="padding:4px 8px;text-align:left">Pair</th>
      <th style="padding:4px 8px;text-align:left">Side</th>
      <th style="padding:4px 8px;text-align:left">Score</th>
      <th style="padding:4px 8px;text-align:left">Status</th>
    </tr></thead>
    <tbody>{verdict_rows}</tbody>
  </table>

  <h3 style="margin-bottom:4px">Graduation Progress (Phase 4)</h3>
  <table border="0" cellspacing="0" style="border-collapse:collapse;width:100%;border:1px solid #e0e0e0;margin-bottom:16px">
    <thead><tr style="background:#f5f5f5">
      <th style="padding:4px 8px;text-align:left">Criterion</th>
      <th style="padding:4px 8px;text-align:left">Current</th>
      <th style="padding:4px 8px;text-align:left">Target</th>
    </tr></thead>
    <tbody>{grad_rows}</tbody>
  </table>

  <p style="color:#bbb;font-size:0.82em;border-top:1px solid #eee;padding-top:10px">
    Practice account · Paper trading only · Not financial advice
  </p>
</body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Forex Briefing {today} — NAV ${nav:,} | {trades} trades | WR {wr:.0%}"
        msg["From"]    = GMAIL_USER
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_APP_PW)
            smtp.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"  [BRIEFING] Sent to {NOTIFY_EMAIL} ✅")
    except Exception as e:
        print(f"  [BRIEFING] Failed: {e}")


def run_briefing():
    """Pull live data and send morning briefing email."""
    print(f"[{now_utc()}] Building daily briefing...")

    # Open positions from OANDA
    positions = []
    try:
        from forex_screener import get_account_summary
        import requests as _req
        api_key  = os.getenv("OANDA_API_KEY", "")
        acct_id  = os.getenv("OANDA_ACCOUNT_ID", "")
        base_url = "https://api-fxpractice.oanda.com"
        hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        r = _req.get(f"{base_url}/v3/accounts/{acct_id}/openTrades", headers=hdrs, timeout=10)
        for t in r.json().get("trades", []):
            positions.append({
                "pair":      t["instrument"].replace("_", "/"),
                "direction": "LONG" if float(t["currentUnits"]) > 0 else "SHORT",
                "entry":     t["price"],
                "upl":       t.get("unrealizedPL", 0),
            })
        acct = get_account_summary()
        nav  = float(acct.get("nav") or acct.get("account", {}).get("NAV") or 0)
    except Exception as e:
        print(f"  [BRIEFING] Positions fetch error: {e}")
        nav = 0

    # Journal summary
    journal_data = {"nav": round(nav, 2), "total_pl": 0, "win_rate": 0,
                    "closed_trades": 0, "profit_factor": 0}
    try:
        jpath = os.path.join(LOG_DIR, "trade_journal.json")
        if os.path.exists(jpath):
            with open(jpath) as f:
                j = json.load(f)
            entries = j if isinstance(j, list) else j.get("trades", [])
            closed  = [e for e in entries if e.get("status") == "closed"]
            wins    = [e for e in closed if float(e.get("pl", 0)) > 0]
            losses  = [e for e in closed if float(e.get("pl", 0)) < 0]
            gross_w = sum(float(e.get("pl", 0)) for e in wins)
            gross_l = abs(sum(float(e.get("pl", 0)) for e in losses))
            journal_data = {
                "nav":           round(nav, 2),
                "total_pl":      round(sum(float(e.get("pl", 0)) for e in closed), 2),
                "win_rate":      len(wins) / len(closed) if closed else 0,
                "closed_trades": len(closed),
                "profit_factor": round(gross_w / gross_l, 2) if gross_l else 0,
            }
    except Exception as e:
        print(f"  [BRIEFING] Journal parse error: {e}")

    # Latest verdicts (from today's or yesterday's file)
    verdicts = []
    try:
        today = date.today().isoformat()
        for d in [today, str(date.fromordinal(date.today().toordinal() - 1))]:
            vpath = os.path.join(LOG_DIR, f"{d}_verdicts.json")
            if os.path.exists(vpath):
                with open(vpath) as f:
                    verdicts = json.load(f).get("verdicts", [])
                break
    except Exception as e:
        print(f"  [BRIEFING] Verdicts load error: {e}")

    send_briefing_email(positions, journal_data, verdicts)


def main():
    if BRIEFING:
        run_briefing()
        return

    print(f"[{now_utc()}] Forex opportunity scan...")
    opps = run_scan()

    if not opps:
        print(f"[{now_utc()}] No setups ≥{GATE} this hour.")
        return

    print(f"[{now_utc()}] {len(opps)} setup(s) found:")
    for o in opps:
        print(f"  {o['pair']} {o['direction']} | score={o['score']} | entry={o['price']} SL={o['stop']} TP1={o['tp1']}")

    log_opportunities(opps)

    if not DRY_CHECK:
        send_email(opps)


if __name__ == "__main__":
    main()
