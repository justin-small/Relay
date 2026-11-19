"""Named schedules (#526): validation, occurrence maths, US formatting.

Pure functions only -- nothing here touches the engine, so config.py can use
it to validate the `schedules` list without an import cycle. The runner that
acts on these windows for the engine is scheduler.py; signage slots, board
rows and crew call times pick a schedule by ref (`schedule:relay:<id>`) and
ask this module whether it is open.

A schedule is stored in config.json as:

    {"id": "a1b2c3d4", "name": "Sunday services", "enabled": true,
     "windows": [{"repeat": "weekly", "days": [0], "start_date": null,
                  "end_date": null, "start_time": "10:00", "stop_time": "12:00"}],
     "plan_times": false, "plan_before": 30, "plan_after": 120}

`plan_times` adds a window around every service time of today's Planning Center
plan (`plan_before` minutes before it, `plan_after` after); a caller that has
no plan passes none and the schedule is just its own windows.

Dates are ISO (YYYY-MM-DD) and times 24-hour (HH:MM) on disk, so parsing never
depends on a locale; the panel shows and takes them in US formats (MM/DD/YYYY,
h:mm AM/PM). `days` counts from Sunday = 0, the US week. A stop time earlier
than the start time is the next day. Times are wall-clock in the one Relay time
zone (config `timezone`, set under System), so "10:00 AM Sunday" stays 10:00 AM
local on both sides of a DST change. Before #526 every schedule kept its own
zone and one flat window; `clean_list` reads those and `legacy_zone` finds the
zone to carry over.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import timewin

UTC = timezone.utc

# US zones only, in the order the dropdown lists them. Arizona is its own entry
# because it keeps standard time all year while the rest of Mountain does not.
US_TIMEZONES: list[tuple[str, str]] = [
    ("America/New_York", "Eastern"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Mountain – Arizona (no DST)"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
]
TZ_LABELS = dict(US_TIMEZONES)
DEFAULT_TIMEZONE = "America/New_York"

DAY_ABBR = list(timewin.DAY_ABBR)
REPEATS = ("weekly", "once")
NAME_MAX = 60
MAX_WINDOWS = 20
_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
REF_PREFIX = "schedule:relay:"
PLAN_BEFORE, PLAN_AFTER, PLAN_MAX = 30, 120, 6 * 60
# How far ahead the panel looks for the next start. A weekly schedule always
# recurs inside 8 days; a one-off can be further out, up to a year.
LOOKAHEAD_DAYS = 8
ONCE_HORIZON_DAYS = 366

# Today's Planning Center service times as UTC (start, stop) pairs for [frm, to); the caller supplies it, so this
# module never reaches into a module that may be off.
PlanSource = Callable[[datetime, datetime], list[tuple[datetime, datetime]]]


def default_timezone(env_tz: str | None = None) -> str:
    """The zone Relay starts on: the host's TZ if it is a US zone we offer,
    else Eastern. The container rarely carries the host's zone, so the
    fallback is the common case."""
    return env_tz if env_tz in TZ_LABELS else DEFAULT_TIMEZONE


def clean_zone(name: object, env_tz: str | None = None) -> str:
    """The Relay time zone: `name` if it is a US zone we offer, else the default."""
    return name if isinstance(name, str) and name in TZ_LABELS else default_timezone(env_tz)


def ref(sid: str) -> str:
    """The ref other features store to pick schedule `sid`: "schedule:relay:<id>"."""
    return REF_PREFIX + sid


def ref_id(value: object) -> str | None:
    """The schedule id inside a ref, or None when `value` is not one."""
    if isinstance(value, str) and value.startswith(REF_PREFIX) and _ID.match(value[len(REF_PREFIX):]):
        return value[len(REF_PREFIX):]
    return None


# ------------------------------------------------------------ validation
# What the admin page is told when a schedule is refused: fixed sentences by key, so a response never carries an
# exception's own text. ScheduleError is a ValueError, so callers that catch ValueError still do.
MESSAGES = {
    "not_object": "A schedule must be an object.",
    "repeat": "Repeat must be weekly or once.",
    "zone": "Pick a US time zone.",
    "same_time": "Start and stop time must differ.",
    "days_range": "Days must be 0 (Sunday) to 6 (Saturday).",
    "no_days": "Pick at least one day of the week.",
    "end_before_start": "End date is before the start date.",
    "once_needs_date": "A one-time window needs a date.",
    "bad_start_time": "Start time is not a valid time.",
    "bad_stop_time": "Stop time is not a valid time.",
    "bad_start_date": "Start date is not a valid date.",
    "bad_end_date": "End date is not a valid date.",
    "bad_date": "That is not a valid date.",
    "bad_time": "That is not a valid time.",
    "no_windows": "Add a window, or use the Planning Center plan times.",
    "too_many_windows": "A schedule can have at most 20 windows.",
    "plan_minutes": "Minutes before and after a service time are whole numbers from 0 to 360.",
    "no_name": "Give the schedule a name.",
    "in_use": "That schedule is still used; pick another one there first.",
    "not_found": "That schedule is gone.",
}


class ScheduleError(ValueError):
    def __init__(self, key: str):
        super().__init__(MESSAGES[key])
        self.key = key


def _field_key(kind: str, what: str) -> str:
    key = "bad_" + what.lower().replace(" ", "_")
    return key if key in MESSAGES else f"bad_{kind}"


def _parse_date(raw, what: str) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ScheduleError(_field_key("date", what)) from None


def _parse_time(raw, what: str) -> str:
    clock = timewin.clean_clock(str(raw or ""))
    if not clock:
        raise ScheduleError(_field_key("time", what))
    return clock


def validate_window(raw: dict) -> dict:
    """A clean window dict, or ScheduleError."""
    if not isinstance(raw, dict):
        raise ScheduleError("not_object")
    repeat = str(raw.get("repeat") or "weekly").lower()
    if repeat not in REPEATS:
        raise ScheduleError("repeat")

    start_time = _parse_time(raw.get("start_time"), "Start time")
    stop_time = _parse_time(raw.get("stop_time"), "Stop time")
    if start_time == stop_time:
        raise ScheduleError("same_time")

    start_date = _parse_date(raw.get("start_date"), "Start date")
    end_date = _parse_date(raw.get("end_date"), "End date")

    days: list[int] = []
    if repeat == "weekly":
        try:
            days = sorted({int(d) for d in (raw.get("days") or [])})
        except (TypeError, ValueError):
            raise ScheduleError("days_range") from None
        if not days:
            raise ScheduleError("no_days")
        if days[0] < 0 or days[-1] > 6:
            raise ScheduleError("days_range")
        if start_date and end_date and end_date < start_date:
            raise ScheduleError("end_before_start")
    else:
        if start_date is None:
            raise ScheduleError("once_needs_date")
        end_date = None

    return {
        "repeat": repeat,
        "days": days,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "start_time": start_time,
        "stop_time": stop_time,
    }


def _plan_minutes(raw, default: int) -> int:
    if raw in (None, ""):
        return default
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 <= raw <= PLAN_MAX:
        raise ScheduleError("plan_minutes")
    return raw


def validate(raw: dict, *, sid: str | None = None) -> dict:
    """A clean schedule dict, or ValueError with a message fit for the panel."""
    if not isinstance(raw, dict):
        raise ScheduleError("not_object")
    sid = sid or str(raw.get("id") or "")
    if not _ID.match(sid):
        sid = secrets.token_hex(4)

    name = " ".join(str(raw.get("name") or "").split())[:NAME_MAX]
    if not name:
        raise ScheduleError("no_name")
    wins = raw.get("windows")
    wins = wins if isinstance(wins, list) else []
    if len(wins) > MAX_WINDOWS:
        raise ScheduleError("too_many_windows")
    windows_ = [validate_window(w) for w in wins]
    plan = bool(raw.get("plan_times"))
    if not windows_ and not plan:
        raise ScheduleError("no_windows")
    return {
        "id": sid,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "windows": windows_,
        "plan_times": plan,
        "plan_before": _plan_minutes(raw.get("plan_before"), PLAN_BEFORE),
        "plan_after": _plan_minutes(raw.get("plan_after"), PLAN_AFTER),
    }


def _from_legacy(item: dict) -> dict:
    """A pre-#526 entry (one flat window with its own zone) as a named schedule."""
    if "windows" in item or "start_time" not in item:
        return item
    keys = ("repeat", "days", "start_date", "end_date", "start_time", "stop_time")
    name = str(item.get("name") or "").strip() or _legacy_name(item)
    return {**{k: v for k, v in item.items() if k not in keys and k != "timezone"},
            "name": name, "windows": [{k: item.get(k) for k in keys if k in item}]}


def _legacy_name(item: dict) -> str:
    days = item.get("days") or []
    if item.get("repeat") == "once" or not days:
        return "One-time schedule"
    return "Every " + ", ".join(DAY_ABBR[d] for d in sorted(days) if isinstance(d, int) and 0 <= d <= 6)


def legacy_zone(raw) -> str | None:
    """The zone of the first pre-#526 schedule that carried one, so Relay's one zone can start from it."""
    for item in raw if isinstance(raw, list) else []:
        z = item.get("timezone") if isinstance(item, dict) else None
        if isinstance(z, str) and z in TZ_LABELS:
            return z
    return None


def clean_list(raw) -> list[dict]:
    """The tolerant path for config load: a hand-edited entry that does not
    validate is dropped rather than failing start-up. Ids are kept unique, and a
    pre-#526 flat schedule becomes a one-window named schedule."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        # A hand-added entry with no id gets one derived from its content, so
        # it keeps the same id across reloads until the panel next saves it.
        sid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if not _ID.match(sid):
            blob = json.dumps(item, sort_keys=True, default=str).encode()
            sid = hashlib.sha1(blob, usedforsecurity=False).hexdigest()[:8]
        try:
            s = validate(_from_legacy(item) if isinstance(item, dict) else item, sid=sid)
        except ValueError:
            continue
        n = 1
        while s["id"] in seen:
            s["id"] = f"{sid}-{n}"
            n += 1
        seen.add(s["id"])
        out.append(s)
    return out


def find(scheds: list[dict], value: object) -> dict | None:
    """The schedule a ref (or a bare id) points at, or None."""
    sid = ref_id(value) or (value if isinstance(value, str) else "")
    return next((s for s in scheds if s["id"] == sid), None)


# ------------------------------------------------------------ occurrences
@dataclass
class Window:
    start: datetime  # UTC
    stop: datetime  # UTC
    names: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    tz: str = DEFAULT_TIMEZONE  # the Relay zone, for labels


def _hm(s: str) -> time:
    mins = timewin.minutes(s)
    return time(mins // 60, mins % 60)


def localize(d: date, t: time, tz: ZoneInfo) -> datetime:
    """Wall-clock `d t` in `tz`, as UTC.

    A time that does not exist (the spring-forward gap) moves to the next
    minute that does. A time that happens twice (fall back) takes the first
    occurrence, so the window still runs once.
    """
    naive = datetime.combine(d, t)
    for step in range(0, 181):
        cand = naive + timedelta(minutes=step)
        aware = cand.replace(tzinfo=tz, fold=0)
        if aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == cand:
            return aware.astimezone(UTC)
    return naive.replace(tzinfo=tz).astimezone(UTC)  # pragma: no cover


def display_name(s: dict) -> str:
    return s.get("name") or summary(s)


def _window_dates(w: dict, frm: datetime, to: datetime, tz: ZoneInfo) -> list[date]:
    first = _parse_date(w.get("start_date"), "")
    last = _parse_date(w.get("end_date"), "")
    if w["repeat"] == "once":
        return [first] if first else []
    # One day back: an overnight window that began yesterday is live now.
    d = frm.astimezone(tz).date() - timedelta(days=1)
    end = to.astimezone(tz).date()
    days = set(w["days"])
    dates = []
    while d <= end:
        if timewin.weekday(d) in days and (first is None or d >= first) and (last is None or d <= last):
            dates.append(d)
        d += timedelta(days=1)
    return dates


def occurrences(s: dict, frm: datetime, to: datetime, tz_name: str = DEFAULT_TIMEZONE,
                plan: PlanSource | None = None) -> list[Window]:
    """Every window of one schedule that overlaps [frm, to), in the Relay zone `tz_name`."""
    tz = ZoneInfo(tz_name)
    out = []
    for w in s["windows"]:
        start_t, stop_t = _hm(w["start_time"]), _hm(w["stop_time"])
        overnight = stop_t <= start_t
        for d in _window_dates(w, frm, to, tz):
            a = localize(d, start_t, tz)
            b = localize(d + timedelta(days=1) if overnight else d, stop_t, tz)
            if b > frm and a < to and b > a:
                out.append(Window(a, b, [display_name(s)], [s["id"]], tz_name))
    if s.get("plan_times") and plan is not None:
        before, after = timedelta(minutes=s["plan_before"]), timedelta(minutes=s["plan_after"])
        for at, _ in plan(frm - after, to + before):
            a, b = at - before, at + after
            if b > frm and a < to:
                out.append(Window(a, b, [display_name(s)], [s["id"]], tz_name))
    return out


def windows(scheds: list[dict], frm: datetime, to: datetime, tz_name: str = DEFAULT_TIMEZONE,
            plan: PlanSource | None = None) -> list[Window]:
    """Enabled schedules' windows over [frm, to), with overlapping or
    back-to-back ones merged -- two services that touch run as one session
    rather than stopping and restarting on the boundary."""
    raw: list[Window] = []
    for s in scheds:
        if s.get("enabled"):
            raw.extend(occurrences(s, frm, to, tz_name, plan))
    raw.sort(key=lambda w: (w.start, w.stop))
    merged: list[Window] = []
    for w in raw:
        if merged and w.start <= merged[-1].stop:
            m = merged[-1]
            m.stop = max(m.stop, w.stop)
            for n, i in zip(w.names, w.ids):
                if i not in m.ids:
                    m.names.append(n)
                    m.ids.append(i)
        else:
            merged.append(w)
    return merged


def current_window(scheds: list[dict], now: datetime, tz_name: str = DEFAULT_TIMEZONE,
                   plan: PlanSource | None = None) -> Window | None:
    for w in windows(scheds, now - timedelta(days=2), now + timedelta(days=2), tz_name, plan):
        if w.start <= now < w.stop:
            return w
    return None


def next_window(scheds: list[dict], now: datetime, tz_name: str = DEFAULT_TIMEZONE,
                plan: PlanSource | None = None) -> Window | None:
    """The first window that starts after `now`."""
    horizon = LOOKAHEAD_DAYS
    for s in scheds:
        if not s.get("enabled"):
            continue
        for w in s["windows"]:
            if w["repeat"] == "once" and w.get("start_date"):
                ahead = (date.fromisoformat(w["start_date"]) - now.date()).days + 2
                horizon = max(horizon, min(ahead, ONCE_HORIZON_DAYS))
    for w in windows(scheds, now, now + timedelta(days=horizon), tz_name, plan):
        if w.start > now:
            return w
    return None


def is_open(s: dict, now: datetime, tz_name: str = DEFAULT_TIMEZONE, plan: PlanSource | None = None) -> bool:
    """Whether schedule `s` (enabled or not -- the caller decides) has a window covering `now`."""
    return current_window([dict(s, enabled=True)], now, tz_name, plan) is not None


def is_open_local(s: dict, local_now: datetime, tz_name: str = DEFAULT_TIMEZONE, plan: PlanSource | None = None) -> bool:
    """`is_open` for a naive wall-clock time in the Relay zone (boards and signage work in local time)."""
    at = localize(local_now.date(), local_now.time().replace(second=0, microsecond=0), ZoneInfo(tz_name))
    return is_open(s, at, tz_name, plan)


def is_expired(s: dict, now: datetime, tz_name: str = DEFAULT_TIMEZONE) -> bool:
    """True when the schedule has no window left to run (a plan-times schedule never expires)."""
    if s.get("plan_times"):
        return False
    lasts = []
    for w in s["windows"]:
        last = w.get("start_date") if w["repeat"] == "once" else w.get("end_date")
        if not last:
            return False
        lasts.append(date.fromisoformat(last))
    if not lasts:
        return False
    to = datetime.combine(max(lasts) + timedelta(days=2), time(), UTC)
    return not occurrences(s, now, max(to, now + timedelta(days=LOOKAHEAD_DAYS)), tz_name)


# ------------------------------------------------------------ US formatting
def fmt_date(iso: str | None) -> str:
    """YYYY-MM-DD -> MM/DD/YYYY."""
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def fmt_time(hhmm: str) -> str:
    """HH:MM (24-hour) -> h:mm AM/PM."""
    return timewin.us_clock(hhmm)


def fmt_instant(dt: datetime, tz: str) -> str:
    """A UTC instant as US wall-clock in `tz`: 'Sun 09/27/2026 10:00 AM CDT'."""
    loc = dt.astimezone(ZoneInfo(tz))
    day = DAY_ABBR[timewin.weekday(loc)]
    return (f"{day} {loc.month:02d}/{loc.day:02d}/{loc.year} "
            f"{fmt_time(f'{loc.hour:02d}:{loc.minute:02d}')} {loc.tzname()}")


def window_summary(w: dict) -> str:
    """One window as a line: 'Every Sun, Wed · 10:00 AM – 12:00 PM'."""
    span = f"{fmt_time(w['start_time'])} – {fmt_time(w['stop_time'])}"
    if _hm(w["stop_time"]) <= _hm(w["start_time"]):
        span += " (next day)"
    if w["repeat"] == "once":
        return f"Once on {fmt_date(w['start_date'])} · {span}"
    days = w["days"]
    when = "Every day" if len(days) == 7 else "Every " + ", ".join(DAY_ABBR[d] for d in days)
    bounds = ""
    if w.get("start_date") and w.get("end_date"):
        bounds = f", {fmt_date(w['start_date'])} to {fmt_date(w['end_date'])}"
    elif w.get("start_date"):
        bounds = f", from {fmt_date(w['start_date'])}"
    elif w.get("end_date"):
        bounds = f", until {fmt_date(w['end_date'])}"
    return f"{when} · {span}{bounds}"


def summary(s: dict) -> str:
    """The schedule's windows in one line for the list."""
    parts = [window_summary(w) for w in s["windows"]]
    if s.get("plan_times"):
        parts.append(f"Planning Center service times ({s['plan_before']} min before to {s['plan_after']} min after)")
    return "; ".join(parts)
