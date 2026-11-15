"""The shared tally state (#242): which cameras are on program and which on preview.

    GET /stream?stream=tally

Modules that read a video switcher (ATEM, Tally Arbiter, ...) publish into this one place with
`tally.tally.publish("<module id>", cameras)` and call `tally.tally.clear("<module id>")` in `detach()`.
The tally screens (`/tally`) and the panel view read only this state, whichever module filled it, so
core never names a switcher. Core never imports a module.

A camera is `{"n": 1, "name": "Camera 1", "program": True, "preview": False}`. `n` is the number a phone
asks for (`/tally?camera=1`). Two sources may publish at once (a main switcher and a streaming one): a camera
is on program or preview when any source says so, and the first source to name a number names it.

The state goes to the viewer port with no login, like the status feed, so it carries camera names and
on-air flags only: no addresses, no errors. Each message is the whole state, sent when it changes and again
every TICK_S so a screen's silence timer never fires.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

from .hub import sse_frame

STREAM = "tally"
VIEWS = ("camera", "operator", "board")
POLL_S = 0.25
TICK_S = 5.0
MAX_CAMERAS = 64
NAME_MAX = 24

_SOURCE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _name(value: object, n: int) -> str:
    text = " ".join(value.split())[:NAME_MAX] if isinstance(value, str) else ""
    return text or f"Camera {n}"


def clean_camera(raw: object) -> dict | None:
    """One camera from a module, or None when it is not usable."""
    if not isinstance(raw, dict):
        return None
    n = raw.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 999:
        return None
    return {"n": n, "name": _name(raw.get("name"), n), "program": raw.get("program") is True,
            "preview": raw.get("preview") is True}


class Tally:
    def __init__(self) -> None:
        self._sources: dict[str, list[dict]] = {}
        self._version = 0

    def publish(self, source: str, cameras: list) -> None:
        """Replace everything `source` knows. Ignored for an id that is not a module id."""
        if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
            return
        cleaned: dict[int, dict] = {}
        for raw in cameras or []:
            cam = clean_camera(raw)
            if cam is not None and cam["n"] not in cleaned and len(cleaned) < MAX_CAMERAS:
                cleaned[cam["n"]] = cam
        new = sorted(cleaned.values(), key=lambda c: c["n"])
        if self._sources.get(source) != new:
            self._sources[source] = new
            self._version += 1

    def clear(self, source: str) -> None:
        if self._sources.pop(source, None) is not None:
            self._version += 1

    def reset(self) -> None:
        self._sources.clear()
        self._version += 1

    def cameras(self) -> list[dict]:
        merged: dict[int, dict] = {}
        for source in sorted(self._sources):
            for cam in self._sources[source]:
                have = merged.get(cam["n"])
                if have is None:
                    merged[cam["n"]] = dict(cam)
                else:
                    have["program"] = have["program"] or cam["program"]
                    have["preview"] = have["preview"] or cam["preview"]
        return [merged[n] for n in sorted(merged)][:MAX_CAMERAS]

    def payload(self) -> dict:
        cams = self.cameras()
        return {
            "type": "tally",
            "cameras": cams,
            "live": [c["n"] for c in cams if c["program"]],
            "next": [c["n"] for c in cams if c["preview"] and not c["program"]],
            "active": bool(self._sources),
            "server_time": time.time(),
        }

    def version(self) -> int:
        return self._version


tally = Tally()


def signature(msg: dict) -> str:
    return json.dumps({k: v for k, v in msg.items() if k != "server_time"}, sort_keys=True)


async def frames(poll_s: float = POLL_S, tick_s: float = TICK_S) -> AsyncIterator[bytes]:
    """The stream: the whole state now, again on each change, and again every `tick_s`."""
    last_version = -1
    last_sent = 0.0
    while True:
        mono = time.monotonic()
        version = tally.version()
        if version != last_version or mono - last_sent >= tick_s:
            last_version, last_sent = version, mono
            yield sse_frame(tally.payload())
        await asyncio.sleep(poll_s)
