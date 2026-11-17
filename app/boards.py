"""Boards (#156): screens an operator builds from widgets, each at its own stable address, /board/<slug>.

A board is a grid (app/layout.py) of widgets, laid out with the shared editor (#421) over the live board. It is kept
in config.json under "boards", so a backup carries it:

    {"id": "lobby", "name": "Lobby", "public": false, "ratio": "16:9", "cols": 12, "rows": 8,
     "items": [{"id": "a1", "type": "captions", "x": 0, "y": 0, "w": 8, "h": 3,
                "style": {...app/layout.py...}, "props": {"lang": "SOURCE", "lines": 3}}]}

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
from typing import Any, Iterable

from . import layout

MAX_BOARDS = 50
MAX_ITEMS = 100
NAME_MAX = 60
TEXT_MAX = 200
_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")
_ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
_TYPE = re.compile(r"[a-z][a-z0-9_]{0,31}(?:\.[a-z][a-z0-9_]{0,31})?")
_PROP_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_TIME = re.compile(r"([01]?\d|2[0-3]):[0-5]\d")
RATIOS = {"16:9": (12, 8), "4:3": (8, 6), "9:16": (6, 10)}
KINDS = ("button", "captions", "clock", "countdown", "text", "status", "lines", "list", "spl", "rta")
FIELD_TYPES = ("text", "area", "select", "check", "time", "number", "note", "ro")
SOURCE = "SOURCE"            # the captions widget's value for the original speech


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
            value = value if isinstance(value, str) and _TIME.fullmatch(value) else \
                (f["default"] if isinstance(f["default"], str) and _TIME.fullmatch(f["default"]) else "")
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


def clean_board(raw: Any, cat: dict[str, dict] | None = None, *, board_id: str | None = None) -> dict:
    """One board, bounded. With `cat` (the catalogue) each widget's settings are checked against its definition and a
    widget of an unknown core type is dropped; without it (config load) settings are only bounded."""
    raw = raw if isinstance(raw, dict) else {}
    bid = board_id if board_id is not None else raw.get("id")
    ratio = raw.get("ratio") if raw.get("ratio") in RATIOS else "16:9"
    cols, rows = layout.clean_grid(raw.get("cols"), raw.get("rows"), RATIOS[ratio])
    items, seen = [], set()
    for it in (raw.get("items") if isinstance(raw.get("items"), list) else [])[:MAX_ITEMS]:
        c = clean_item(it, cat)
        if c is None:
            continue
        if c["id"] in seen:
            c["id"] = uuid.uuid4().hex[:10]
        seen.add(c["id"])
        items.append(c)
    return {"id": bid if isinstance(bid, str) and _SLUG.fullmatch(bid) else "",
            "name": layout.text(raw.get("name"), NAME_MAX) or "Board", "public": raw.get("public") is True,
            "ratio": ratio, "cols": cols, "rows": rows, "items": layout.place(items, cols, rows)}


def clean_list(raw: Any) -> list[dict]:
    """The stored boards (config load): bounded, with unique addresses; a board with no usable address is dropped."""
    out, seen = [], set()
    for b in (raw if isinstance(raw, list) else [])[:MAX_BOARDS]:
        c = clean_board(b)
        if c["id"] and c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
    return out


def slug_for(name: str, taken: set[str]) -> str:
    """A new board's address from its name: lowercase words joined by hyphens, numbered when already taken."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:32].strip("-") or "board"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def find(boards: list[dict], bid: str) -> dict | None:
    return next((b for b in boards if b["id"] == bid), None)


def create(boards: list[dict], payload: Any, cat: dict[str, dict]) -> dict:
    """A new board from {name, ratio, public, items?}, with a fresh address. Raises BoardError."""
    if len(boards) >= MAX_BOARDS:
        raise BoardError(f"Relay keeps at most {MAX_BOARDS} boards.")
    payload = payload if isinstance(payload, dict) else {}
    name = layout.text(payload.get("name"), NAME_MAX)
    if not name:
        raise BoardError("Give the board a name.")
    ratio = payload.get("ratio") if payload.get("ratio") in RATIOS else "16:9"
    if "cols" not in payload:
        payload = {**payload, "cols": RATIOS[ratio][0], "rows": RATIOS[ratio][1]}
    return clean_board({**payload, "name": name, "ratio": ratio}, cat,
                       board_id=slug_for(name, {b["id"] for b in boards}))


def update(board: dict, payload: Any, cat: dict[str, dict]) -> dict:
    """The board with what `payload` changes (name, public, ratio, cols, rows, items); its address stays."""
    payload = payload if isinstance(payload, dict) else {}
    merged = {**board, **{k: payload[k] for k in ("name", "public", "ratio", "cols", "rows", "items") if k in payload}}
    if "name" in payload and not layout.text(payload.get("name"), NAME_MAX):
        raise BoardError("Give the board a name.")
    return clean_board(merged, cat, board_id=board["id"])


def export(board: dict) -> dict:
    return {"relay_board": 1, "name": board["name"], "public": board["public"], "ratio": board["ratio"],
            "cols": board["cols"], "rows": board["rows"], "items": board["items"]}


def import_board(boards: list[dict], data: Any, cat: dict[str, dict]) -> dict:
    """A board from an exported file, under a new address. Checked like a save."""
    if not isinstance(data, dict) or data.get("relay_board") != 1:
        raise BoardError("That is not a Relay board file.")
    return create(boards, data, cat)
