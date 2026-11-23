"""The stream: what Start and Stop mean (#592).

Start no longer means "start captions". It opens the stream (a running flag and a clock), runs captions as one part
of it while the operator wants them (`stream_captions`, on by default), and tells every module that is on, with
`modules.announce("stream.started")`, so each can follow Start and Stop if its own switch says so. Stop is the
same, the other way: `stream.stopped`. A module hears it with `modules.listen("stream.started", fn)` in `attach()`
and `unlisten` in `detach()`; `fn()` runs on the event loop and must not block.

Captions are the Captions module's part of the stream (app/modules/captions/): while it is on it adds itself with
`stream.add_part(...)`, and the engine (app/engine.py) keeps `running` as "the captions are live", while the
stream's `running` is "the operator has started the show". With that module off, Start opens the stream and runs
no captions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import config, modules
from .engine import demo_mode, engine

log = logging.getLogger("relay.stream")

STARTED = "stream.started"
STOPPED = "stream.stopped"


@dataclass
class Part:
    """One thing Start runs: `start` may raise a RuntimeError (the engine's StartRefused) to refuse the whole Start."""
    start: Callable[[], Awaitable[None]]
    stop: Callable[[], Awaitable[None]]
    running: Callable[[], bool]
    wanted: Callable[[], bool]


class Stream:
    def __init__(self):
        self.parts: dict[str, Part] = {}
        self.running = False
        # Wall clock, as the engine's: served to clients on other machines that render the clock themselves.
        self.started_at: float | None = None
        self._lock = asyncio.Lock()

    def captions_wanted(self) -> bool:
        return bool(config.get().get("stream_captions", True))

    def add_part(self, name: str, start, stop, running, *, wanted=lambda: True) -> None:
        """A module adds something Start runs (the Captions module adds "captions") in `attach()`, and takes it
        away with `remove_part` in `detach()`. `wanted()` says whether this Start should run it."""
        self.parts[name] = Part(start, stop, running, wanted)

    def remove_part(self, name: str) -> None:
        self.parts.pop(name, None)

    async def start(self) -> dict:
        """Open the stream. Captions start with it when wanted; if they are refused (no key, no audio) the whole
        start is refused with the engine's own `StartRefused` and nothing is left half started."""
        async with self._lock:
            if self.running:
                return self.status()
            started: list[Part] = []
            try:
                for part in list(self.parts.values()):
                    if part.wanted():
                        await part.start()
                        started.append(part)
            except BaseException:
                for part in reversed(started):      # a refused part leaves nothing half started
                    await part.stop()
                raise
            self.running = True
            self.started_at = time.time()
            modules.announce(STARTED)
            engine._push_status()
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            was = self.running
            self.running = False
            self.started_at = None
            for part in list(self.parts.values()):
                if part.running():
                    await part.stop()
            if was:
                modules.announce(STOPPED)
            engine._push_status()
            return self.status()

    async def apply_parts(self) -> None:
        """The operator turned captions on or off in settings: follow it live, so a running stream gains or loses
        its parts without a stop. A refused start stays in the part's own error for the panel."""
        async with self._lock:
            if not self.running:
                return
            for part in list(self.parts.values()):
                if part.wanted() and not part.running():
                    if demo_mode():
                        continue
                    try:
                        await part.start()
                    except RuntimeError:
                        continue
                elif not part.wanted() and part.running():
                    await part.stop()
            engine._push_status()

    def push_status(self) -> None:
        """Send the status to the admin pages and viewers (the engine owns what it holds)."""
        engine._push_status()

    def status(self) -> dict:
        return {"running": self.running, "started_at": self.started_at, "captions": self.captions_wanted(),
                "captions_available": "captions" in self.parts}


stream = Stream()
