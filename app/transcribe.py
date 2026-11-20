"""Transcription backends: who turns the room's audio into source-language text.

A feed names a backend in `realtime.backend`; the engine asks this module for it and gets a session back. A
backend takes 24 kHz PCM (the capture's own rate) and yields partial and committed text through the sinks it is
given, and reports its state, errors and cost in `health()` (app/realtime.py's RealtimeSession is the shape).
What stays in core for every backend, because it happens before or after one: silence gating (app/audio.py),
reconnects with backoff (RealtimeSession), the word blocklist (app/hub.py) and the status feed.

Two backends are built in: **scribe** (ElevenLabs Scribe v2 Realtime through the one Vercel AI Gateway key, the
default; word timestamps, and speaker labels when the model moves to Scribe v3 Live) and **openai** (the old key,
kept as a fallback; `gpt-live-transcribe`). A module can add its own with `register_backend`, as audio sources
and outputs are added in app/audio.py: core never imports a module.

An unknown backend, or one whose key is not set, falls back to OpenAI with a warning the operator sees
(`resolve`), so an install that only has the OpenAI key behaves exactly as it did.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

from . import config, languages, netsock
from .realtime import SOURCE, RealtimeSession, TranscriptionSession, TranslationRelaySession

log = logging.getLogger("relay.transcribe")

DEFAULT_BACKEND = "scribe"
FALLBACK_BACKEND = "openai"

GATEWAY_WS = "wss://ai-gateway.vercel.sh/v4/ai/transcription-model"
SCRIBE_MODEL = "elevenlabs/scribe-v2-realtime"
# Vercel's published price for Scribe v2 Realtime: $0.39 per hour of streamed audio.
SCRIBE_PRICE_PER_HOUR = 0.39
# The gateway's frame limit for one binary audio message.
MAX_AUDIO_FRAME_BYTES = 65536


@dataclass
class Backend:
    """One way to transcribe. A module registers its own instance.

    `key_setting` names the saved secret the backend needs (None: it needs none); `translates` says it can also
    translate and speak in one session, which only the OpenAI translations endpoint does -- a backend without
    it is paired with the text translator (app/texttranslate.py). `session(cfg, key, **kw)` returns an object
    shaped like RealtimeSession (start(queue), stop(), state, health()); `kw` carries `name`,
    `source_language`, `on_source_delta`, `on_source_final` and `on_status`.
    """

    id: str
    label: str
    session: Callable[..., RealtimeSession]
    key_setting: str | None = None
    key_label: str = ""
    translates: bool = False
    price_per_hour: float | None = None

    def key(self, cfg: dict) -> str:
        return (cfg.get(self.key_setting) or "").strip() if self.key_setting else ""

    def available(self, cfg: dict) -> bool:
        return self.key_setting is None or bool(self.key(cfg))


_backends: dict[str, Backend] = {}


def register_backend(backend: Backend) -> None:
    _backends[backend.id] = backend


def unregister_backend(backend_id: str) -> None:
    if backend_id in (DEFAULT_BACKEND, FALLBACK_BACKEND):
        raise ValueError("the built-in backends cannot be removed")
    _backends.pop(backend_id, None)


def backends() -> list[Backend]:
    """Every registered backend, the built-in ones first."""
    first = [_backends[i] for i in (DEFAULT_BACKEND, FALLBACK_BACKEND) if i in _backends]
    return first + [b for i, b in sorted(_backends.items()) if i not in (DEFAULT_BACKEND, FALLBACK_BACKEND)]


def resolve(cfg: dict) -> tuple[Backend, str | None]:
    """The backend the main feed runs on, and a sentence for the operator when it is not the one they chose.

    The chosen backend runs when it is registered and its key is saved. Otherwise OpenAI runs when its key is
    saved, with the reason. With neither usable the chosen backend is returned (or OpenAI, if the choice is
    unknown) so the start is refused naming the key it needs.
    """
    wanted = str((cfg.get("realtime") or {}).get("backend") or DEFAULT_BACKEND)
    fallback = _backends[FALLBACK_BACKEND]
    backend = _backends.get(wanted)
    if backend is None:
        warning = f"The transcription backend \"{wanted}\" is not installed or switched on, so OpenAI is used."
        log.warning(warning)
        return fallback, warning
    if backend.available(cfg):
        return backend, None
    if backend.id != FALLBACK_BACKEND and fallback.available(cfg):
        warning = f"{backend.label} needs {backend.key_label or 'its key'}, which is not set, so OpenAI is used."
        log.warning(warning)
        return fallback, warning
    return backend, None


# ------------------------------------------------------------------ OpenAI

def _openai_session(cfg: dict, key: str, **kw) -> RealtimeSession:
    rt = cfg["realtime"]
    return TranscriptionSession(
        api_key=key,
        # Text mode has nothing to translate without the transcript, so an empty transcribe_model (the
        # realtime mode's "source pane off") falls back to the default here.
        model=rt.get("transcribe_model") or config.DEFAULTS["realtime"]["transcribe_model"],
        noise_reduction=rt.get("noise_reduction"),
        segment_idle_s=rt.get("segment_idle_s"),
        segment_max_idle_s=rt.get("segment_max_idle_s"),
        **kw,
    )


def openai_relay(cfg: dict, key: str, *, target: str, transcribe_model: str, **kw) -> TranslationRelaySession:
    """The OpenAI translate-and-speak session, one per target: only the OpenAI backend `translates`."""
    rt = cfg["realtime"]
    return TranslationRelaySession(
        api_key=key,
        model=rt["translate_model"],
        noise_reduction=rt.get("noise_reduction"),
        transcribe_model=transcribe_model,
        segment_idle_s=rt.get("segment_idle_s"),
        segment_max_idle_s=rt.get("segment_max_idle_s"),
        target=target,
        **kw,
    )


# ------------------------------------------------------------------ Scribe v2 Realtime through the Vercel gateway

class ScribeSession(RealtimeSession):
    """Source language only, over the AI Gateway's streaming-transcription WebSocket.

    The wire format is the one @ai-sdk/gateway speaks (its `experimental_streamTranscribe`): the model id rides
    the URL, the key rides the `Sec-WebSocket-Protocol` handshake, the first text frame is
    `transcription-stream.start` with the audio format and provider options, audio goes as binary frames of at
    most 64 KiB, and text frames come back as `transcript-delta`, `transcript-partial`, `transcript-final` and
    `error` parts. ElevenLabs commits a turn on its own after a pause (voice activity), which is what the
    silence gate's gaps produce, so nothing is committed from here.
    """

    kind = "transcribe"
    backend = "scribe"
    price_per_hour = SCRIBE_PRICE_PER_HOUR
    SUBPROTOCOL = "ai-gateway-transcription.v1"
    AUTH_PREFIX = "ai-gateway-auth."
    VAD_SILENCE_S = 0.8

    def __init__(
        self,
        *,
        source_language: str,
        on_source_delta: Callable[[str], None],
        on_source_final: Callable[[str], None],
        segment_idle_s: float | None = None,
        segment_max_idle_s: float | None = None,
        base_url: str = GATEWAY_WS,
        **kw,
    ):
        kw.setdefault("model", SCRIBE_MODEL)
        super().__init__(**kw)
        self.source_language = source_language
        self.base_url = base_url
        if segment_idle_s is not None:
            self.SEGMENT_IDLE_S = segment_idle_s
        if segment_max_idle_s is not None:
            self.SEGMENT_MAX_IDLE_S = segment_max_idle_s
        self._sinks = {SOURCE: (on_source_delta, on_source_final)}
        # Whether this turn has put any text on screen already (through the deltas), and the cumulative text
        # of the last partial, for a gateway that sends partials and no deltas.
        self._turn_text = False
        self._last_partial = ""

    def _url(self) -> str:
        return f"{self.base_url}?ai-model-id={quote(self.model, safe='')}"

    async def _open(self):
        protocols = [self.SUBPROTOCOL, f"{self.AUTH_PREFIX}{self.api_key}"]
        return await netsock.websocket("translation", self._url(), subprotocols=protocols,
                                       max_size=1 << 24, ping_interval=20)

    def _session_update(self) -> dict:
        iso = languages.SOURCE_LANGUAGES[self.source_language]["iso"]
        return {
            "type": "transcription-stream.start",
            "inputAudioFormat": {"type": "audio/pcm", "rate": 24000},
            "providerOptions": {"elevenlabs": {
                "languageCode": iso,
                "streaming": {"commitStrategy": "vad", "includeTimestamps": True,
                              "vadSilenceThresholdSecs": self.VAD_SILENCE_S},
            }},
        }

    async def _pump_audio(self, ws) -> None:
        assert self._audio_q is not None
        while True:
            pcm = await self._audio_q.get()
            self._count(pcm)
            for at in range(0, len(pcm), MAX_AUDIO_FRAME_BYTES):
                await ws.send(pcm[at:at + MAX_AUDIO_FRAME_BYTES])

    async def _commit_idle_segments(self) -> None:
        """Nothing to do: the gateway commits each turn itself, and its final text is the settled line."""
        await asyncio.Event().wait()

    def _handle(self, evt: dict) -> None:
        etype = evt.get("type", "")
        if etype == "transcript-partial":
            # Measured on the live gateway: partials are the whole turn so far, and the model revises them
            # ("Good morning, Evie." becomes "Good morning, everyone. Welcome"), with no deltas at all. Show
            # the text while it only grows; the final replaces whatever was shown.
            text = evt.get("text") or ""
            if not self._turn_text and text.startswith(self._last_partial) and len(text) > len(self._last_partial):
                super()._handle({"type": "transcript-delta", "delta": text[len(self._last_partial):]})
                self._last_partial = text
            return
        if etype == "transcript-final":
            rest = self._partials.get(SOURCE, "").strip()
            self._partials[SOURCE] = ""
            self._last_ch_delta.pop(SOURCE, None)
            self._last_partial = ""
            self._turn_text = False
            text = (evt.get("text") or "").strip() or rest
            if text:
                self.last_delta_ts = time.time()
                self._emit(SOURCE, 1, text)
            return
        if etype == "transcript-delta" and (evt.get("delta") or ""):
            self._turn_text = True
        super()._handle(evt)

    def _delta_channel(self, etype: str) -> str | None:
        return SOURCE if etype == "transcript-delta" else None


def _scribe_session(cfg: dict, key: str, **kw) -> RealtimeSession:
    rt = cfg["realtime"]
    return ScribeSession(
        api_key=key,
        segment_idle_s=rt.get("segment_idle_s"),
        segment_max_idle_s=rt.get("segment_max_idle_s"),
        **kw,
    )


register_backend(Backend(
    id="scribe", label="ElevenLabs Scribe v2 Realtime (Vercel)", session=_scribe_session,
    key_setting="vercel_api_key", key_label="a Vercel AI Gateway key", price_per_hour=SCRIBE_PRICE_PER_HOUR,
))
register_backend(Backend(
    id="openai", label="OpenAI", session=_openai_session,
    key_setting="openai_api_key", key_label="an OpenAI API key", translates=True,
))
