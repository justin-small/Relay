"""The relay: one capture, one transcription session, one translator, one hub.

The transcription backend (app/transcribe.py: Scribe through the Vercel gateway, or OpenAI) turns the room's audio
into source-language lines; a chat model translates each finished line into every target that is on
(app/texttranslate.py), through the provider the AI models page picks for translation (app/aimodels.py). Targets
cost tokens, not minutes, and a toggle only changes which languages the next request asks for.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import aimodels, config, languages, redact, streamstatus, transcribe
from .audio import AudioCapture
from .hub import hub
from .realtime import RealtimeSession
from .texttranslate import TextTranslator
from .recorder import recorder
from .modules import modules

log = logging.getLogger("relay.engine")

TRANSCRIPTION_STREAM = "transcription"
TRANSLATION_STREAM = "translation"


class StartRefused(RuntimeError):
    """Why a start was refused: `key` names its sentence in START_MESSAGES ("capture": the audio device's own
    sentence is in last_error). A RuntimeError, so callers that catch that still do."""

    def __init__(self, key: str, message: str):
        super().__init__(message)
        self.key = key


REHEARSAL_REFUSAL = (
    "Rehearsal mode: capture is disabled so nothing is billed. The viewer pages "
    "are showing canned captions. Restart Relay without RELAY_DEMO=1 to go live."
)
START_MESSAGES = {
    "rehearsal": REHEARSAL_REFUSAL,
    "no_key": "No OpenAI API key set. Add one in the admin panel.",
    "no_vercel_key": "No Vercel AI Gateway API key set. Add one in the admin panel, or pick OpenAI to transcribe.",
    "no_translation_key": "Captions are running, but translating them needs a Vercel AI Gateway key or an OpenAI "
                          "key. Add one in the admin panel.",
    "no_backend_key": "The transcription backend needs a key that is not set. Add it in the admin panel.",
}
BACKEND_KEY_REFUSALS = {"openai_api_key": "no_key", "vercel_api_key": "no_vercel_key"}


def demo_mode() -> bool:
    """Rehearsal mode (RELAY_DEMO=1). Read on every call rather than once at
    import, so the value always matches the environment the process runs in."""
    return os.environ.get("RELAY_DEMO") == "1"


class Engine:
    def __init__(self):
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
        # Wall clock, not monotonic: this timestamp is served to clients on
        # other machines (a Companion surface, a browser) that render the
        # session clock against their own clock. None whenever stopped.
        self.started_at: float | None = None
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.capture = AudioCapture(loop)

    # -- master control -----------------------------------------------
    async def start(self) -> dict:
        # Rehearsal only fakes the viewer feed. A real start here would open
        # billed sessions with the configured key, which is exactly what a
        # rehearsal exists to avoid -- so refuse before touching anything.
        # Not recorded in last_error: the refusal answers one press, and a
        # sticky error would sit in the panel for the whole rehearsal.
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
            hub.set_blocklist(redact.read_blocklist_file(config.blocklist_path()))
            await self._stop_all_sessions()
            assert self.capture is not None
            try:
                self.capture.gate_silence = bool(cfg["realtime"].get("gate_silence", True))
                from . import audiobus  # here: the bus loads the sound library, which this module must not need at import
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
            self._push_status()
            return self.status()

    async def stop(self) -> dict:
        async with self._lock:
            await self._stop_all_sessions()
            if self.capture is not None:
                self.capture.stop()
            self.running = False
            self.started_at = None
            self._end_recording()
            self._push_status()
            return self.status()

    async def restart(self) -> dict:
        if self.running:
            await self.stop()
            return await self.start()
        return self.status()

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
    async def set_target(self, target: str, enabled: bool) -> dict:
        cfg0 = config.get()
        if target not in languages.target_names(cfg0["source_language"]):
            raise KeyError(target)
        spec = dict(cfg0["targets"].get(target, {}))
        spec["enabled"] = enabled
        config.save({"targets": {target: spec}})

        async with self._lock:
            # A target is a key in the next request, not a session.
            if self.translator is not None:
                if enabled:
                    hub.reset(TRANSLATION_STREAM, target)
                    self.translator.targets.add(target)
                else:
                    self.translator.targets.discard(target)
            self._push_status()
        return self.status()

    async def apply_ai(self) -> dict:
        """Point the running translator at the provider and model the AI models page now picks, without
        touching the transcription session or any other feed."""
        async with self._lock:
            if self.translator is not None:
                route = aimodels.resolve(config.get(), "translation")
                self.translation_route = route
                self.translator.use(route.url, route.key, route.model)
            self._push_status()
        return self.status()

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
            on_status=self._push_status,
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
            on_status=self._push_status,
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

    def _sessions(self) -> list:
        """Every live connection, for health and the meter, in a stable order."""
        return [s for s in (self.transcriber, self.translator) if s is not None]

    def _live_names(self) -> list[str]:
        return sorted(self.translator.targets) if self.translator is not None else []

    # -- status --------------------------------------------------------
    def live_targets(self) -> list[dict]:
        cfg = config.get()
        out = []
        for target in self._live_names():
            session = self.translator
            out.append(
                {
                    "target": target,
                    "label": languages.target_label(target),
                    "iso": languages.target_iso(target),
                    "script": languages.target_script(target),
                    "state": session.state,
                }
            )
        return sorted(out, key=lambda d: d["label"])

    def status(self) -> dict:
        # Imported here: the scheduler and the stream drive this engine, so they import us.
        from .scheduler import scheduler
        from .stream import stream

        cfg = config.get()
        sessions = [s.health() for s in self._sessions()]
        live = set(self._live_names())
        return {
            # The show is running (#592): Start was pressed. Captions are one part of it, `captions_running`.
            "running": stream.running,
            "started_at": stream.started_at,
            "captions_running": self.running,
            "stream": stream.status(),
            "error": self.last_error,
            # What transcribes the main feed, and the sentence for why it is not what the operator chose.
            "backend": ({"id": self.backend.id, "label": self.backend.label, "warning": self.backend_warning,
                         "price_per_hour": self.backend.price_per_hour}
                        if self.running and self.backend is not None else None),
            # Where translation runs, and the sentence for why it is not the provider the operator chose.
            "translation": ({"provider": self.translation_route.provider, "label": self.translation_route.label,
                             "model": self.translation_route.model, "warning": self.translation_route.warning}
                            if self.running and self.translation_route is not None else None),
            "backends": [{"id": b.id, "label": b.label, "available": b.available(cfg),
                          "price_per_hour": b.price_per_hour} for b in transcribe.backends()],
            "audio": self.capture.status() if self.capture else {},
            "sessions": sessions,
            "viewers": hub.viewer_count(),
            # What the stream-watching modules say, with their reasons (admin only; the status feed drops them).
            "streams": streamstatus.streams.entries(admin=True),
            "blocklist": list(hub.blocklist),
            "blocklist_file": str(config.blocklist_path()),
            "blocklist_max": redact.MAX_TERMS,
            "source_language": cfg["source_language"],
            "schedule": scheduler.status(),
            "targets": [
                {
                    "target": name,
                    "label": languages.target_label(name),
                    "language_label": languages.target_label(name),
                    "enabled": spec.get("enabled", False),
                    "live": name in live,
                }
                for name, spec in sorted(cfg["targets"].items())
            ],
        }

    def meter(self) -> dict:
        """The small, high-frequency slice of status -- level, speech and each
        session's state. Pushed at 1 Hz so the admin meter stays live without
        re-sending the full status object (which carries the whole blocklist)."""
        from .stream import stream

        a = self.capture.status() if self.capture else {}
        return {
            "type": "meter",
            "running": stream.running,
            "started_at": stream.started_at,
            "level": a.get("level", 0.0),
            "rms_dbfs": a.get("rms_dbfs", -60.0),
            "peak_dbfs": a.get("peak_dbfs", -60.0),
            "clipping": a.get("clipping", False),
            "clipped_samples": a.get("clipped_samples", 0),
            "speaking": a.get("speaking", False),
            "sessions": [{"name": s.name, "state": s.state} for s in self._sessions()],
        }

    def targets_payload(self) -> dict:
        """The viewer-facing target set, as pushed over the caption SSE whenever
        it changes -- so viewers no longer poll /api/targets on a timer."""
        cfg = config.get()
        return {
            "type": "targets",
            "source": {
                "lang": cfg["source_language"],
                "label": languages.SOURCE_LANGUAGES[cfg["source_language"]]["label"],
                "iso": languages.SOURCE_LANGUAGES[cfg["source_language"]]["iso"],
                "script": languages.SOURCE_LANGUAGES[cfg["source_language"]]["script"],
            },
            "live": self.live_targets(),
            # Planning Center service feeds a panel can pick: empty unless the module is on and connected.
            "service": [],
            # ProPresenter feeds a panel can pick (#138): both false unless its module is on and set up.
            "propresenter": {"lyrics": False, "timers": False},
            # The status feed (#224) is core, so always offered; an older Relay leaves this out.
            "status": True,
            "running": self.running,
            "font_px": cfg["default_font_px"],
            # Top bar pills the modules that are on ask for (id, text, count, href).
            "pills": modules.pills(),
            # Each module's health state for the sidebar's dots ({id: state}), refreshed every few seconds.
            "module_health": modules.health_states(),
            # Whatever the modules that are on add (app/modules/): they override the empty defaults above.
            **modules.status(),
        }

    def _push_status(self) -> None:
        try:
            hub.push_status(self.status())
            # Viewers learn about a language going live/dark on their open SSE
            # connection instead of polling; the client dedupes by signature.
            hub.broadcast_viewers(self.targets_payload())
        except Exception:  # pragma: no cover
            log.exception("status push failed")


engine = Engine()
