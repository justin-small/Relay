"""The status feed (#224) and the shared stream status modules publish into (#249).

    GET /stream?stream=status

For the sound desk and the booth: is Relay capturing, how hot is the input,
which languages are live, how many are watching, and -- from the modules that
watch a livestream (OBS, Resi, YouTube, ...) -- is the stream on air and
healthy. Read by a panel on the Status feed and by the /status screen.

On the viewer port with no login, like the caption feeds, so it carries no
error text, no addresses and no keys: a stream entry says `health` only, and
the reason stays on the module's admin page (`detail`, never sent here).

Each message is the whole state. One is sent when something shown changes,
and the whole state again every TICK_S so the input level and the clock move
and a panel's silence timer never fires. The input level is a clock-like field:
it moves on every poll, so only its coarse `state` counts as a change.

Core never imports a module. A module publishes with
`streamstatus.streams.publish("<module id>", ...)` from its own work and calls
`streams.clear("<module id>")` in detach(), so a module switched off leaves
nothing behind.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import AsyncIterator

from .hub import hub, sse_frame

STREAM = "status"
POLL_S = 1.0
TICK_S = 5.0
MAX_SOURCES = 8          # more than any church streams to at once
MAX_LANGUAGES = 8
HEALTH = ("ok", "warn", "error", "idle")
LABEL_MAX = 24
DETAIL_MAX = 160
# Below this the input counts as silent: a room with nobody at the microphone, or an input that delivers nothing.
SILENT_DBFS = -50.0

_SOURCE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit]


class StreamStatus:
    """What each stream-watching module last said. One entry per source (a module id); at most MAX_SOURCES."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def publish(self, source: str, *, label: str, on_air: bool | None = None, health: str = "idle",
                live_since: float | None = None, viewers: int | None = None, detail: str = "") -> None:
        """Replace `source`'s entry. `on_air` None means not known (not connected yet); `live_since` is when the
        stream went live (epoch seconds), sent so a screen counts the elapsed time itself; `viewers` None means
        the provider gives no count. `detail` is for the admin page only."""
        if not _SOURCE_RE.fullmatch(source or ""):
            raise ValueError("source must be a module id")
        if source not in self._entries and len(self._entries) >= MAX_SOURCES:
            raise ValueError("too many stream sources")
        if health not in HEALTH:
            raise ValueError("unknown health")
        self._entries[source] = {
            "source": source,
            "label": _text(label, LABEL_MAX) or source,
            "on_air": None if on_air is None else bool(on_air),
            "health": health,
            "live_since": float(live_since) if on_air and isinstance(live_since, (int, float)) else None,
            "viewers": max(0, int(viewers)) if isinstance(viewers, (int, float)) and not isinstance(viewers, bool)
            else None,
            "detail": _text(detail, DETAIL_MAX),
        }

    def clear(self, source: str) -> None:
        self._entries.pop(source, None)

    def entries(self, admin: bool = False) -> list[dict]:
        out = sorted(self._entries.values(), key=lambda e: (e["label"].lower(), e["source"]))
        if admin:
            return [dict(e) for e in out]
        return [{k: v for k, v in e.items() if k != "detail"} for e in out]


streams = StreamStatus()


def input_state(audio: dict, running: bool) -> dict:
    """The input as a screen shows it: `state` is off, lost, clipping, silent or ok; `dbfs` the level, rounded."""
    dbfs = audio.get("rms_dbfs", -60.0)
    dbfs = int(round(dbfs)) if isinstance(dbfs, (int, float)) else -60
    if not running or not audio.get("running"):
        state = "off"
    elif audio.get("reconnecting") or audio.get("error"):
        state = "lost"
    elif audio.get("clipping"):
        state = "clipping"
    elif dbfs <= SILENT_DBFS:
        state = "silent"
    else:
        state = "ok"
    return {"state": state, "dbfs": max(-60, min(0, dbfs))}


def payload(now: float | None = None) -> dict:
    from .engine import engine  # the engine imports the hub, as this file does; import it late all the same

    now = time.time() if now is None else now
    audio = engine.capture.status() if engine.capture else {}
    live = engine.live_targets()
    entries = streams.entries()
    return {
        "type": "status",
        "running": bool(engine.running),
        "started_at": engine.started_at if engine.running else None,
        "input": input_state(audio, bool(engine.running)),
        "languages": [_text(t["label"], LABEL_MAX) for t in live[:MAX_LANGUAGES]],
        "viewers": hub.viewer_count(),
        # On air when any stream source says it is. None when no source is set up to say either way.
        "on_air": any(e["on_air"] for e in entries) if any(e["on_air"] is not None for e in entries) else None,
        "streams": entries,
        "server_time": now,
    }


def signature(msg: dict) -> str:
    """What counts as a change: everything but the clock and the level's exact dB."""
    shown = {k: v for k, v in msg.items() if k != "server_time"}
    shown["input"] = (msg.get("input") or {}).get("state")
    return json.dumps(shown, sort_keys=True, default=str)


async def frames(poll_s: float = POLL_S, tick_s: float = TICK_S) -> AsyncIterator[bytes]:
    """The stream: the whole state now, again on each change, and again every `tick_s`."""
    last_sig: str | None = None
    last_sent = 0.0
    while True:
        msg = payload()
        sig = signature(msg)
        mono = time.monotonic()
        if sig != last_sig or mono - last_sent >= tick_s:
            last_sig, last_sent = sig, mono
            yield sse_frame(msg)
        await asyncio.sleep(poll_s)

