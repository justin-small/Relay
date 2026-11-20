"""Shared time helpers (#522): one weekday numbering and one way to read clock times and daily windows.

**Days count from Sunday = 0** (Sunday 0, Monday 1 ... Saturday 6), the US week the pages show. Python's
`date.weekday()` counts from Monday, so never compare a stored day with it directly: use `weekday(d)`.
Before this, engine schedules and signage counted from Sunday while boards, crew call times and YouTube counted
from Monday; the last two had their saved days moved over when this landed.

Clock times are "HH:MM", 24-hour. A window whose end is not after its start runs past midnight, and the day it
started on is the one that counts. `tests/test_shared_concepts.py` refuses new weekday or HH:MM parsing elsewhere.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

DAY_ABBR = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
DAY_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
WEEKDAYS = frozenset(range(1, 6))          # Monday to Friday
WEEKEND = frozenset((0, 6))
DAY_MINUTES = 24 * 60

CLOCK = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
# YouTube rules have always accepted "9:30" (and stored "09:30").
_LOOSE_CLOCK = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")


def weekday(d: date) -> int:
    """The day of the week of `d`, Sunday = 0."""
    return (d.weekday() + 1) % 7


def from_monday_first(day: int) -> int:
    """A day numbered from Monday = 0 (Python's `weekday()`) renumbered from Sunday = 0. For migrating saved days."""
    return (day + 1) % 7


def is_clock(text: object) -> bool:
    """Whether `text` is a clock time written "HH:MM", 24-hour."""
    return isinstance(text, str) and CLOCK.fullmatch(text) is not None


def clean_clock(text: object, *, loose: bool = False) -> str | None:
    """`text` as "HH:MM", or None when it is not a clock time. `loose` also takes a one-digit hour ("9:30")."""
    if not isinstance(text, str):
        return None
    m = (_LOOSE_CLOCK if loose else CLOCK).fullmatch(text.strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def minutes(hhmm: str) -> int:
    """Minutes after midnight of a checked "HH:MM" clock time."""
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def us_clock(hhmm: str) -> str:
    """A checked "HH:MM" as the pages show it: "9:30 AM", "12:05 AM", "8:30 PM"."""
    mins = minutes(hhmm)
    h, m = mins // 60, mins % 60
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def window_start(now: datetime, start: int, end: int, day_ok: Callable[[date], bool]) -> datetime | None:
    """When the run of a daily window covering wall-clock `now` began, or None if no run covers it.

    `start` and `end` are minutes after midnight; `end` at or before `start` runs past midnight into the next day,
    and `start == end` is the whole day. `day_ok(d)` says whether a run may start on date `d` (its weekday, a date
    range...). The result keeps `now`'s tzinfo (wall-clock arithmetic, so a run keeps its clock time across a
    daylight-saving change)."""
    if start == end:
        start, end = 0, DAY_MINUTES
    crossing = end <= start
    candidates = [now.date()] + ([now.date() - timedelta(days=1)] if crossing else [])
    for d in candidates:
        if not day_ok(d):
            continue
        midnight = datetime(d.year, d.month, d.day, tzinfo=now.tzinfo)
        begin = midnight + timedelta(minutes=start)
        finish = midnight + timedelta(minutes=end + (DAY_MINUTES if crossing else 0))
        if begin <= now < finish:
            return begin
    return None
