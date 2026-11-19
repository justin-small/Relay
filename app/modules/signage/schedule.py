"""Signage schedules (#206): which playlist a screen plays right now, and why.

Pure functions only; store.py reads the rows and calls in here. The order of precedence, highest first:

    takeover        another module forcing the screen (alerts, live service; #207). Registered with `register_layer`, or provided as "signage.layer.takeover".
    tag             a slide-note tag naming a playlist (#208). ProPresenter provides it as "signage.layer.tag".
    override        a playlist an operator chose by hand, until cleared or until the next scheduled change.
    schedule        the time slot that matches now: a screen's own slots, else its group's, else the all-screens ones.
                    Within one level a one-off date range beats a weekly slot, and the later start wins a tie.
    default         the group's playlist when it has one, else the screen's own.

Times are wall-clock in the signage time zone (a US zone from the Settings tab), so "Sunday 9:00" is 9:00 on both
sides of a daylight-saving change. A slot whose end is not after its start runs into the next day. On the night the
clocks go forward a slot that starts in the skipped hour starts at the first minute after it; when they go back, the
repeated hour is simply played twice. Days count from Sunday = 0, as in the rest of Relay.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from ... import modules as core, timewin

TARGETS = ("screen", "group", "all")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
HORIZON_DAYS = 8
TAKEOVER, TAG = "takeover", "tag"
LAYER_ORDER = (TAKEOVER, TAG)

# name -> fn(screen: dict, now_utc: datetime) -> {"playlist_id": int, "why": str} | None. Another module provides its
# layer as the capability "signage.layer.<name>" (#523); `register_layer` is for signage's own.
LAYER = "signage.layer."
_layers: dict[str, Callable[[dict, datetime], dict | None]] = {}


class Refused(ValueError):
    """A slot signage will not save. The text is a fixed sentence for the admin page."""


def register_layer(name: str, fn: Callable[[dict, datetime], dict | None]) -> None:
    """Let another module claim a screen above the schedule. `name` is "takeover" or "tag"."""
    if name not in LAYER_ORDER:
        raise ValueError("unknown signage layer")
    _layers[name] = fn


def unregister_layer(name: str) -> None:
    _layers.pop(name, None)


def layer_result(screen: dict, now_utc: datetime) -> dict | None:
    """The first registered layer, in precedence order, that claims `screen`: {"layer", "playlist_id", "why"}."""
    for name in LAYER_ORDER:
        fn = _layers.get(name) or core.using(LAYER + name)
        if fn is None:
            continue
        try:
            got = fn(screen, now_utc)
        except Exception:
            continue
        if got and got.get("playlist_id"):
            return {"layer": name, "playlist_id": int(got["playlist_id"]), "why": str(got.get("why") or "")}
    return None


# ------------------------------------------------------------------ time

def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("America/New_York")


def local(now_utc: datetime, tz: str) -> datetime:
    """`now_utc` as a naive wall-clock datetime in the signage zone."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(zone(tz)).replace(tzinfo=None)




# ------------------------------------------------------------------ slots

def clean_slot(raw: dict) -> dict:
    """Validate a slot from the admin page into the columns the table holds. Raises Refused."""
    if not isinstance(raw, dict):
        raise Refused("A slot must be an object.")
    target = str(raw.get("target") or "")
    if target not in TARGETS:
        raise Refused("Choose a screen, a group or all screens.")
    try:
        target_id = int(raw.get("target_id") or 0) if target != "all" else 0
    except (TypeError, ValueError):
        raise Refused("Choose a screen or a group.") from None
    if target != "all" and target_id <= 0:
        raise Refused("Choose a screen or a group.")
    try:
        playlist_id = int(raw.get("playlist_id") or 0)
    except (TypeError, ValueError):
        raise Refused("Choose a playlist.") from None
    if playlist_id <= 0:
        raise Refused("Choose a playlist.")
    start_date = str(raw.get("start_date") or "").strip()
    end_date = str(raw.get("end_date") or "").strip()
    for d in (start_date, end_date):
        if d and not DATE_RE.match(d):
            raise Refused("Dates are written YYYY-MM-DD.")
        if d:
            try:
                date.fromisoformat(d)
            except ValueError:
                raise Refused("That date does not exist.") from None
    if bool(start_date) != bool(end_date):
        raise Refused("A date range needs both a first and a last day.")
    if start_date and end_date < start_date:
        raise Refused("The range ends before it starts.")
    start_time = str(raw.get("start_time") or "").strip()
    end_time = str(raw.get("end_time") or "").strip()
    if bool(start_time) != bool(end_time):
        raise Refused("Give both a start and an end time, or neither for the whole day.")
    for t in (start_time, end_time):
        if t and not timewin.is_clock(t):
            raise Refused("Times are written HH:MM, 24-hour.")
    if start_time and start_time == end_time:
        raise Refused("The start and end time must differ.")
    days_raw = raw.get("days")
    days_raw = days_raw if isinstance(days_raw, (list, tuple)) else []
    days: set[int] = set()
    for d in days_raw:
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6:
            raise Refused("Days are 0 (Sunday) to 6 (Saturday).")
        days.add(d)
    if not start_date and not days:
        raise Refused("Choose the days of the week, or a range of dates.")
    return {"target": target, "target_id": target_id, "playlist_id": playlist_id,
            "days": ",".join(str(d) for d in sorted(days)), "start_time": start_time, "end_time": end_time,
            "start_date": start_date, "end_date": end_date}


def is_range(slot: dict) -> bool:
    return bool(slot["start_date"])


def _occurrence_start(slot: dict, now: datetime) -> datetime | None:
    """When the run of `slot` that covers wall-clock `now` began, or None if it does not cover it."""
    days = {int(d) for d in slot["days"].split(",") if d != ""}
    timed = bool(slot["start_time"])
    starts, ends = (timewin.minutes(slot["start_time"]), timewin.minutes(slot["end_time"])) if timed else (0, 0)

    def day_ok(d: date) -> bool:
        if days and timewin.weekday(d) not in days:
            return False
        return not slot["start_date"] or slot["start_date"] <= d.isoformat() <= slot["end_date"]

    return timewin.window_start(now, starts, ends, day_ok)


def _level(slot: dict, screen: dict) -> int | None:
    if slot["target"] == "screen":
        return 0 if slot["target_id"] == screen["id"] else None
    if slot["target"] == "group":
        return 1 if screen.get("group_id") and slot["target_id"] == screen["group_id"] else None
    return 2


def matching(slots: list[dict], screen: dict, now: datetime) -> list[dict]:
    """The slots that apply to `screen` at wall-clock `now`, best first."""
    found = []
    for s in slots:
        level = _level(s, screen)
        if level is None:
            continue
        began = _occurrence_start(s, now)
        if began is None:
            continue
        found.append(((level, 0 if is_range(s) else 1, -began.timestamp(), s["id"]), s))
    found.sort(key=lambda p: p[0])
    return [s for _, s in found]


def describe_slot(slot: dict) -> str:
    """"Sun, Wed 9:00 AM-11:30 AM", "Dec 20-Jan 3 all day", for the admin page."""
    when = f"{timewin.us_clock(slot['start_time'])}-{timewin.us_clock(slot['end_time'])}" if slot["start_time"] else "all day"
    if is_range(slot):
        span = slot["start_date"] if slot["start_date"] == slot["end_date"] else f"{slot['start_date']} to {slot['end_date']}"
        days = ""
        if slot["days"]:
            days = ", ".join(DAY_NAMES[int(d)] for d in slot["days"].split(",")) + " "
        return f"{span}, {days}{when}"
    ds = [int(d) for d in slot["days"].split(",")]
    names = "Every day" if len(ds) == 7 else ", ".join(DAY_NAMES[d] for d in ds)
    return f"{names} {when}"


def next_change(key: Callable[[datetime], object], start_utc: datetime, tz: str) -> datetime | None:
    """The first minute after `start_utc` at which `key(wall-clock now)` differs from its value at `start_utc`, or
    None when it does not change within HORIZON_DAYS. Steps in UTC, so a daylight-saving change is no special case."""
    base = key(local(start_utc, tz))
    t = start_utc.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(HORIZON_DAYS * 24 * 60):
        if key(local(t, tz)) != base:
            return t
        t += timedelta(minutes=1)
    return None
