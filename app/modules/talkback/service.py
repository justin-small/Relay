"""The talkback engine (#93): the crew's talkback mic, transcribed on a session of its own.

It is separate from the main `Engine` (app/engine.py): its own capture (a bus lane, through the `talkback_lane` audio
source below), its own transcription session from the same pluggable backends (app/transcribe.py), its own start,
stop and restart, its own error state. It never translates, and it never touches `hub`: the text lives here, in a
short ring and a list of listeners, so none of it can reach the transcription or translation feeds, a recording or an
export. Silence is gated like the main feed (the capture's own gate), so a quiet comms channel is not billed.
"""
from __future__ import annotations

import asyncio
import collections
import copy
import itertools
import logging
import time
from typing import Callable

from ... import audio, audiobus, config, engine as engine_mod, languages, transcribe

log = logging.getLogger("relay.talkback")

SOURCE_ID = "talkback_lane"
MAX_LINES = 200
MAX_LINE_CHARS = 2000

START_MESSAGES = {
    **engine_mod.START_MESSAGES,
    "no_lane": "Pick the audio bus lane that carries the talkback mic.",
    "no_loop": "Talkback is not running yet. Try again in a moment.",
}


class TalkbackLaneSource(audiobus.LaneSource):
    """A lane as an audio source, one instance for talkback alone: the main capture's `lane` source keeps its own
    state, so the two can listen to the same lane or to different ones."""

    id = SOURCE_ID
    label = "Talkback lane"


def clean_settings(raw) -> dict:
    """The saved settings: a bus lane (0 is none), a backend ("" follows the main feed's) and a source language
    ("" follows the main feed's)."""
    raw = raw if isinstance(raw, dict) else {}
    lane = raw.get("lane")
    if isinstance(lane, bool) or not isinstance(lane, int) or not 1 <= lane <= audio.MAX_LANES:
        lane = 0
    backend = raw.get("backend")
    backend = backend.strip().lower()[:40] if isinstance(backend, str) else ""
    language = raw.get("source_language")
    language = language if isinstance(language, str) and language in languages.SOURCE_LANGUAGES else ""
    return {"lane": lane, "backend": backend, "source_language": language}


class Refused(RuntimeError):
    """A start that cannot go ahead. `key` names its sentence in START_MESSAGES."""

    def __init__(self, key: str, message: str | None = None):
        super().__init__(message or START_MESSAGES[key])
        self.key = key


class TalkbackEngine:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.capture: audio.AudioCapture | None = None
        self.session = None
        self._audio_q: asyncio.Queue | None = None
        self.backend: transcribe.Backend | None = None
        self.backend_warning: str | None = None
        self.lane = 0
        self.language = ""
        self.running = False
        self.started_at: float | None = None
        self.last_error: str | None = None
        # Seconds sent and cost of sessions that ended, so a restart does not reset what the operator is billed.
        self._past_audio_s = 0.0
        self._past_cost = 0.0
        self.partial = ""
        self.lines: collections.deque = collections.deque(maxlen=MAX_LINES)
        self._seq = itertools.count(1)
        self._listeners: list[Callable[[dict], None]] = []
        self._lock = asyncio.Lock()

    # -- module life --------------------------------------------------------------------------------------------
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.capture = audio.AudioCapture(loop)
        self._lock = asyncio.Lock()

    async def shutdown(self) -> None:
        await self.stop()
        self.capture = None
        self.loop = None

    # -- listeners ------------------------------------------------------------------------------------------------
    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """Call `fn(event)` for every talkback event: {"type": "delta", "text"} while a line is open, {"type": "line",
        "seq", "ts", "text"} when it is committed. Runs on the event loop and must not block. Returns the way out."""
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn) if fn in self._listeners else None

    def _tell(self, event: dict) -> None:
        for fn in tuple(self._listeners):
            try:
                fn(event)
            except Exception:  # a listener's fault never stops the session
                log.exception("talkback listener failed")

    def recent(self, limit: int = 50) -> list[dict]:
        return list(self.lines)[-max(1, min(MAX_LINES, limit)):]

    def _on_delta(self, text: str) -> None:
        self.partial = (self.partial + text)[-MAX_LINE_CHARS:]
        self._tell({"type": "delta", "text": self.partial})

    def _on_final(self, text: str) -> None:
        text = (text or "").strip()[:MAX_LINE_CHARS]
        self.partial = ""
        if not text:
            return
        line = {"seq": next(self._seq), "ts": time.time(), "text": text}
        self.lines.append(line)
        self._tell({"type": "line", **line})

    # -- control --------------------------------------------------------------------------------------------------
    def _plan(self) -> tuple[dict, transcribe.Backend, str | None, str, int]:
        """What a start would run on, or Refused."""
        cfg = copy.deepcopy(config.get())
        mine = clean_settings(cfg.get("talkback"))
        if not mine["lane"]:
            raise Refused("no_lane")
        if mine["backend"]:
            cfg["realtime"]["backend"] = mine["backend"]
        backend, warning = transcribe.resolve(cfg)
        if backend.key_setting and not backend.key(cfg):
            key = engine_mod.BACKEND_KEY_REFUSALS.get(backend.key_setting, "no_backend_key")
            raise Refused(key)
        language = mine["source_language"] or cfg["source_language"]
        return cfg, backend, warning, language, mine["lane"]

    async def start(self) -> dict:
        # Rehearsal opens no billed session, and talkback is a billed session: refuse like the main engine.
        if engine_mod.demo_mode():
            self.last_error = None
            raise Refused("rehearsal")
        if self.loop is None or self.capture is None:
            raise Refused("no_loop")
        async with self._lock:
            try:
                cfg, backend, warning, language, lane = self._plan()
            except Refused as exc:
                self.last_error = str(exc)
                raise
            await self._stop_session()
            self.capture.gate_silence = bool(cfg["realtime"].get("gate_silence", True))
            try:
                self.capture.start(str(lane), 0, SOURCE_ID)
            except RuntimeError:
                self.last_error = self.capture.error or "The talkback lane could not be opened."
                raise Refused("capture", self.last_error) from None
            self.backend, self.backend_warning = backend, warning
            self.lane, self.language = lane, language
            session = backend.session(cfg, backend.key(cfg), name="talkback", source_language=language,
                                      on_source_delta=self._on_delta, on_source_final=self._on_final,
                                      on_status=lambda: None)
            self._audio_q = self.capture.add_consumer()
            session.start(self._audio_q)
            self.session = session
            self.running, self.started_at, self.last_error = True, time.time(), None
            self.partial = ""
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_session()
            if self.capture is not None:
                self.capture.stop()
            self.running, self.started_at = False, None
            return self.status()

    async def restart(self) -> dict:
        if not self.running:
            return self.status()
        await self.stop()
        return await self.start()

    async def _stop_session(self) -> None:
        session, self.session = self.session, None
        if session is None:
            return
        await session.stop()
        health = session.health()
        self._past_audio_s += health["audio_s"]
        self._past_cost += health["cost_usd"] or 0.0
        if self.capture is not None and self._audio_q is not None:
            self.capture.remove_consumer(self._audio_q)
        self._audio_q = None

    # -- status ---------------------------------------------------------------------------------------------------
    def _billed(self) -> dict:
        live = self.session.health() if self.session is not None else {"audio_s": 0.0, "cost_usd": None}
        return {"audio_s": round(self._past_audio_s + live["audio_s"], 1),
                "cost_usd": (None if live["cost_usd"] is None and self._past_cost == 0.0
                             else round(self._past_cost + (live["cost_usd"] or 0.0), 4))}

    @staticmethod
    def _main_billed() -> dict:
        """What the main feed's sessions have sent, next to talkback's: the second stream of billed audio."""
        sessions = [s.health() for s in engine_mod.engine._sessions()]
        costs = [s["cost_usd"] for s in sessions if s.get("cost_usd") is not None]
        return {"running": engine_mod.engine.running,
                "audio_s": round(sum(s.get("audio_s", 0.0) for s in sessions), 1),
                "cost_usd": round(sum(costs), 4) if costs else None}

    def status(self) -> dict:
        cfg = config.get()
        return {
            "running": self.running,
            "started_at": self.started_at,
            "error": self.last_error,
            "lane": self.lane if self.running else clean_settings(cfg.get("talkback"))["lane"],
            "language": self.language,
            "backend": ({"id": self.backend.id, "label": self.backend.label, "warning": self.backend_warning,
                         "price_per_hour": self.backend.price_per_hour}
                        if self.running and self.backend is not None else None),
            "session": self.session.health() if self.session is not None else None,
            "audio": self.capture.status() if self.capture is not None else {},
            "billed": self._billed(),
            "main_billed": self._main_billed(),
            "partial": self.partial,
        }

    def health(self) -> tuple[str, str]:
        if self.last_error and not self.running:
            return "error", self.last_error
        if not self.running:
            return "idle", "Not running"
        state = self.session.state if self.session is not None else "idle"
        if state == "connected":
            return "ok", "Listening to the talkback mic"
        if state == "error":
            return "error", (self.session.error if self.session else None) or "The transcription session failed."
        return "warn", "Connecting to the transcription service"


service = TalkbackEngine()
