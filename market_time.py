#!/usr/bin/env python3
"""
market_time.py — the trading day and wall-clock time, in US/Eastern.

Every pipeline script computed "today" and "now" with the naive date.today()/
datetime.now(), which read the RUNNER's local clock — UTC on GitHub Actions.
That agreed with the actual ET trading day in the common case, because a run
close to its intended schedule never crosses a day boundary between UTC and
ET. It broke the moment a run was delayed late enough to cross one: on
2026-08-28 the EOD job actually ran at 11:09 PM ET, but journal.py's own
"Generated: {datetime.now()} ET" printed a UTC timestamp mislabelled as ET
and read as "2026-08-29 03:09" — the wrong day, printed with a timezone claim
that was false. Its filename followed the same wrong clock: journal.py wrote
2026-08-29.md while run_eod_github.sh's own $DATE (already fixed to compute in
ET — see that script) was 2026-08-28. The bash guard's G1 idempotency check
greps for "$DATE.md" and would never find a match against a file the Python
layer filed under a different day, so the two layers silently disagreed about
which day's session they were looking at.

This mirrors the run_eod_github.sh / run_github.sh / run_midday_github.sh fix
exactly, one level up: derive the real US/Eastern date and time directly
rather than trusting the runner's clock to already be in the right zone.

Use et_today() anywhere "today" determines a log filename or a decision about
which trading day a session belongs to. Use et_now() anywhere a timestamp is
printed and labelled ET. Do not blanket-replace every datetime.now() in the
codebase with this — timestamps used only for elapsed-time math or cooldown
deltas are self-consistent regardless of zone and gain nothing from it.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def et_now() -> datetime:
    """Current wall-clock time in US/Eastern, timezone-aware."""
    return datetime.now(EASTERN)


def et_today() -> date:
    """Current calendar date in US/Eastern — the trading day, not the runner's."""
    return et_now().date()


def et_today_iso() -> str:
    """et_today() as 'YYYY-MM-DD', matching the bash scripts' $DATE exactly."""
    return et_today().isoformat()


def et_timestamp(fmt: str = "%Y-%m-%d %H:%M:%S ET") -> str:
    """Formatted ET timestamp for journal/report headers. Default label is ET
    and is only ever attached to a value actually computed in that zone."""
    return et_now().strftime(fmt)


# ── NYSE session check — self-contained, no broker dependency ────────────────
#
# Added 2026-10-29 for the Robinhood Execution Procedure's step 1a. That step
# originally called Alpaca's get_clock -- a broker whose credentials have
# nothing to do with Robinhood trading -- to answer "is the market open."
# When Alpaca's MCP connection returned 401 on an otherwise-unrelated
# credential problem, it silently blocked every Robinhood tick from ever
# reaching a trade decision: an unrelated system's outage gating real-money
# execution, exactly the kind of accidental cross-book coupling this file
# exists to avoid elsewhere. This check depends on nothing but the ET clock
# and a hardcoded holiday calendar, so it can never 401.
#
# Must be extended with each year's calendar before relying on it for that
# year -- an unlisted year falls back to the plain weekday+hours check below,
# which only risks a false "open" on an actual holiday (a harmless rejected
# order, not a bad price or a bad size), not a false "closed."
NYSE_HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}
# 1:00 PM ET close instead of 4:00 PM. Day after Thanksgiving and Christmas
# Eve every year; day before July 4th only when the 4th itself is a weekday
# trading day (not already a 3-day-weekend holiday) -- not populated for
# 2027 yet since July 4, 2027 falls on a Sunday and the resulting early-close
# date needs checking against NYSE's actual published calendar, not derived
# here. Extend before relying on this for 2027 H2.
NYSE_EARLY_CLOSE = {
    date(2026, 11, 27), date(2026, 12, 24),
}


def is_nyse_session_open(now: datetime = None) -> bool:
    """
    True only during regular NYSE trading hours: 9:30 AM-4:00 PM ET
    (1:00 PM on an early-close day), Mon-Fri, not a holiday. Self-contained --
    see the module comment above for why this doesn't call any broker API.
    """
    now = now if now is not None else et_now()
    today = now.date()

    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    if today in NYSE_HOLIDAYS:
        return False

    close_hour, close_minute = (13, 0) if today in NYSE_EARLY_CLOSE else (16, 0)
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_time = now.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return open_time <= now <= close_time
