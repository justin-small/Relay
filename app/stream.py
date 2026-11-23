"""The stream: what Start and Stop mean (#592).

Start no longer means "start captions". It opens the stream (a running flag and a clock), runs captions as one part
of it while the operator wants them (`stream_captions`, on by default), and tells every module that is on, with
`modules.announce("stream.started")`, so each can follow Start and Stop if its own switch says so. Stop is the
same, the other way: `stream.stopped`. A module hears it with `modules.listen("stream.started", fn)` in `attach()`
and `unlisten` in `detach()`; `fn()` runs on the event loop and must not block.

Captions are the engine (app/engine.py). The engine keeps its own `running`, which stays "the captions are live",
and the stream's `running` is "the operator has started the show".
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import config, modules
from .engine import demo_mode, engine

log = logging.getLogger("relay.stream")

STARTED = "stream.started"
STOPPED = "stream.stopped"


class Stream:
    def __init__(self):
        self.running = False
        # Wall clock, as the engine's: served to clients on other machines that render the clock themselves.
        self.started_at: float | None = None
        self._lock = asyncio.Lock()

    def captions_wanted(self) -> bool:
        return bool(config.get().get("stream_captions", True))

    async def start(self) -> dict:
        """Open the stream. Captions start with it when wanted; if they are refused (no key, no audio) the whole
        start is refused with the engine's own `StartRefused` and nothing is left half started."""
        async with self._lock:
            if self.running:
                return self.status()
            if self.captions_wanted():
                await engine.start()
            self.running = True
            self.started_at = engine.started_at if engine.running and engine.started_at else time.time()
            modules.announce(STARTED)
            engine._push_status()
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            was = self.running
            self.running = False
            self.started_at = None
            if engine.running:
                await engine.stop()
            if was:
                modules.announce(STOPPED)
            engine._push_status()
            return self.status()

    async def apply_captions(self) -> None:
        """The operator turned captions on or off in settings: follow it live, so a running stream gains or loses
        captions without a stop. A refused start stays in the engine's own error for the panel."""
        async with self._lock:
            if not self.running:
                return
            if self.captions_wanted() and not engine.running:
                if demo_mode():
                    return
                try:
                    await engine.start()
                except RuntimeError:
                    return
            elif not self.captions_wanted() and engine.running:
                await engine.stop()
            engine._push_status()

    def push_status(self) -> None:
        """Send the status to the admin pages and viewers (the engine owns what it holds)."""
        engine._push_status()

    def status(self) -> dict:
        return {"running": self.running, "started_at": self.started_at, "captions": self.captions_wanted()}


stream = Stream()
