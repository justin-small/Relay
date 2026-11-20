"""The desks Audio Mixers knows (#150, #486), and the one way every desk's strips are named.

Every desk is mirrored as strips keyed the same way whatever its brand: `in<n>` (input channels), `mix<n>` (aux mixes or
buses, the destinations a send goes to), `main<n>` (the main mix) and `dca<n>`, each numbered from 1 as the desk's own
surface numbers them. A desk has a model, and the model says which protocol family speaks to it and how many of each strip
it has. Fader levels are kept in dB, with `OFF_DB` for a fader all the way down; the touch faders on boards draw them on
one shared fader law (`db_to_pos`, `pos_to_db`) and each driver turns dB into its desk's own numbers.

Built from the published protocols and the Bitfocus Companion modules (MIT) for each desk, not on real hardware: every
model is marked untested until someone runs it against the desk (`TESTED`).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

KINDS = ("in", "mix", "main", "dca")
KIND_NAMES = {"in": "Input", "mix": "Mix", "main": "Main", "dca": "DCA"}
OFF_DB = -90.0                 # a fader at the bottom (-inf on the desk)
MAX_DB = 10.0
COLOURS = ("off", "red", "green", "yellow", "blue", "purple", "cyan", "white", "orange", "pink")
TESTED: frozenset[str] = frozenset()      # models proved on a real desk; none yet
_STRIP = re.compile(r"(in|mix|main|dca)([1-9]\d{0,2})")


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    family: str                # "ah" (Avantis, dLive, GLD, Qu), "sq", "x32", "wing", "yamaha"
    port: int
    counts: tuple[int, int, int, int]       # in, mix, main, dca
    scenes: int
    name_len: int              # 0: the desk takes no names over its protocol
    colours: bool = True
    meters: bool = False
    note: str = ""

    def count(self, kind: str) -> int:
        return self.counts[KINDS.index(kind)]


_AH = "Allen & Heath"
_YA = "Yamaha"
MODELS: dict[str, Model] = {m.id: m for m in (
    Model("avantis", f"{_AH} Avantis", "ah", 51325, (64, 24, 1, 16), 500, 8,
          note="MIDI over TCP. Turn on the console's MIDI and set its base channel (Utility > Control > MIDI)."),
    Model("dlive", f"{_AH} dLive", "ah", 51325, (128, 24, 1, 24), 500, 8,
          note="MIDI over TCP to the MixRack (51325). Set the base MIDI channel to match the MixRack."),
    Model("gld", f"{_AH} GLD", "ah", 51325, (48, 20, 0, 16), 500, 8,
          note="MIDI over TCP. Set the MIDI channel to match Setup > Control > MIDI."),
    Model("qu16", f"{_AH} Qu-16", "ah", 51325, (16, 7, 1, 4), 100, 6,
          note="MIDI over TCP. Set the MIDI channel to match Setup > Control > MIDI."),
    Model("qu24", f"{_AH} Qu-24", "ah", 51325, (24, 7, 1, 4), 100, 6),
    Model("qu32", f"{_AH} Qu-32", "ah", 51325, (32, 7, 1, 4), 100, 6),
    Model("sq5", f"{_AH} SQ-5", "sq", 51325, (48, 12, 1, 8), 300, 0, colours=False,
          note="MIDI over TCP (NRPN). The SQ sends no channel names or colours over MIDI."),
    Model("sq6", f"{_AH} SQ-6", "sq", 51325, (48, 12, 1, 8), 300, 0, colours=False),
    Model("sq7", f"{_AH} SQ-7", "sq", 51325, (48, 12, 1, 8), 300, 0, colours=False),
    Model("x32", "Behringer X32", "x32", 10023, (32, 16, 1, 8), 100, 12, meters=True,
          note="OSC over UDP 10023. Relay asks for updates every few seconds (/xremote)."),
    Model("m32", "Midas M32", "x32", 10023, (32, 16, 1, 8), 100, 12, meters=True),
    Model("wing", "Behringer WING", "wing", 2223, (40, 16, 4, 16), 999, 16,
          note="OSC over UDP 2223. Relay renews its subscription every few seconds."),
    Model("cl1", f"{_YA} CL1", "yamaha", 49280, (48, 24, 1, 16), 300, 8,
          note="RCP over TCP 49280. Allow remote control from this computer's address on the console."),
    Model("cl3", f"{_YA} CL3", "yamaha", 49280, (64, 24, 1, 16), 300, 8),
    Model("cl5", f"{_YA} CL5", "yamaha", 49280, (72, 24, 1, 16), 300, 8),
    Model("ql1", f"{_YA} QL1", "yamaha", 49280, (32, 16, 1, 16), 300, 8),
    Model("ql5", f"{_YA} QL5", "yamaha", 49280, (64, 16, 1, 16), 300, 8),
    Model("tf1", f"{_YA} TF1", "yamaha", 49280, (40, 20, 1, 8), 100, 8),
    Model("tf3", f"{_YA} TF3", "yamaha", 49280, (48, 20, 1, 8), 100, 8),
    Model("tf5", f"{_YA} TF5", "yamaha", 49280, (48, 20, 1, 8), 100, 8),
    Model("dm3", f"{_YA} DM3", "yamaha", 49280, (16, 6, 1, 8), 200, 32),
    Model("dm7", f"{_YA} DM7", "yamaha", 49280, (120, 48, 1, 24), 500, 32),
)}
FAMILIES = {"ah": f"{_AH} (MIDI)", "sq": f"{_AH} SQ (MIDI)", "x32": "Behringer X32 / Midas M32 (OSC)",
            "wing": "Behringer WING (OSC)", "yamaha": "Yamaha (RCP)"}


def strip_key(kind: str, n: int) -> str:
    return f"{kind}{n}"


def parse_strip(key: object) -> tuple[str, int] | None:
    """("in", 3) for "in3"; None for anything that is not a strip key."""
    m = _STRIP.fullmatch(key) if isinstance(key, str) else None
    return (m.group(1), int(m.group(2))) if m else None


def strips(model: Model) -> list[str]:
    return [strip_key(k, n) for k in KINDS for n in range(1, model.count(k) + 1)]


def has_strip(model: Model, key: object) -> bool:
    got = parse_strip(key)
    return bool(got) and 1 <= got[1] <= model.count(got[0])


def default_name(key: str) -> str:
    kind, n = parse_strip(key) or ("in", 0)
    return f"{KIND_NAMES[kind]} {n}" if kind != "main" or n > 1 else "Main"


def clamp_db(value: object) -> float | None:
    """A level in dB clamped to the faders' range, OFF_DB for -inf; None for anything that is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if math.isnan(v):
        return None
    return OFF_DB if v <= OFF_DB else min(MAX_DB, round(v, 2))


def db_text(db: float | None) -> str:
    if db is None:
        return "?"
    return "-∞" if db <= OFF_DB else f"{db:+.1f}".replace("+0.0", "0.0")


# One fader law for every touch fader (the X32's four straight pieces): position 0..1 to dB and back.
def pos_to_db(pos: float) -> float:
    p = max(0.0, min(1.0, float(pos)))
    if p <= 0.0:
        return OFF_DB
    if p >= 0.5:
        db = p * 40 - 30
    elif p >= 0.25:
        db = p * 80 - 50
    elif p >= 0.0625:
        db = p * 160 - 70
    else:
        db = p * 480 - 90
    return OFF_DB if db <= OFF_DB else round(db, 2)


def db_to_pos(db: float | None) -> float:
    if db is None or db <= OFF_DB:
        return 0.0
    d = min(MAX_DB, db)
    if d >= -10:
        p = (d + 30) / 40
    elif d >= -30:
        p = (d + 50) / 80
    elif d >= -60:
        p = (d + 70) / 160
    else:
        p = (d + 90) / 480
    return round(max(0.0, min(1.0, p)), 4)


def clean_name(value: object, limit: int) -> str:
    """A strip name a desk can take: printable ASCII, trimmed to the desk's length."""
    if not isinstance(value, str):
        return ""
    text = "".join(c for c in value if 32 <= ord(c) < 127).strip()
    return text[:limit].strip() if limit else ""
