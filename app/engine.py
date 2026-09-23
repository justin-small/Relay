"""The relay: one capture, one session per enabled target, one hub.

Each session is a `gpt-realtime-translate` connection that carries both feeds --
the source transcript and that target's translation -- so there is no separate
transcription session to run or pay for. Toggling a target on/off opens/closes
exactly one session; that toggle is the cost control.

With two or more targets live, every session reports the same source
transcript. Only one of them (`_source_owner`) is allowed to publish it, so the
transcription stream never receives the same words twice.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

from . import config, languages, redact
from .audio import AudioCapture
from .hub import hub
from .realtime import TranslationRelaySession
from .recorder import recorder

log = logging.getLogger("relay.engine")

TRANSCRIPTION_STREAM = "transcription"
TRANSLATION_STREAM = "translation"


REHEARSAL_REFUSAL = (
    "Rehearsal mode: capture is disabled so nothing is billed. The viewer pages "
    "are showing canned captions. Restart Relay without RELAY_DEMO=1 to go live."
)


def demo_mode() -> bool:
    """Rehearsal mode (RELAY_DEMO=1). Read on every call rather than once at
    import, so the value always matches the environment the process runs in."""
    return os.environ.get("RELAY_DEMO") == "1"


class Engine:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.capture: AudioCapture | None = None
        self.relays: dict[str, TranslationRelaySession] = {}
        self._audio_queues: dict[str, asyncio.Queue] = {}
        self._source_owner: str | None = None
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
            raise RuntimeError(REHEARSAL_REFUSAL)
        async with self._lock:
            cfg = config.get()
            key = (cfg.get("openai_api_key") or "").strip()
            if not key:
                self.last_error = "No OpenAI API key set. Add one in the admin panel."
                raise RuntimeError(self.last_error)

            hub.history_lines = cfg["history_lines"]
            hub.set_blocklist(redact.read_blocklist_file(config.blocklist_path()))
            await self._stop_all_sessions()
            assert self.capture is not None
            try:
                self.capture.gate_silence = bool(cfg["realtime"].get("gate_silence", True))
                self.capture.start(cfg.get("audio_device"), cfg.get("input_channel", 0))
            except RuntimeError as exc:
                self.last_error = str(exc)
                raise

            self.running = True
            self.started_at = time.time()
            src = cfg["source_language"]
            hub.reset(TRANSCRIPTION_STREAM, src)
            enabled = [t for t, spec in sorted(cfg["targets"].items()) if spec.get("enabled")]
            # The recorded window is exactly this start -> the matching stop.
            self._begin_recording(cfg, src, enabled)
            for target in enabled:
                self._open_relay(target, cfg, key)
            # Both feeds now come out of the translation sessions, so with no
            # target enabled there is nothing to transcribe either. Say so
            # rather than sitting there looking connected.
            self.last_error = None if enabled else (
                "No target language is enabled, so nothing is being transcribed "
                "or translated. Turn one on."
            )
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
        cfg = config.save({"targets": {target: spec}})

        async with self._lock:
            if target in self.relays:
                await self._close_relay(target)
            if enabled and self.running:
                key = (cfg.get("openai_api_key") or "").strip()
                if key:
                    hub.reset(TRANSLATION_STREAM, target)
                    self._open_relay(target, cfg, key)
            self._push_status()
        return self.status()

    # -- session plumbing ---------------------------------------------
    # There is no turn-detection block to build: the translations endpoint sets
    # phrase boundaries itself and rejects `turn_detection` outright. What the
    # operator steers instead is when *this app* commits a caption line --
    # segment_idle_s / segment_max_idle_s, passed through below.

    def _source_delta(self, target: str, text: str) -> None:
        """Publish the source transcript from one session only -- see the note
        at the top of this module."""
        if target == self._source_owner:
            hub.publish_delta(TRANSCRIPTION_STREAM, config.get()["source_language"], text)

    def _source_final(self, target: str, text: str) -> None:
        if target == self._source_owner:
            hub.publish_final(TRANSCRIPTION_STREAM, config.get()["source_language"], text)

    def _elect_source_owner(self) -> None:
        self._source_owner = min(self.relays) if self.relays else None

    def _open_relay(self, target: str, cfg: dict, key: str) -> None:
        assert self.capture is not None
        session = TranslationRelaySession(
            name=f"translate:{target}",
            api_key=key,
            model=cfg["realtime"]["translate_model"],
            noise_reduction=cfg["realtime"].get("noise_reduction"),
            transcribe_model=cfg["realtime"].get("transcribe_model") or "",
            segment_idle_s=cfg["realtime"].get("segment_idle_s"),
            segment_max_idle_s=cfg["realtime"].get("segment_max_idle_s"),
            source_language=cfg["source_language"],
            target=target,
            on_source_delta=lambda d, t=target: self._source_delta(t, d),
            on_source_final=lambda x, t=target: self._source_final(t, x),
            on_target_delta=lambda d, t=target: hub.publish_delta(TRANSLATION_STREAM, t, d),
            on_target_final=lambda x, t=target: hub.publish_final(TRANSLATION_STREAM, t, x),
            on_status=self._push_status,
        )
        q = self.capture.add_consumer()
        self._audio_queues[target] = q
        session.start(q)
        self.relays[target] = session
        self._elect_source_owner()

    async def _close_relay(self, target: str) -> None:
        session = self.relays.pop(target, None)
        if session is not None:
            await session.stop()
        q = self._audio_queues.pop(target, None)
        if q is not None and self.capture is not None:
            self.capture.remove_consumer(q)
        self._elect_source_owner()

    async def _stop_all_sessions(self) -> None:
        for target in list(self.relays):
            await self._close_relay(target)

    # -- status --------------------------------------------------------
    def live_targets(self) -> list[dict]:
        cfg = config.get()
        out = []
        for target, session in self.relays.items():
            out.append(
                {
                    "target": target,
                    "label": languages.target_label(target),
                    "iso": languages.target_iso(target),
                    "state": session.state,
                }
            )
        return sorted(out, key=lambda d: d["label"])

    def status(self) -> dict:
        # Imported here: the scheduler drives this engine, so it imports us.
        from .scheduler import scheduler

        cfg = config.get()
        sessions = [self.relays[t].health() for t in sorted(self.relays)]
        return {
            "running": self.running,
            "started_at": self.started_at,
            "error": self.last_error,
            "audio": self.capture.status() if self.capture else {},
            "sessions": sessions,
            "viewers": hub.viewer_count(),
            "blocklist": list(hub.blocklist),
            "blocklist_file": str(config.blocklist_path()),
            "source_language": cfg["source_language"],
            "schedule": scheduler.status(),
            "targets": [
                {
                    "target": name,
                    "label": languages.target_label(name),
                    "language_label": languages.target_label(name),
                    "enabled": spec.get("enabled", False),
                    "live": name in self.relays,
                }
                for name, spec in sorted(cfg["targets"].items())
            ],
        }

    def meter(self) -> dict:
        """The small, high-frequency slice of status -- level, speech and each
        session's state. Pushed at 1 Hz so the admin meter stays live without
        re-sending the full status object (which carries the whole blocklist)."""
        a = self.capture.status() if self.capture else {}
        return {
            "type": "meter",
            "running": self.running,
            "started_at": self.started_at,
            "level": a.get("level", 0.0),
            "rms_dbfs": a.get("rms_dbfs", -60.0),
            "peak_dbfs": a.get("peak_dbfs", -60.0),
            "clipping": a.get("clipping", False),
            "clipped_samples": a.get("clipped_samples", 0),
            "speaking": a.get("speaking", False),
            "sessions": [
                {"name": self.relays[t].name, "state": self.relays[t].state}
                for t in sorted(self.relays)
            ],
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
            },
            "live": self.live_targets(),
            "running": self.running,
            "font_px": cfg["default_font_px"],
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
