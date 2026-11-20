"""What one desk is doing, as Relay last heard it: the mirror every driver writes and every view reads (#150).

A value Relay has not heard is None ("unknown"), never a guess: an Allen & Heath desk says a mute only when it changes,
so until then the mute is unknown, not "on". Each change is reported to `on_change(what, key)` with `what` one of
"name", "colour", "mute", "level", "scene", "send", "meter", "link", so the service can push it to pages, tell core
about new names and let the watchdog look.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .models import Model, clamp_db, default_name, strips


@dataclass
class Strip:
    key: str
    name: str | None = None
    colour: str | None = None
    muted: bool | None = None
    db: float | None = None
    meter: float | None = None              # dBFS, X32/M32 only
    sends: dict[int, tuple[bool | None, float | None]] = field(default_factory=dict)   # mix n: (on, dB)

    def shown(self) -> str:
        return self.name or default_name(self.key)

    def public(self) -> dict:
        return {"key": self.key, "name": self.name, "shown": self.shown(), "colour": self.colour, "muted": self.muted,
                "db": self.db, "meter": self.meter}


@dataclass
class Mirror:
    model: Model
    on_change: Callable[[str, str], None] | None = None
    connected: bool = False
    heard_at: float = 0.0
    scene: int | None = None
    scene_names: dict[int, str] = field(default_factory=dict)
    strips: dict[str, Strip] = field(default_factory=dict)
    product: str = ""

    def __post_init__(self) -> None:
        if not self.strips:
            self.strips = {k: Strip(k) for k in strips(self.model)}

    def _tell(self, what: str, key: str = "") -> None:
        if self.on_change:
            self.on_change(what, key)

    def heard(self) -> None:
        self.heard_at = time.time()

    def link(self, up: bool) -> None:
        if up != self.connected:
            self.connected = up
            if not up:     # what the desk said before may have changed while Relay could not hear it
                for s in self.strips.values():
                    s.meter = None
            self._tell("link")

    def set(self, key: str, *, name: str | None = None, colour: str | None = None, muted: bool | None = None,
            db: float | None = None) -> None:
        s = self.strips.get(key)
        if s is None:
            return
        self.heard()
        if name is not None and name != s.name:
            s.name = name
            self._tell("name", key)
        if colour is not None and colour != s.colour:
            s.colour = colour
            self._tell("colour", key)
        if muted is not None and muted != s.muted:
            s.muted = muted
            self._tell("mute", key)
        if db is not None:
            v = clamp_db(db)
            if v is not None and v != s.db:
                s.db = v
                self._tell("level", key)

    def set_send(self, key: str, mix: int, *, on: bool | None = None, db: float | None = None) -> None:
        s = self.strips.get(key)
        if s is None or not 1 <= mix <= self.model.count("mix"):
            return
        self.heard()
        old_on, old_db = s.sends.get(mix, (None, None))
        new = (old_on if on is None else on, old_db if db is None else clamp_db(db))
        if new != (old_on, old_db):
            s.sends[mix] = new
            self._tell("send", key)

    def set_meter(self, key: str, dbfs: float | None) -> None:
        s = self.strips.get(key)
        if s is not None:
            s.meter = None if dbfs is None else round(max(-90.0, min(20.0, dbfs)), 1)

    def set_scene(self, number: int | None, name: str | None = None) -> None:
        self.heard()
        if name:
            self.scene_names[number or 0] = name
        if number is not None and number != self.scene:
            self.scene = number
            self._tell("scene")

    def name_scene(self, number: int, name: str) -> None:
        if name and self.scene_names.get(number) != name[:64]:
            self.scene_names[number] = name[:64]
            self._tell("scene")

    def set_scenes(self, names: list[str]) -> None:
        new = {i + 1: s[:64] for i, s in enumerate(names) if s}
        if new != self.scene_names:
            self.scene_names = new
            self._tell("scene")

    def meters_read(self) -> None:
        self._tell("meter")

    def scene_name(self) -> str:
        return self.scene_names.get(self.scene or 0, "") if self.scene is not None else ""

    def public(self) -> dict:
        return {"connected": self.connected, "heard_at": self.heard_at, "product": self.product, "scene": self.scene,
                "scene_name": self.scene_name(), "strips": [s.public() for s in self.strips.values()]}
