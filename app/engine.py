"""What every page reads about the running relay: the status, the meter and the targets the viewers are told.

The caption pipeline itself (one capture, one transcription session, one translator) is the Captions module's
(app/modules/captions/pipeline.py, #592). While that module is on it plugs into `Engine.pipeline`, as one part of
core's stream (app/stream.py); while it is off this engine answers with captions idle and nothing running.
"""
from __future__ import annotations

import asyncio
import logging
import os

from . import config, languages, redact, streamstatus, transcribe
from .hub import hub
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


class _NoPipeline:
    """What the status reads while the Captions module is off."""
    backend = backend_warning = translation_route = None


class Engine:
    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        # The Captions module's pipeline while that module is on (app/modules/captions/pipeline.py), else None.
        self.pipeline = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    # -- what the pipeline holds, or nothing while captions are off ------
    @property
    def running(self) -> bool:
        return bool(self.pipeline and self.pipeline.running)

    @property
    def started_at(self) -> float | None:
        return self.pipeline.started_at if self.pipeline else None

    @property
    def last_error(self) -> str | None:
        return self.pipeline.last_error if self.pipeline else None

    @property
    def capture(self):
        return self.pipeline.capture if self.pipeline else None

    def _sessions(self) -> list:
        return self.pipeline.sessions() if self.pipeline else []

    def _live_names(self) -> list[str]:
        return self.pipeline.live_names() if self.pipeline else []

    def live_targets(self) -> list[dict]:
        return self.pipeline.live_targets() if self.pipeline else []

    async def restart(self) -> dict:
        if self.pipeline:
            await self.pipeline.restart()
        return self.status()

    async def apply_ai(self) -> dict:
        if self.pipeline:
            await self.pipeline.apply_ai()
        return self.status()

    async def set_target(self, target: str, enabled: bool) -> dict:
        cfg0 = config.get()
        if target not in languages.target_names(cfg0["source_language"]):
            raise KeyError(target)
        spec = dict(cfg0["targets"].get(target, {}))
        spec["enabled"] = enabled
        config.save({"targets": {target: spec}})
        if self.pipeline:
            await self.pipeline.apply_target(target, enabled)
        else:
            self._push_status()
        return self.status()

    # -- status --------------------------------------------------------
    def status(self) -> dict:
        # Imported here: the scheduler and the stream drive this engine, so they import us.
        from .scheduler import scheduler
        from .stream import stream

        cfg = config.get()
        p = self.pipeline or _NoPipeline
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
            "backend": ({"id": p.backend.id, "label": p.backend.label, "warning": p.backend_warning,
                         "price_per_hour": p.backend.price_per_hour}
                        if self.running and p.backend is not None else None),
            # Where translation runs, and the sentence for why it is not the provider the operator chose.
            "translation": ({"provider": p.translation_route.provider, "label": p.translation_route.label,
                             "model": p.translation_route.model, "warning": p.translation_route.warning}
                            if self.running and p.translation_route is not None else None),
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
