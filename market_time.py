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
