"""The caption pipeline (#592): one capture, one transcription session, one translator.

The transcription backend (app/transcribe.py: Scribe through the Vercel gateway, or OpenAI) turns the room's audio
into source-language lines; a chat model translates each finished line into every target that is on
(app/texttranslate.py), through the provider the AI models page picks for translation (app/aimodels.py). Targets
cost tokens, not minutes, and a toggle only changes which languages the next request asks for.

This used to be the engine's own body. Core's engine (app/engine.py) now holds only what every page reads, and this
module plugs into it while the Captions module is on, as one part of core's stream (app/stream.py).
"""
from __future__ import annotations

import asyncio
import logging
import time

from ... import aimodels, config, languages, transcribe
from ...audio import AudioCapture
from ...engine import (BACKEND_KEY_REFUSALS, REHEARSAL_REFUSAL, START_MESSAGES, TRANSCRIPTION_STREAM,
                       TRANSLATION_STREAM, StartRefused, demo_mode, engine)
from ...hub import hub
from ...realtime import RealtimeSession
from ...recorder import recorder
from ...redact import read_blocklist_file
from ...texttranslate import TextTranslator

log = logging.getLogger("relay.captions")


class Pipeline:
    def __init__(self, owner=None) -> None:
        # The engine whose pages this feeds (core's one; a test passes its own).
        self.engine = owner or engine
        self.loop: asyncio.AbstractEventLoop | None = None
        self.capture: AudioCapture | None = None
        self._audio_queues: dict[str, asyncio.Queue] = {}
        # One transcription session feeding a chat model that translates every target.
        self.transcriber: RealtimeSession | None = None
        # What the main feed runs on, and why that is not the backend the operator chose (None: it is).
        self.backend: transcribe.Backend | None = None
        self.backend_warning: str | None = None
        self.translator: TextTranslator | None = None
        # Where the translator's requests go (app/aimodels.py), and why that is not the operator's choice.
        self.translation_route: aimodels.Route | None = None
        self.running = False
        # Wall clock, not monotonic: served to clients on other machines that render the clock against their own.
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.capture = AudioCapture(loop)

    # -- master control -----------------------------------------------
    async def start(self) -> dict:
        # Rehearsal only fakes the viewer feed. A real start here would open billed sessions with the configured
        # key, which is exactly what a rehearsal exists to avoid -- so refuse before touching anything. Not
        # recorded in last_error: the refusal answers one press, and a sticky error would sit in the panel for the
        # whole rehearsal.
        if demo_mode():
            raise StartRefused("rehearsal", REHEARSAL_REFUSAL)
        async with self._lock:
            cfg = config.get()
            backend, warning = transcribe.resolve(cfg)
            key = backend.key(cfg)
            if backend.key_setting and not key:
                refusal = BACKEND_KEY_REFUSALS.get(backend.key_setting, "no_backend_key")
                self.last_error = START_MESSAGES[refusal]
                raise StartRefused(refusal, self.last_error)
            self.backend, self.backend_warning = backend, warning

            hub.history_lines = cfg["history_lines"]
            hub.set_blocklist(read_blocklist_file(config.blocklist_path()))
            await self._stop_all_sessions()
            assert self.capture is not None
            try:
                self.capture.gate_silence = bool(cfg["realtime"].get("gate_silence", True))
                from ... import audiobus  # here: the bus loads the sound library, which this module must not need at import
                self.capture.start(str(cfg["audio_lane"]), 0, audiobus.LANE_SOURCE)
            except RuntimeError:
                # The capture's own sentence (fixed text naming the device), never the exception's.
                self.last_error = self.capture.error or "The audio input could not be opened."
                raise StartRefused("capture", self.last_error) from None

            self.running = True
            self.started_at = time.time()
            src = cfg["source_language"]
            hub.reset(TRANSCRIPTION_STREAM, src)
            enabled = [t for t, spec in sorted(cfg["targets"].items()) if spec.get("enabled")]
            # The recorded window is exactly this start -> the matching stop.
            self._begin_recording(cfg, src, enabled)
            self._open_text_pipeline(enabled, cfg, backend, key)
            # With no target enabled there is nothing to translate, and the captions still run; say so.
            self.last_error = None if enabled else (
                "No target language is enabled, so captions run without translation. Turn one on."
            )
            if enabled and not self.translation_route.key:
                self.last_error = START_MESSAGES["no_translation_key"]
            self.engine._push_status()
            return self.engine.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_all_sessions()
            if self.capture is not None:
                self.capture.stop()
            self.running = False
            self.started_at = None
            self._end_recording()
            self.engine._push_status()
            return self.engine.status()

    async def restart(self) -> dict:
        if self.running:
            await self.stop()
            return await self.start()
        return self.engine.status()

    # -- recording ------------------------------------------------------
    def _begin_recording(self, cfg: dict, source: str, targets: list[str]) -> None:
        """Open a run file, if recording is on. A failure here is logged by the
        recorder and leaves `line_sink` unset: the event still goes ahead."""
        hub.line_sink = None
        rec = cfg.get("recording") or {}
        if not rec.get("enabled"):
            return
        recorder.configure(config.recordings_path(), rec.get("keep_runs", 20))
        if recorder.start_run(source, targets):
            hub.line_sink = recorder.append

    def _end_recording(self) -> None:
        hub.line_sink = None
        recorder.finish_run()

    # -- per-target toggling ------------------------------------------
    async def apply_target(self, target: str, enabled: bool) -> None:
        async with self._lock:
            # A target is a key in the next request, not a session.
            if self.translator is not None:
                if enabled:
                    hub.reset(TRANSLATION_STREAM, target)
                    self.translator.targets.add(target)
                else:
                    self.translator.targets.discard(target)
            self.engine._push_status()

    async def apply_ai(self) -> dict:
        """Point the running translator at the provider and model the AI models page now picks, without
        touching the transcription session or any other feed."""
        async with self._lock:
            if self.translator is not None:
                route = aimodels.resolve(config.get(), "translation")
                self.translation_route = route
                self.translator.use(route.url, route.key, route.model)
            self.engine._push_status()
            return self.engine.status()

    def _open_text_pipeline(self, enabled: list[str], cfg: dict, backend: transcribe.Backend, key: str) -> None:
        assert self.capture is not None
        rt = cfg["realtime"]
        src = cfg["source_language"]
        # Translation goes where the AI models page says (Vercel by default, the OpenAI key as fallback),
        # whichever backend transcribes.
        route = aimodels.resolve(cfg, "translation")
        self.translation_route = route
        translator = TextTranslator(
            url=route.url,
            api_key=route.key,
            model=route.model,
            reasoning_effort=rt.get("text_reasoning_effort", ""),
            source_language=src,
            on_final=lambda t, x: hub.publish_final(TRANSLATION_STREAM, t, x),
            on_status=self.engine._push_status,
        )
        translator.targets = set(enabled)

        def source_final(text: str) -> None:
            # Translate the line as published, blocklist applied: the list is
            # English and Spanish words, which no filter on the far side could
            # catch once they are Arabic or Chinese. A removed word is never
            # sent to the model, so it cannot reach any language.
            translator.submit(hub.publish_final(TRANSCRIPTION_STREAM, src, text))

        session = backend.session(
            cfg, key,
            name="transcribe",
            source_language=src,
            on_source_delta=lambda d: hub.publish_delta(TRANSCRIPTION_STREAM, src, d),
            on_source_final=source_final,
            on_status=self.engine._push_status,
        )
        translator.start()
        q = self.capture.add_consumer()
        self._audio_queues[session.name] = q
        session.start(q)
        self.transcriber, self.translator = session, translator

    async def _close_text_pipeline(self) -> None:
        session, translator = self.transcriber, self.translator
        self.transcriber = self.translator = None
        if session is not None:
            await session.stop()
            q = self._audio_queues.pop(session.name, None)
            if q is not None and self.capture is not None:
                self.capture.remove_consumer(q)
        if translator is not None:
            await translator.stop()

    async def _stop_all_sessions(self) -> None:
        await self._close_text_pipeline()

    def sessions(self) -> list:
        """Every live connection, for health and the meter, in a stable order."""
        return [s for s in (self.transcriber, self.translator) if s is not None]

    def live_names(self) -> list[str]:
        return sorted(self.translator.targets) if self.translator is not None else []

    def live_targets(self) -> list[dict]:
        out = []
        for target in self.live_names():
            out.append(
                {
                    "target": target,
                    "label": languages.target_label(target),
                    "iso": languages.target_iso(target),
                    "script": languages.target_script(target),
                    "state": self.translator.state,
                }
            )
        return sorted(out, key=lambda d: d["label"])
