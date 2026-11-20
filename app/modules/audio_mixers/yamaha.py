"""Yamaha CL, QL, TF and DM over RCP, Yamaha's remote control protocol on TCP 49280 (#150).

One text command a line, answered `OK ...` or `ERROR ...`; a change made anywhere arrives as `NOTIFY set ...`. A
parameter is an address and two indexes from 0 (`MIXER:Current/InCh/Fader/Level 4 0` is input 5's fader). Levels are
hundredths of a dB with -32768 for -inf; `Fader/On` is 1 when the channel is *not* muted. Scenes are recalled with
`ssrecall_ex <library> <n>` and reported with `sscurrent_ex`; `ssinfo_ex` answers a scene's title. CL and QL call their
scene library `scene_a`; TF and DM are written to `MIXER:Lib/Scene`, which no one has tried on a real desk yet.
"""
from __future__ import annotations

import re

from .models import OFF_DB, Model

NODES = {"in": "MIXER:Current/InCh", "mix": "MIXER:Current/Mix", "main": "MIXER:Current/St", "dca": "MIXER:Current/DCA"}
COLOURS = {"purple": "Purple", "pink": "Pink", "red": "Red", "orange": "Orange", "yellow": "Yellow", "blue": "Blue",
           "cyan": "SkyBlue", "green": "Green", "off": "Off", "white": "Off"}
_BACK_COLOURS = {v.lower(): k for k, v in COLOURS.items() if k != "white"}
_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|(\S+)')
_PARAM = re.compile(r"(MIXER:Current/(?:InCh|Mix|St|DCA))/(Fader/Level|Fader/On|Label/Name|Label/Color)")
SEND_LEVEL = "MIXER:Current/InCh/ToMix/Level"
SEND_ON = "MIXER:Current/InCh/ToMix/On"
NEG_INF = -32768


def library(model: Model) -> str:
    return "scene_a" if model.id[:2] in ("cl", "ql") else "MIXER:Lib/Scene"


def tokens(line: str) -> list[str]:
    return [m.group(1) if m.group(1) is not None else m.group(2) for m in _TOKEN.finditer(line)]


def quote(text: str) -> str:
    return '"' + text.replace("\\", "").replace('"', "") + '"'


def level(db: float) -> int:
    return NEG_INF if db <= OFF_DB else max(-13800, min(1000, round(db * 100)))


def db(value: int) -> float:
    return OFF_DB if value <= -13800 else value / 100


def index(kind: str, n: int) -> tuple[str, int, int]:
    return NODES[kind], (0 if kind == "main" else n - 1), 0


def set_cmd(kind: str, n: int, what: str, value: int | str) -> str:
    node, x, y = index(kind, n)
    v = quote(value) if isinstance(value, str) else str(value)
    return f"set {node}/{what} {x} {y} {v}\n"


def get_cmd(kind: str, n: int, what: str) -> str:
    node, x, y = index(kind, n)
    return f"get {node}/{what} {x} {y}\n"


def strip_of(node: str, x: int) -> str | None:
    kind = next((k for k, v in NODES.items() if v == node), None)
    if kind is None:
        return None
    return "main1" if kind == "main" else f"{kind}{x + 1}"


def colour_of(text: str) -> str:
    return _BACK_COLOURS.get(text.strip().lower().replace(" ", ""), "off")


def parse(line: str) -> tuple | None:
    """What one line from the desk says:
    ("strip", key, "level" | "on" | "name" | "colour", value), ("send", input, mix, "level" | "on", value),
    ("scene", number), ("scene_name", number, title), ("product", name), or None."""
    t = tokens(line.strip())
    if len(t) < 3 or t[0] not in ("OK", "NOTIFY"):
        return None
    cmd = t[1]
    if cmd in ("get", "set") and len(t) >= 6:
        addr = t[2]
        try:
            x, y = int(t[3]), int(t[4])
        except ValueError:
            return None
        raw = t[5]
        if addr in (SEND_LEVEL, SEND_ON):
            try:
                v = int(raw)
            except ValueError:
                return None
            return ("send", x + 1, y + 1, "level", db(v)) if addr == SEND_LEVEL else ("send", x + 1, y + 1, "on", v == 1)
        m = _PARAM.fullmatch(addr)
        if not m:
            return None
        key = strip_of(m.group(1), x)
        what = m.group(2)
        if key is None:
            return None
        if what in ("Fader/Level", "Fader/On"):
            try:
                v = int(raw)
            except ValueError:
                return None
            return ("strip", key, "level", db(v)) if what == "Fader/Level" else ("strip", key, "on", v == 1)
        return ("strip", key, "name", raw) if what == "Label/Name" else ("strip", key, "colour", colour_of(raw))
    if cmd == "sscurrent_ex" and len(t) >= 4:
        try:
            return ("scene", int(t[3]))
        except ValueError:
            return None
    if cmd == "ssinfo_ex" and len(t) >= 5:
        try:
            return ("scene_name", int(t[3]), t[4])
        except ValueError:
            return None
    if cmd == "devinfo" and len(t) >= 4 and t[2] == "productname":
        return ("product", t[3])
    return None
