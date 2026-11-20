"""Boards (#156): screens an operator builds from widgets, each at its own stable address, /board/<slug>.

A board is one or more pages, each a grid (app/layout.py) of widgets laid out with the shared editor (#421). It is
kept in config.json under "boards", so a backup carries it:

    {"id": "lobby", "name": "Lobby", "place": "Foyer", "public": false, "ratio": "16:9", "cols": 12, "rows": 8,
     "pages": [{"id": "p1", "name": "Welcome",
                "items": [{"id": "a1", "type": "captions", "x": 0, "y": 0, "w": 8, "h": 3,
                           "style": {...app/layout.py...}, "props": {"lang": "SOURCE", "lines": 3}}]}],
     "rotation": {"mode": "one" | "rotate" | "schedule" | "follow", "every": 20, "pages": ["p1", "p2"],
                  "schedule": [{"days": "all", "from": "09:00", "to": "10:30", "show": "p2" | "rotate"}],
              (a row may instead carry "schedule": "schedule:relay:<id>", a named schedule: it covers the board
              while that schedule is open, and its own days and times are not used)
                  "other": "p1",
                  "follow": {"source": "propresenter", "rules": [{"match": "group", "value": "Sermon", "page": "p3"}],
                             "none": "keep" | "p1"}},
     "screen_names": {"<a screen's own id>": "Foyer TV left"}}

`place` is where the screen hangs (Foyer, Stage); the Boards page groups by it. Which page a screen shows is decided
here (`current`), so every screen showing a board agrees; a rotation turns from the clock. A board saved before pages
had only `items`: they become page 1.

`id` is the address and never changes once made, so a TV keeps its link through a rename. A board is opened by anyone
signed in to Relay (`boards.view`, every role) on the operator port, or by anyone at all on either port when it is
`public`; it is changed only by `boards.manage` (admin).

Widgets. Core's are below (WIDGETS): captions in any language, a clock, a countdown, text, a link to another board and
the livestream's status, and the room's sound level and spectrum (app/metering.py). A module adds its own by overriding `Module.board_widgets()`; its widget types start with the
module's id ("lighting.cue"), and core asks the module for their live data and presses (`board_data`,
`board_press`) without importing it. A widget whose module is off stays on the board, drawn as off, and comes back when
the module does.

A widget's settings ("props") are checked against the fields its definition declares, so a browser can only choose
among the values offered. On config load (when the modules' definitions are not known yet) they are only bounded:
plain values, short text.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from collections.abc import Callable
from typing import Any, Iterable

from . import layout, schedules, text, timewin

MAX_BOARDS = 50
MAX_ITEMS = 100
NAME_MAX = 60
TEXT_MAX = 200
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31})?")
_PROP_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
RATIOS = {"16:9": (12, 8), "4:3": (8, 6), "9:16": (6, 10)}
KINDS = ("button", "captions", "clock", "countdown", "text", "status", "lines", "list", "spl", "rta", "frame")
FIELD_TYPES = ("text", "area", "select", "check", "time", "number", "note", "ro")
SOURCE = "SOURCE"            # the captions widget's value for the original speech
MAX_PAGES = 12
PAGE_NAME_MAX = 40
PLACE_MAX = 40
_PLACE_REF = re.compile(r"place:relay:\d{1,9}")   # the core place (app/screens.py) this board hangs in
MAX_RULES = 30
RULE_VALUE_MAX = 80
MAX_SCREENS = 50
SCREEN_NAME_MAX = 40
EVERY_MIN, EVERY_MAX = 5, 3600
_PAGE_ID = re.compile(r"[a-z0-9]{1,12}")
SCREEN_ID = re.compile(r"[A-Za-z0-9_-]{8,40}")
MODES = ("one", "rotate", "schedule", "follow")
ROTATE = "rotate"            # a schedule row that shows the rotation
KEEP = "keep"                # following: when nothing matches, the page stays
DAYS = {"all": "Every day", "weekdays": "Weekdays", "weekends": "Weekends", "mon": "Monday", "tue": "Tuesday",
        "wed": "Wednesday", "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday"}
# Each one-day choice as its weekday number (Sunday = 0, see app/timewin.py).
DAY_NUMBERS = {k: i for i, k in enumerate(timewin.DAY_KEYS)}
# The background colours a widget's settings card offers (the full look has a colour picker).
SWATCHES = ("#18181b", "#1e3a8a", "#14532d", "#7f1d1d", "#713f12", "#3f3f46")
MATCHES = {"presentation": "Presentation", "group": "Slide group", "label": "Slide label"}


class BoardError(ValueError):
    """A refusal; the text is a fixed sentence fit for the page."""


# --------------------------------------------------------------------------------------------- widgets
def _languages() -> list[list[str]]:
    from . import config, languages

    src = config.get().get("source_language", "ENGLISH")
    label = languages.SOURCE_LANGUAGES.get(src, {}).get("label", "Original")
    return [[SOURCE, f"{label} (original speech)"]] + [[k, v["label"]] for k, v in languages.TARGETS.items()]


def _sound_sources() -> list[list[str]]:
    """Where a sound widget can read: Relay's own meter, then each module's registered source (#149)."""
    from . import metering

    return [["", "Relay's own meter"]] + [[k, v] for k, v in metering.sources()]


def _route_options(direction: str) -> list[list[str]]:
    """[[channel ref, name]] for every audio and video channel that gives (source) or takes (destination) a signal."""
    from . import channels, db

    try:
        return [[c["ref"], c["shown"]] for c in channels.listing()
                if c["kind"] in ("audio", "video") and any(e["direction"] == direction for e in c["ends"])]
    except db.Unavailable:
        return []


def core_widgets() -> list[dict]:
    """Core's widget definitions. Asked for each time, since the caption languages follow the configuration."""
    return [
        {"type": "captions", "name": "Captions", "group": "Captions", "kind": "captions", "w": 8, "h": 3,
         "style": {"icon": "cc"},
         "fields": [{"key": "lang", "label": "Language", "type": "select", "options": _languages(), "default": SOURCE},
                    {"key": "lines", "label": "Lines shown", "type": "select",
                     "options": [[str(n), str(n)] for n in range(1, 7)], "default": "3"}]},
        {"type": "clock", "name": "Clock", "group": "Clock and text", "kind": "clock", "w": 3, "h": 2,
         "style": {"icon": "clock"},
         "fields": [{"key": "h24", "label": "24-hour clock", "type": "check", "default": False}]},
        {"type": "countdown", "name": "Countdown", "group": "Clock and text", "kind": "countdown", "w": 3, "h": 2,
         "style": {"icon": "clock", "label": "Starts in"},
         "fields": [{"key": "at", "label": "Counts down to (time of day)", "type": "time", "default": "10:30"}]},
        {"type": "text", "name": "Text", "group": "Clock and text", "kind": "text", "w": 4, "h": 1,
         "fields": [{"key": "text", "label": "Text", "type": "area", "max": TEXT_MAX, "default": "Welcome"}]},
        {"type": "link", "name": "Board link", "group": "Clock and text", "kind": "button", "w": 2, "h": 1,
         "style": {"icon": "arrow", "bg": "#1e3a8a", "pressed": "#3b5bdb"},
         "fields": [{"key": "board", "label": "Opens board", "type": "select", "options": [], "default": ""}]},
        {"type": "route", "name": "Route", "group": "Routing", "kind": "button", "w": 3, "h": 1,
         "style": {"icon": "arrow", "bg": "#14532d", "pressed": "#16a34a"}, "press_permission": "network.manage",
         "fields": [{"key": "destination", "label": "Destination (what takes the signal)", "type": "select",
                     "options": _route_options("destination"), "default": ""},
                    {"key": "source", "label": "Source (what it takes)", "type": "select",
                     "options": _route_options("source"), "default": ""},
                    {"key": "_", "type": "note", "label": "A press routes the source to the destination, as on the NDI "
                     "and Blackmagic pages, and is not retried. Pressing needs someone signed in who may manage routes."}]},
        {"type": "stream", "name": "Livestream", "group": "Status", "kind": "status", "w": 3, "h": 2,
         "style": {"icon": "cam"},
         "fields": [{"key": "_", "label": "On air when any stream Relay watches is live (OBS, YouTube...).",
                     "type": "note"}]},
        {"type": "spl", "name": "Sound level", "group": "Sound", "kind": "spl", "w": 3, "h": 2,
         "style": {"icon": "mic"},
         "fields": [{"key": "weighting", "label": "Weighting", "type": "select", "default": "A",
                     "options": [["A", "Sound level, A-weighted"], ["C", "Sound level, C-weighted"]]},
                    {"key": "response", "label": "Response", "type": "select", "default": "slow",
                     "options": [["slow", "Slow (1 s)"], ["fast", "Fast (125 ms)"], ["leq", "Average, last minute (A)"]]},
                    {"key": "source", "label": "Read from", "type": "select", "default": "",
                     "options": _sound_sources()}]},
        {"type": "rta", "name": "Spectrum", "group": "Sound", "kind": "rta", "w": 6, "h": 3,
         "style": {"icon": "mic"},
         "fields": [{"key": "view", "label": "Shown as", "type": "select", "default": "bars",
                     "options": [["bars", "Room spectrum (RTA)"], ["waterfall", "Spectrogram"]]},
                    {"key": "source", "label": "Read from", "type": "select", "default": "",
                     "options": _sound_sources()}]},
    ]


def clean_definition(raw: Any, owner: str = "") -> dict | None:
    """A widget definition as the editor and the checks use it, or None when it is not usable. A module's types must
    start with `owner.`."""
    if not isinstance(raw, dict):
        return None
    wtype = raw.get("type")
    if not isinstance(wtype, str) or not _TYPE.fullmatch(wtype):
        return None
    if owner and not wtype.startswith(owner + "."):
        return None
    if not owner and "." in wtype:
        return None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "text"
    fields = []
    for f in raw.get("fields") if isinstance(raw.get("fields"), list) else []:
        if not isinstance(f, dict) or f.get("type") not in FIELD_TYPES or not isinstance(f.get("key"), str) \
                or not (f["key"] == "_" or _PROP_KEY.fullmatch(f["key"])):
            continue
        opts = [[layout.text(o[0], 80), layout.text(o[1], 80)] for o in (f.get("options") or [])
                if isinstance(o, (list, tuple)) and len(o) == 2 and isinstance(o[0], str)][:200]
        fields.append({"key": f["key"], "label": layout.text(f.get("label"), 120), "type": f["type"], "options": opts,
                       "max": layout.number(f.get("max"), 1, TEXT_MAX, TEXT_MAX), "default": f.get("default")})
    presets = []
    for p in raw.get("presets") if isinstance(raw.get("presets"), list) else []:
        if isinstance(p, dict) and layout.text(p.get("name"), 60):
            presets.append({"name": layout.text(p["name"], 60), "props": p.get("props") if isinstance(p.get("props"), dict)
                            else {}, "style": p.get("style") if isinstance(p.get("style"), dict) else {}})
    return {"type": wtype, "name": layout.text(raw.get("name"), 60) or wtype, "group": layout.text(raw.get("group"), 40),
            "kind": kind, "w": layout.number(raw.get("w"), 1, layout.MAX_CELLS, 2),
            "h": layout.number(raw.get("h"), 1, layout.MAX_CELLS, 1),
            "style": layout.clean_style(raw.get("style")), "fields": fields, "presets": presets[:200],
            "press_permission": raw.get("press_permission") if isinstance(raw.get("press_permission"), str) else "",
            "module": owner, "off_note": layout.text(raw.get("off_note"), 80)}


def catalogue(module_defs: Iterable[dict] = (), boards: list[dict] | None = None) -> dict[str, dict]:
    """Every widget type that can be placed now, by type: core's, then the switched-on modules' (already cleaned).
    The board link offers the other boards."""
    out: dict[str, dict] = {}
    for raw in core_widgets():
        d = clean_definition(raw)
        if d:
            if d["type"] == "link":
                d["fields"][0]["options"] = [[b["id"], b["name"]] for b in (boards or [])]
            out[d["type"]] = d
    for d in module_defs:
        if d and d["type"] not in out:
            out[d["type"]] = d
    return out


def item_title(definition: dict, props: dict) -> str:
    """What an item is called when it has no label of its own: the preset it matches (a cue's name), else the chosen
    option of its first list setting (a caption language), else the widget's name."""
    for p in definition.get("presets") or []:
        if p["props"] and all(props.get(k) == v for k, v in p["props"].items()):
            return p["name"]
    for f in definition.get("fields") or []:
        if f["type"] == "select":
            label = next((o[1] for o in f["options"] if o[0] == props.get(f["key"])), "")
            return label or definition["name"]
        if f["type"] != "note":
            break
    return definition["name"]


# --------------------------------------------------------------------------------------------- cleaning
def _plain(value: Any) -> Any:
    """A setting kept for a widget whose definition is not known (its module is off, or on config load)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)) and value == value:
        return max(-1e9, min(1e9, value))
    if isinstance(value, str):
        return value[:TEXT_MAX]
    return None


def clean_props(raw: Any, definition: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    if definition is None:
        return {k: _plain(v) for k, v in list(raw.items())[:20] if isinstance(k, str) and _PROP_KEY.fullmatch(k)}
    out: dict[str, Any] = {}
    for f in definition["fields"]:
        key, kind, value = f["key"], f["type"], raw.get(f["key"], f["default"])
        if kind in ("note",) or key == "_":
            continue
        if kind == "select":
            allowed = [o[0] for o in f["options"]]
            value = value if isinstance(value, str) and value in allowed else \
                (f["default"] if f["default"] in allowed else (allowed[0] if allowed else ""))
        elif kind == "check":
            value = value is True
        elif kind == "time":
            value = value if timewin.clean_clock(value, loose=True) else \
                (f["default"] if timewin.clean_clock(f["default"], loose=True) else "")
        elif kind == "number":
            value = layout.number(value, -1_000_000, 1_000_000, f["default"] if isinstance(f["default"], int) else 0)
        elif kind == "area":
            value = value[:f["max"]].strip() if isinstance(value, str) else ""
            value = "\n".join(" ".join(line.split()) for line in value.splitlines())[:f["max"]]
        else:   # text, ro
            value = layout.text(value, f["max"])
        out[key] = value
    return out


def clean_item(raw: Any, cat: dict[str, dict] | None) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or not _TYPE.fullmatch(raw["type"]):
        return None
    definition = (cat or {}).get(raw["type"])
    if cat is not None and definition is None and "." not in raw["type"]:
        return None          # an unknown core type: nothing could ever draw it
    iid = raw.get("id") if isinstance(raw.get("id"), str) and _ITEM_ID.fullmatch(raw["id"]) else uuid.uuid4().hex[:10]
    return {"id": iid, "type": raw["type"], "x": raw.get("x"), "y": raw.get("y"), "w": raw.get("w"), "h": raw.get("h"),
            "style": layout.clean_style(raw.get("style"), definition["style"] if definition else None),
            "props": clean_props(raw.get("props"), definition)}


# --------------------------------------------------------------------------------------------- pages and rotation
def _clean_items(raw: Any, cat: dict[str, dict] | None, cols: int, rows: int, seen: set[str]) -> list[dict]:
    items = []
    for it in (raw if isinstance(raw, list) else [])[:MAX_ITEMS]:
        c = clean_item(it, cat)
        if c is None:
            continue
        if c["id"] in seen:
            c["id"] = uuid.uuid4().hex[:10]
        seen.add(c["id"])
        items.append(c)
    return layout.place(items, cols, rows)


def clean_pages(raw_pages: Any, raw_items: Any, cat: dict[str, dict] | None, cols: int, rows: int) -> list[dict]:
    """A board's pages: at least one, at most MAX_PAGES, each {"id", "name", "items"}. A board saved before pages
    (#156) had only `items`: they become page 1. Widget ids are unique across the whole board, so a press finds one."""
    pages_in = raw_pages if isinstance(raw_pages, list) and raw_pages else [{"id": "p1", "name": "Page 1", "items": raw_items}]
    out, ids, seen = [], set(), set()
    for n, p in enumerate(pages_in[:MAX_PAGES], start=1):
        p = p if isinstance(p, dict) else {}
        pid = p.get("id") if isinstance(p.get("id"), str) and _PAGE_ID.fullmatch(p["id"]) and p["id"] not in ids else ""
        k = n
        while not pid or pid in ids:
            pid, k = f"p{k}", k + 1
        ids.add(pid)
        out.append({"id": pid, "name": layout.text(p.get("name"), PAGE_NAME_MAX) or f"Page {n}",
                    "items": _clean_items(p.get("items"), cat, cols, rows, seen)})
    return out


def _hhmm(value: Any, default: str) -> str:
    return timewin.clean_clock(value, loose=True) or default


def clean_rotation(raw: Any, page_ids: list[str]) -> dict:
    """What decides the page a board shows: one page, a rotation, a schedule by day and time, or following another
    program (a module's follow source, ProPresenter first). A page that no longer exists falls back to the first."""
    raw = raw if isinstance(raw, dict) else {}
    first = page_ids[0]

    def show(v: Any, extra: tuple[str, ...] = (ROTATE,)) -> str:
        return v if isinstance(v, str) and (v in page_ids or v in extra) else first

    rot_pages = [p for p in (raw.get("pages") if isinstance(raw.get("pages"), list) else []) if p in page_ids]
    schedule = []
    for r in (raw.get("schedule") if isinstance(raw.get("schedule"), list) else [])[:MAX_RULES]:
        if isinstance(r, dict):
            named = r.get("schedule")
            schedule.append({"days": r.get("days") if r.get("days") in DAYS else "all",
                             "from": _hhmm(r.get("from"), "09:00"), "to": _hhmm(r.get("to"), "10:00"),
                             "schedule": named if schedules.ref_id(named) else "",
                             "show": show(r.get("show"))})
    follow = raw.get("follow") if isinstance(raw.get("follow"), dict) else {}
    rules = []
    for r in (follow.get("rules") if isinstance(follow.get("rules"), list) else [])[:MAX_RULES]:
        if isinstance(r, dict) and layout.text(r.get("value"), RULE_VALUE_MAX):
            rules.append({"match": r.get("match") if r.get("match") in MATCHES else "presentation",
                          "value": layout.text(r.get("value"), RULE_VALUE_MAX), "page": show(r.get("page"), ())})
    source = follow.get("source") if isinstance(follow.get("source"), str) and _PROP_KEY.fullmatch(follow["source"]) \
        else "propresenter"
    return {"mode": raw.get("mode") if raw.get("mode") in MODES else "one",
            "every": layout.number(raw.get("every"), EVERY_MIN, EVERY_MAX, 20),
            "pages": list(dict.fromkeys(rot_pages)) or list(page_ids),
            "schedule": schedule, "other": show(raw.get("other")),
            "follow": {"source": source, "rules": rules,
                       "none": follow["none"] if follow.get("none") in page_ids else KEEP}}


def _clean_screen_names(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(k, str) and SCREEN_ID.fullmatch(k) and layout.text(v, SCREEN_NAME_MAX) and len(out) < MAX_SCREENS:
            out[k] = layout.text(v, SCREEN_NAME_MAX)
    return out


def clean_board(raw: Any, cat: dict[str, dict] | None = None, *, board_id: str | None = None) -> dict:
    """One board, bounded. With `cat` (the catalogue) each widget's settings are checked against its definition and a
    widget of an unknown core type is dropped; without it (config load) settings are only bounded."""
    raw = raw if isinstance(raw, dict) else {}
    bid = board_id if board_id is not None else raw.get("id")
    ratio = raw.get("ratio") if raw.get("ratio") in RATIOS else "16:9"
    cols, rows = layout.clean_grid(raw.get("cols"), raw.get("rows"), RATIOS[ratio])
    pages = clean_pages(raw.get("pages"), raw.get("items"), cat, cols, rows)
    return {"id": bid if isinstance(bid, str) and _SLUG.fullmatch(bid) else "",
            "name": layout.text(raw.get("name"), NAME_MAX) or "Board", "place": layout.text(raw.get("place"), PLACE_MAX),
            "place_ref": raw.get("place_ref") if isinstance(raw.get("place_ref"), str)
            and _PLACE_REF.fullmatch(raw["place_ref"]) else "",
            "public": raw.get("public") is True, "ratio": ratio, "cols": cols, "rows": rows, "pages": pages,
            "rotation": clean_rotation(raw.get("rotation"), [p["id"] for p in pages]),
            "screen_names": _clean_screen_names(raw.get("screen_names"))}


def all_items(board: dict) -> list[dict]:
    """Every widget on every page of the board."""
    return [it for p in board["pages"] for it in p["items"]]


def find_item(board: dict, item_id: str) -> dict | None:
    return next((it for it in all_items(board) if it["id"] == item_id), None)


def clean_list(raw: Any) -> list[dict]:
    """The stored boards (config load): bounded, with unique addresses; a board with no usable address is dropped."""
    out, seen = [], set()
    for b in (raw if isinstance(raw, list) else [])[:MAX_BOARDS]:
        c = clean_board(b)
        if c["id"] and c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


# --------------------------------------------------------------------------------------------- which page shows now
def _day_ok(days: str, weekday: int) -> bool:
    """Whether a row's `days` choice includes `weekday` (Sunday = 0)."""
    return days == "all" or (days == "weekdays" and weekday in timewin.WEEKDAYS) \
        or (days == "weekends" and weekday in timewin.WEEKEND) or DAY_NUMBERS.get(days) == weekday


OpenRef = Callable[[str, datetime], bool]


def schedule_users(ref: str) -> list[dict]:
    """The board rows that use named schedule `ref`, for the Schedules page's "used by"."""
    from . import config

    return [{"where": "Boards", "what": f"{b['name']}: schedule row {i + 1}"}
            for b in config.get()["boards"] for i, r in enumerate(b["rotation"]["schedule"]) if r.get("schedule") == ref]


def schedule_row(board: dict, now: datetime, is_open: OpenRef | None = None) -> int | None:
    """The index of the first schedule row that covers `now` (local time), else None. A row whose end is before its
    start runs past midnight; then the day it started on is the one checked. A row that names a schedule covers
    `now` while `is_open(ref, now)` says that schedule is open (a schedule that is gone never covers it)."""
    for i, r in enumerate(board["rotation"]["schedule"]):
        if r.get("schedule"):
            if is_open is not None and is_open(r["schedule"], now):
                return i
            continue
        days = r["days"]
        if timewin.window_start(now, timewin.minutes(r["from"]), timewin.minutes(r["to"]),
                                lambda d, days=days: _day_ok(days, timewin.weekday(d))):
            return i
    return None


def show_label(board: dict, show: str, short: bool = False) -> str:
    """What a schedule row or rule shows, in words: a page's number and name (only its name when `short`), or the
    rotation."""
    names = {p["id"]: p["name"] if short else f"{n} · {p['name']}" for n, p in enumerate(board["pages"], start=1)}
    if show == ROTATE:
        rot = board["rotation"]
        nums = [str(n) for n, p in enumerate(board["pages"], start=1) if p["id"] in rot["pages"]]
        joined = ", ".join(nums[:-1]) + " and " + nums[-1] if len(nums) > 1 else (nums[0] if nums else "")
        return f"Rotate {joined}, every {rot['every']} s"
    return names.get(show, names[board["pages"][0]["id"]])


def _rotate(board: dict) -> dict:
    rot = board["rotation"]
    return {"pages": [p for p in rot["pages"] if any(q["id"] == p for q in board["pages"])] or
            [board["pages"][0]["id"]], "every": rot["every"]}


def current(board: dict, now: datetime, follow: dict | None = None, last: str | None = None,
            is_open: OpenRef | None = None) -> dict:
    """Which page the board shows now: {"page": id, "rotate": {"pages", "every"} or None, "key": what decided it,
    "why": a line for the operator}. A rotation is turned by each screen from the clock, so every screen showing the
    board turns together. `follow` is what the follow source shows now ({"presentation", "group", "label"}), `last`
    the page shown before (kept when nothing matches and the board is set to keep it)."""
    rot, first = board["rotation"], board["pages"][0]["id"]
    ids = [p["id"] for p in board["pages"]]

    def out(show: str, key: str, why: str) -> dict:
        if show == ROTATE:
            r = _rotate(board)
            return {"page": r["pages"][0], "rotate": r, "key": key, "why": why}
        return {"page": show if show in ids else first, "rotate": None, "key": key, "why": why}

    mode = rot["mode"]
    if mode == "rotate":
        return out(ROTATE, "rotate", show_label(board, ROTATE))
    if mode == "schedule":
        i = schedule_row(board, now, is_open)
        if i is not None:
            row = rot["schedule"][i]
            if row.get("schedule"):
                then = show_label(board, rot["other"], True)
                return out(row["show"], f"s{i}", f"Schedule: {show_label(board, row['show'], True)} while the named "
                                                 f"schedule is open, then {then}")
            after = now.replace(hour=int(row["to"][:2]), minute=int(row["to"][3:]), second=0, microsecond=0)
            nxt = schedule_row(board, after, is_open)
            then = show_label(board, rot["schedule"][nxt]["show"] if nxt is not None else rot["other"], True)
            return out(row["show"], f"s{i}",
                       f"Schedule: {show_label(board, row['show'], True)} until {row['to']}, then {then}")
        return out(rot["other"], "other", f"Schedule: {show_label(board, rot['other'], True)} outside the times set")
    if mode == "follow":
        now_pp = follow if isinstance(follow, dict) else None
        if now_pp is None:
            return out(last if last in ids else first, "follow-off", "Following ProPresenter, which is not connected")
        for i, r in enumerate(rot["follow"]["rules"]):
            seen = str(now_pp.get(r["match"]) or "").strip().lower()
            if seen and seen == r["value"].strip().lower():
                return out(r["page"], f"f{i}", f"Following ProPresenter: {MATCHES[r['match']].lower()} "
                                               f"{r['value']} shows {show_label(board, r['page'], True)}")
        none = rot["follow"]["none"]
        if none == KEEP:
            return out(last if last in ids else first, "keep", "Following ProPresenter: nothing matches, so the page stays")
        return out(none, "none", f"Following ProPresenter: nothing matches, so {show_label(board, none, True)} shows")
    return out(first, "one", f"Always {show_label(board, first, True)}")


# --------------------------------------------------------------------------------------------- templates
def _widget(cat: dict[str, dict], wtype: str, x: int, y: int, w: int, h: int, props: dict | None = None,
            style: dict | None = None) -> dict | None:
    d = cat.get(wtype)
    if d is None:
        return None
    base = {f["key"]: f["default"] for f in d["fields"] if f["type"] != "note" and f["default"] is not None}
    return {"type": wtype, "x": x, "y": y, "w": w, "h": h, "props": {**base, **(props or {})},
            "style": {**d["style"], **(style or {})}}


def templates(cat: dict[str, dict]) -> list[dict]:
    """Starting layouts for a new board, built from the widgets there are now. Each is {"id", "name", "about",
    "ratio", "items"}; every widget can be moved, changed or removed afterwards."""
    buttons = [(d, p) for d in cat.values() if d["module"] and d["kind"] == "button" for p in (d["presets"] or [{}])][:4]
    panel = [_widget(cat, d["type"], 2 * n, 0, 2, 2, p.get("props"), p.get("style")) for n, (d, p) in enumerate(buttons)]
    if not panel:
        panel = [_widget(cat, "text", 0, 0, 8, 2, {"text": "Add buttons from the widget list"})]
    raw = [
        ("lobby", "Lobby welcome", "Welcome, clock, countdown, captions, livestream", "16:9",
         [_widget(cat, "text", 0, 0, 8, 2, {"text": "Welcome"}), _widget(cat, "clock", 8, 0, 4, 2),
          _widget(cat, "stream", 8, 2, 4, 2), _widget(cat, "captions", 0, 2, 8, 6),
          _widget(cat, "countdown", 8, 4, 4, 4)]),
        ("stage", "Stage confidence", "Clock, time left, captions", "16:9",
         [_widget(cat, "clock", 0, 0, 6, 2), _widget(cat, "countdown", 6, 0, 6, 2, style={"label": "Time left"}),
          _widget(cat, "captions", 0, 2, 12, 6, {"lines": "4"})]),
        ("captions", "Captions only", "Full-screen, one language", "16:9",
         [_widget(cat, "captions", 0, 0, 12, 8, {"lines": "4"})]),
        ("buttons", "Button panel", "Cues and links, tablet", "4:3", panel + [_widget(cat, "clock", 0, 4, 8, 2)]),
        ("sound", "Sound check", "Level, spectrum, clock", "16:9",
         [_widget(cat, "spl", 0, 0, 6, 4), _widget(cat, "clock", 6, 0, 6, 4), _widget(cat, "rta", 0, 4, 12, 4)]),
        ("blank", "Blank", "Empty grid", "16:9", []),
    ]
    return [{"id": t, "name": n, "about": a, "ratio": r, "items": [i for i in its if i]} for t, n, a, r, its in raw]


# --------------------------------------------------------------------------------------------- changes
def slug_for(name: str, taken: set[str]) -> str:
    """A new board's address from its name: lowercase words joined by hyphens, numbered when already taken."""
    return text.slug(name, taken, limit=32, fallback="board")


def find(boards: list[dict], bid: str) -> dict | None:
    return next((b for b in boards if b["id"] == bid), None)


def create(boards: list[dict], payload: Any, cat: dict[str, dict]) -> dict:
    """A new board from {name, ratio, place, public, template?, pages? or items?}, with a fresh address. Raises
    BoardError."""
    if len(boards) >= MAX_BOARDS:
        raise BoardError(f"Relay keeps at most {MAX_BOARDS} boards.")
    payload = payload if isinstance(payload, dict) else {}
    name = layout.text(payload.get("name"), NAME_MAX)
    if not name:
        raise BoardError("Give the board a name.")
    tpl = next((t for t in templates(cat) if t["id"] == payload.get("template")), None)
    if tpl is not None:
        payload = {**payload, "ratio": payload.get("ratio") if payload.get("ratio") in RATIOS else tpl["ratio"],
                   "pages": [{"id": "p1", "name": "Page 1", "items": tpl["items"]}]}
    ratio = payload.get("ratio") if payload.get("ratio") in RATIOS else "16:9"
    if "cols" not in payload or tpl is not None:
        payload = {**payload, "cols": RATIOS[ratio][0], "rows": RATIOS[ratio][1]}
    payload = {k: v for k, v in payload.items() if k not in ("screen_names", "place_ref")}
    return clean_board({**payload, "name": name, "ratio": ratio}, cat,
                       board_id=slug_for(name, {b["id"] for b in boards}))


UPDATABLE = ("name", "place", "public", "ratio", "cols", "rows", "pages", "rotation")


def update(board: dict, payload: Any, cat: dict[str, dict]) -> dict:
    """The board with what `payload` changes (name, place, public, ratio, cols, rows, pages, rotation; `items`
    alone replaces page 1's); its address stays."""
    payload = payload if isinstance(payload, dict) else {}
    merged = {**board, **{k: payload[k] for k in UPDATABLE if k in payload}}
    if "items" in payload and "pages" not in payload:
        merged["pages"] = [{**board["pages"][0], "items": payload["items"]}] + board["pages"][1:]
    if "name" in payload and not layout.text(payload.get("name"), NAME_MAX):
        raise BoardError("Give the board a name.")
    return clean_board(merged, cat, board_id=board["id"])


def name_screen(board: dict, screen: str, name: Any) -> dict:
    """The board with a screen's name set (or cleared with "")."""
    if not SCREEN_ID.fullmatch(screen or ""):
        raise BoardError("That is not a screen.")
    names = {k: v for k, v in board["screen_names"].items() if k != screen}
    if layout.text(name, SCREEN_NAME_MAX):
        if len(names) >= MAX_SCREENS:
            raise BoardError("This board has too many named screens.")
        names[screen] = layout.text(name, SCREEN_NAME_MAX)
    return {**board, "screen_names": names}


def export(board: dict) -> dict:
    return {"relay_board": 2, "name": board["name"], "place": board["place"], "public": board["public"],
            "ratio": board["ratio"], "cols": board["cols"], "rows": board["rows"], "pages": board["pages"],
            "rotation": board["rotation"]}


def import_board(boards: list[dict], data: Any, cat: dict[str, dict]) -> dict:
    """A board from an exported file (this version's, or one saved before pages), under a new address. Checked like a
    save."""
    if not isinstance(data, dict) or data.get("relay_board") not in (1, 2):
        raise BoardError("That is not a Relay board file.")
    return create(boards, {k: v for k, v in data.items() if k != "template"}, cat)
