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


def main():
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
