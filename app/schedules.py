"""Scheduled start/stop windows: validation, occurrence maths, US formatting.

Pure functions only -- nothing here touches the engine, so config.py can use
it to validate the `schedules` list without an import cycle. The runner that
acts on these windows is scheduler.py.

A schedule is stored in config.json as:

    {"id": "a1b2c3d4", "name": "Sunday morning", "enabled": true,
     "repeat": "weekly", "days": [0], "start_date": null, "end_date": null,
     "start_time": "10:00", "stop_time": "12:00",
     "timezone": "America/Chicago"}

Dates are ISO (YYYY-MM-DD) and times 24-hour (HH:MM) on disk, so parsing never
depends on a locale; the panel shows and takes them in US formats (MM/DD/YYYY,
h:mm AM/PM). `days` counts from Sunday = 0, the US week. A stop time earlier
than the start time is the next day. Times are wall-clock in the schedule's own
zone, so "10:00 AM Sunday" stays 10:00 AM local on both sides of a DST change.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

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

DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
REPEATS = ("weekly", "once")
NAME_MAX = 60
_ID = re.compile(r"[A-Za-z0-9_-]{1,32}\Z")
_TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)\Z")
# How far ahead the panel looks for the next start. A weekly schedule always
# recurs inside 8 days; a one-off can be further out, up to a year.
LOOKAHEAD_DAYS = 8
ONCE_HORIZON_DAYS = 366


def default_timezone(env_tz: str | None = None) -> str:
    """The zone a new schedule starts on: the host's TZ if it is a US zone we
    offer, else Eastern. The container rarely carries the host's zone, so the
    fallback is the common case."""
    return env_tz if env_tz in TZ_LABELS else DEFAULT_TIMEZONE


# ------------------------------------------------------------ validation
def _parse_date(raw, what: str) -> date | None:
    if raw in (None, ""):
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ValueError(f"{what} is not a valid date.") from None


def _parse_time(raw, what: str) -> str:
    m = _TIME.match(str(raw or "").strip())
    if not m:
        raise ValueError(f"{what} is not a valid time.")
    return m.group(0)


def validate(raw: dict, *, sid: str | None = None) -> dict:
    """A clean schedule dict, or ValueError with a message fit for the panel."""
    if not isinstance(raw, dict):
        raise ValueError("A schedule must be an object.")
    sid = sid or str(raw.get("id") or "")
    if not _ID.match(sid):
        sid = secrets.token_hex(4)

    name = " ".join(str(raw.get("name") or "").split())[:NAME_MAX]
    repeat = str(raw.get("repeat") or "weekly").lower()
    if repeat not in REPEATS:
        raise ValueError("Repeat must be weekly or once.")

    tz = str(raw.get("timezone") or "")
    if tz not in TZ_LABELS:
        raise ValueError("Pick a US time zone.")

    start_time = _parse_time(raw.get("start_time"), "Start time")
    stop_time = _parse_time(raw.get("stop_time"), "Stop time")
    if start_time == stop_time:
        raise ValueError("Start and stop time must differ.")

    start_date = _parse_date(raw.get("start_date"), "Start date")
    end_date = _parse_date(raw.get("end_date"), "End date")

    days: list[int] = []
    if repeat == "weekly":
        try:
            days = sorted({int(d) for d in (raw.get("days") or [])})
        except (TypeError, ValueError):
            raise ValueError("Days must be 0 (Sunday) to 6 (Saturday).") from None
        if not days:
            raise ValueError("Pick at least one day of the week.")
        if days[0] < 0 or days[-1] > 6:
            raise ValueError("Days must be 0 (Sunday) to 6 (Saturday).")
        if start_date and end_date and end_date < start_date:
            raise ValueError("End date is before the start date.")
    else:
        if start_date is None:
            raise ValueError("A one-time schedule needs a date.")
        end_date = None

    return {
        "id": sid,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "repeat": repeat,
        "days": days,
        "start_date": start_date.isoformat() if start_date else None,
        "end_date": end_date.isoformat() if end_date else None,
        "start_time": start_time,
        "stop_time": stop_time,
        "timezone": tz,
    }


def clean_list(raw) -> list[dict]:
    """The tolerant path for config load: a hand-edited entry that does not
    validate is dropped rather than failing start-up. Ids are kept unique."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        # A hand-added entry with no id gets one derived from its content, so
        # it keeps the same id across reloads until the panel next saves it.
        sid = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
        if not _ID.match(sid):
            blob = json.dumps(item, sort_keys=True, default=str).encode()
            sid = hashlib.sha1(blob).hexdigest()[:8]
        try:
            s = validate(item, sid=sid)
        except ValueError:
            continue
        n = 1
        while s["id"] in seen:
            s["id"] = f"{sid}-{n}"
            n += 1
        seen.add(s["id"])
        out.append(s)
    return out


# ------------------------------------------------------------ occurrences
@dataclass
class Window:
    start: datetime  # UTC
    stop: datetime  # UTC
    names: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    tz: str = DEFAULT_TIMEZONE  # zone of the first schedule, for labels


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


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


def occurrences(s: dict, frm: datetime, to: datetime) -> list[Window]:
    """Every window of one schedule that overlaps [frm, to)."""
    tz = ZoneInfo(s["timezone"])
    start_t, stop_t = _hm(s["start_time"]), _hm(s["stop_time"])
    overnight = stop_t <= start_t
    first = _parse_date(s.get("start_date"), "")
    last = _parse_date(s.get("end_date"), "")

    if s["repeat"] == "once":
        dates = [first] if first else []
    else:
        # One day back: an overnight window that began yesterday is live now.
        d = frm.astimezone(tz).date() - timedelta(days=1)
        end = to.astimezone(tz).date()
        days = set(s["days"])
        dates = []
        while d <= end:
            if (d.weekday() + 1) % 7 in days \
                    and (first is None or d >= first) and (last is None or d <= last):
                dates.append(d)
            d += timedelta(days=1)

    out = []
    for d in dates:
        a = localize(d, start_t, tz)
        b = localize(d + timedelta(days=1) if overnight else d, stop_t, tz)
        if b > frm and a < to and b > a:
            out.append(Window(a, b, [display_name(s)], [s["id"]], s["timezone"]))
    return out


def windows(scheds: list[dict], frm: datetime, to: datetime) -> list[Window]:
    """Enabled schedules' windows over [frm, to), with overlapping or
    back-to-back ones merged -- two services that touch run as one session
    rather than stopping and restarting on the boundary."""
    raw: list[Window] = []
    for s in scheds:
        if s.get("enabled"):
            raw.extend(occurrences(s, frm, to))
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


def current_window(scheds: list[dict], now: datetime) -> Window | None:
    for w in windows(scheds, now - timedelta(days=2), now + timedelta(days=2)):
        if w.start <= now < w.stop:
            return w
    return None


def next_window(scheds: list[dict], now: datetime) -> Window | None:
    """The first window that starts after `now`."""
    horizon = LOOKAHEAD_DAYS
    for s in scheds:
        if s.get("enabled") and s["repeat"] == "once" and s.get("start_date"):
            ahead = (date.fromisoformat(s["start_date"]) - now.date()).days + 2
            horizon = max(horizon, min(ahead, ONCE_HORIZON_DAYS))
    for w in windows(scheds, now, now + timedelta(days=horizon)):
        if w.start > now:
            return w
    return None


def is_expired(s: dict, now: datetime) -> bool:
    """True when the schedule has no window left to run."""
    last = s.get("start_date") if s["repeat"] == "once" else s.get("end_date")
    if not last:
        return False
    to = datetime.combine(date.fromisoformat(last) + timedelta(days=2), time(), UTC)
    return not occurrences(s, now, max(to, now + timedelta(days=LOOKAHEAD_DAYS)))


# ------------------------------------------------------------ US formatting
def fmt_date(iso: str | None) -> str:
    """YYYY-MM-DD -> MM/DD/YYYY."""
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def fmt_time(hhmm: str) -> str:
    """HH:MM (24-hour) -> h:mm AM/PM."""
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def fmt_instant(dt: datetime, tz: str) -> str:
    """A UTC instant as US wall-clock in `tz`: 'Sun 09/27/2026 10:00 AM CDT'."""
    loc = dt.astimezone(ZoneInfo(tz))
    day = DAY_ABBR[(loc.weekday() + 1) % 7]
    return (f"{day} {loc.month:02d}/{loc.day:02d}/{loc.year} "
            f"{fmt_time(f'{loc.hour:02d}:{loc.minute:02d}')} {loc.tzname()}")


def summary(s: dict) -> str:
    """One line for the schedule list:
    'Every Sun, Wed · 10:00 AM – 12:00 PM Central'."""
    span = f"{fmt_time(s['start_time'])} – {fmt_time(s['stop_time'])}"
    if _hm(s["stop_time"]) <= _hm(s["start_time"]):
        span += " (next day)"
    zone = TZ_LABELS.get(s["timezone"], s["timezone"]).split(" – ")[0]
    if s["timezone"] == "America/Phoenix":
        zone = "Arizona"
    if s["repeat"] == "once":
        return f"Once on {fmt_date(s['start_date'])} · {span} {zone}"
    days = s["days"]
    when = "Every day" if len(days) == 7 else "Every " + ", ".join(DAY_ABBR[d] for d in days)
    bounds = ""
    if s.get("start_date") and s.get("end_date"):
        bounds = f", {fmt_date(s['start_date'])} to {fmt_date(s['end_date'])}"
    elif s.get("start_date"):
        bounds = f", from {fmt_date(s['start_date'])}"
    elif s.get("end_date"):
        bounds = f", until {fmt_date(s['end_date'])}"
    return f"{when} · {span} {zone}{bounds}"
