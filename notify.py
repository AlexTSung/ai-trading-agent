#!/usr/bin/env python3
"""
notify.py — push notifications for pipeline events, via ntfy.sh.

Pure HTTP POST, no MCP, no SDK — runs inside GitHub Actions exactly like
trade.py's own REST calls to Alpaca (no local machine or interactive agent
session required). Notifications are advisory only, never a trading
decision: a failure here must never block or fail a pipeline run, so every
public function catches its own exceptions and degrades to a logged no-op
rather than raising.

Configuration: the NTFY_TOPIC env var names a private ntfy.sh topic. ntfy.sh
topics are unauthenticated by default — anyone who learns the topic name can
read every message sent to it — so this must be a long, hard-to-guess random
string, not something like "alex-trades". If NTFY_TOPIC is unset, every
function below is a no-op (logged once per call), never a crash: a missing
notification channel is a degraded nice-to-have, not a Hard Rule.
"""

import os
import smtplib
import sys
from email.mime.text import MIMEText

import requests

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
NTFY_BASE_URL = "https://ntfy.sh"


def send(title: str, message: str, priority: str = "default", tags: str = None) -> bool:
    """Best-effort push notification. Returns True if sent, False if skipped
    or failed — never raises, since a notification failure must not affect
    trading."""
    if not NTFY_TOPIC:
        print("[notify] NTFY_TOPIC not set -- skipping notification", file=sys.stderr)
        return False
    try:
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        r = requests.post(f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
                           data=message.encode("utf-8"), headers=headers, timeout=10)
        if r.status_code != 200:
            print(f"[notify] ntfy.sh returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"[notify] failed to send: {e}", file=sys.stderr)
        return False


def send_email(subject: str, body: str) -> bool:
    """Best-effort email through Gmail SMTP. Needs GMAIL_USER, GMAIL_APP_PASSWORD
    and NOTIFY_EMAIL in the environment (the same repo secrets the forex agent
    uses). Any of them missing is a logged no-op, never a failure.

    Every message goes to stderr: several pipeline steps pipe stdout straight
    into a JSON file, and a status line on stdout corrupts it (portfolio-review
    .json, 2026-11-06 and 2026-11-09)."""
    user, password, to = (os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD"),
                          os.environ.get("NOTIFY_EMAIL"))
    if not (user and password and to):
        print("[notify] email not configured (GMAIL_USER/GMAIL_APP_PASSWORD/NOTIFY_EMAIL) "
              "-- skipping email", file=sys.stderr)
        return False
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = subject, user, to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
            smtp.login(user, password)
            smtp.sendmail(user, [to], msg.as_string())
        return True
    except (smtplib.SMTPException, OSError) as e:
        print(f"[notify] email failed: {e}", file=sys.stderr)
        return False


def alert(title: str, message: str, priority: str = "default", tags: str = None) -> bool:
    """Push AND email. True if either channel delivered."""
    pushed = send(title, message, priority=priority, tags=tags)
    emailed = send_email(title, message)
    return pushed or emailed


def notify_new_approvals(symbols: list, judge_results: dict) -> bool:
    """Called by judge.py whenever new (not seen in an earlier run today)
    APPROVE rows appear. One notification per run, not one per symbol."""
    if not symbols:
        return False
    lines = []
    for sym in symbols:
        scored = judge_results.get("scores", {}).get(sym, {})
        score = scored.get("score")
        rationale = scored.get("rationale", "")
        line = f"{sym}: {score}" if score is not None else sym
        if rationale:
            line += f" -- {rationale[:140]}"
        lines.append(line)
    title = f"Judge APPROVE: {len(symbols)} new candidate(s)"
    return send(title, "\n".join(lines), priority="high", tags="chart_with_upwards_trend")


def notify_portfolio_review(flags: list) -> bool:
    """Called by trade.py's portfolio-review step whenever one or more open
    positions have something worth a second look (near-stop, thesis-break
    condition, fresh negative news). Never fires for a clean review."""
    if not flags:
        return False
    lines = []
    for f in flags:
        plpc = f.get("unrealized_plpc")
        plpc_str = f"{plpc*100:+.1f}%" if plpc is not None else "?"
        lines.append(f"{f['symbol']} ({f.get('tier', '?')}, {plpc_str}): "
                     + "; ".join(f.get("reasons", [])))
    title = f"Portfolio review: {len(flags)} position(s) worth a look"
    return send(title, "\n".join(lines), priority="default", tags="mag")
