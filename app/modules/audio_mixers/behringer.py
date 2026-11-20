"""Behringer X32 / Midas M32 and Behringer WING over OSC (#150), from Behringer's published OSC documents and the
Companion modules for X32 and WING (MIT).

**X32 / M32** (UDP 10023). `/xremote` every few seconds makes the desk send every change for ten seconds. Faders are
0..1 on the X32's four-piece law; `/mix/on` is 1 when the channel is *not* muted; colours are 0-15 (8-15 the same
colours inverted). Scenes are numbered from 0 (`/-action/goscene`, `/-show/prepos/current`). `/meters ,si /meters/0`
streams a blob of 70 linear meter values every 50 ms for ten seconds: inputs 0-31, aux 32-39, FX returns 40-47, buses
48-63, matrices 64-69 (1.0 is 0 dBFS).

**WING** (UDP 2223). `/*S` every few seconds subscribes to changes. A parameter's answer is `,sff`: its text, its 0..1
value and its real value (dB for a fader), or just one of them; a value Relay sends as a float is the real value.
`/mute` is 1 when muted. Scenes: `/$ctl/lib/$actionidx <n>` then `/$ctl/lib/$action GO`; `/$ctl/lib/$actidx` is the
current one and `/$ctl/lib` answers with `$scenes list [a, b, ...]`.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Any

from .models import OFF_DB, pos_to_db, db_to_pos

X32_COLOURS = ("off", "red", "green", "yellow", "blue", "purple", "cyan", "white")
WING_COLOURS = ("off", "blue", "cyan", "green", "green", "yellow", "orange", "red", "pink", "purple", "purple", "white",
                "white")
X32_METERS = "/meters/0"


# -- X32 / M32 --------------------------------------------------------------------------------------------------------
def x32_base(kind: str, n: int) -> str | None:
    if kind == "in":
        return f"/ch/{n:02d}"
    if kind == "mix":
        return f"/bus/{n:02d}"
    if kind == "main":
        return "/main/st" if n == 1 else None
    if kind == "dca":
        return f"/dca/{n}"
    return None


def x32_paths(kind: str, n: int) -> dict[str, str]:
    """The fader, on, name and colour addresses of one strip."""
    b = x32_base(kind, n)
    if b is None:
        return {}
    if kind == "dca":
        return {"fader": f"{b}/fader", "on": f"{b}/on", "name": f"{b}/config/name", "colour": f"{b}/config/color"}
    return {"fader": f"{b}/mix/fader", "on": f"{b}/mix/on", "name": f"{b}/config/name", "colour": f"{b}/config/color"}


_X32_RE = re.compile(r"/(ch|bus|main|dca)/(\d{1,2}|st)(/mix)?/(fader|on|config/name|config/color)")
_X32_SEND = re.compile(r"/ch/(\d{2})/mix/(\d{2})/(level|on)")
_X32_SCENE_NAME = re.compile(r"/-show/showfile/scene/(\d{3})/name")


def x32_strip(address: str) -> tuple[str, str] | None:
    """(strip key, "fader" | "on" | "name" | "colour") for an address Relay follows."""
    m = _X32_RE.fullmatch(address)
    if not m:
        return None
    node, num, mix, what = m.groups()
    kind = {"ch": "in", "bus": "mix", "main": "main", "dca": "dca"}[node]
    if what in ("fader", "on") and (kind == "dca") == bool(mix):     # /ch/01/mix/fader but /dca/1/fader
        return None
    if what.startswith("config") and mix:
        return None
    n = 1 if num == "st" else int(num)
    if (kind == "main") != (num == "st"):
        return None
    return f"{kind}{n}", {"fader": "fader", "on": "on", "config/name": "name", "config/color": "colour"}[what]


def x32_send(address: str) -> tuple[int, int, str] | None:
    m = _X32_SEND.fullmatch(address)
    return (int(m.group(1)), int(m.group(2)), m.group(3)) if m else None


def x32_scene_name(address: str) -> int | None:
    m = _X32_SCENE_NAME.fullmatch(address)
    return int(m.group(1)) if m else None


def x32_colour(value: int) -> str:
    return X32_COLOURS[int(value) % 8] if isinstance(value, int) else "off"


def x32_colour_value(colour: str) -> int:
    return X32_COLOURS.index(colour) if colour in X32_COLOURS else 0


def x32_level(db: float) -> float:
    return float(db_to_pos(db))


def x32_db(value: float) -> float:
    return pos_to_db(value)


def x32_meters(blob: bytes) -> list[float | None] | None:
    """dBFS for each value of a /meters/0 blob (None for silence): int32 count, then little-endian floats."""
    if len(blob) < 4:
        return None
    count = struct.unpack("<i", blob[:4])[0]
    n = min(count, (len(blob) - 4) // 4)
    if n <= 0:
        return None
    vals = struct.unpack(f"<{n}f", blob[4:4 + 4 * n])
    return [20 * math.log10(v) if v > 1e-5 else None for v in vals]


def x32_meter_index(kind: str, n: int) -> int | None:
    if kind == "in" and n <= 32:
        return n - 1
    if kind == "mix" and n <= 16:
        return 48 + n - 1
    return None


# -- WING ---------------------------------------------------------------------------------------------------------
def wing_base(kind: str, n: int) -> str | None:
    return {"in": f"/ch/{n}", "mix": f"/bus/{n}", "main": f"/main/{n}", "dca": f"/dca/{n}"}.get(kind)


def wing_paths(kind: str, n: int) -> dict[str, str]:
    b = wing_base(kind, n)
    return {"fader": f"{b}/fdr", "mute": f"{b}/mute", "name": f"{b}/name", "colour": f"{b}/col"} if b else {}


_WING_RE = re.compile(r"/(ch|bus|main|dca)/(\d{1,2})/(fdr|mute|name|col)")
_WING_SEND = re.compile(r"/ch/(\d{1,2})/send/(\d{1,2})/(lvl|on)")
_SCENES = re.compile(r"\$scenes\s+list\s+\[([^\]]*)\]")


def wing_strip(address: str) -> tuple[str, str] | None:
    m = _WING_RE.fullmatch(address)
    if not m:
        return None
    node, num, what = m.groups()
    kind = {"ch": "in", "bus": "mix", "main": "main", "dca": "dca"}[node]
    return f"{kind}{int(num)}", {"fdr": "fader", "mute": "mute", "name": "name", "col": "colour"}[what]


def wing_send(address: str) -> tuple[int, int, str] | None:
    m = _WING_SEND.fullmatch(address)
    return (int(m.group(1)), int(m.group(2)), m.group(3)) if m else None


def wing_number(args: list[Any]) -> float | None:
    """The real value of a WING answer: the last number of `,sff`, a lone number, or a number written as text."""
    nums = [a for a in args if isinstance(a, (int, float)) and not isinstance(a, bool)]
    if len(args) >= 3 and len(nums) >= 2:
        return float(nums[-1])
    if nums:
        return float(nums[0])
    for a in args:
        if isinstance(a, str):
            t = a.strip().lower()
            if t in ("-inf", "-oo", "off"):
                return OFF_DB
            try:
                return float(t)
            except ValueError:
                return None
    return None


def wing_text(args: list[Any]) -> str | None:
    for a in args:
        if isinstance(a, str):
            return a
    return None


def wing_colour(value: float | None) -> str:
    i = int(value or 0)
    return WING_COLOURS[i] if 0 <= i < len(WING_COLOURS) else "off"


def wing_colour_value(colour: str) -> int:
    return WING_COLOURS.index(colour) if colour in WING_COLOURS else 0


def wing_scenes(args: list[Any]) -> list[str] | None:
    text = " ".join(a for a in args if isinstance(a, str))
    m = _SCENES.search(text)
    if not m:
        return None
    return [s.strip() for s in m.group(1).split(",") if s.strip()]
