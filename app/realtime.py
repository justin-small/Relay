"""OpenAI Realtime sessions over WebSocket.

One kind: TranslationRelaySession -- a `gpt-realtime-translate` connection that
emits *both* transcripts on a single socket:

    session.input_transcript.*   -> source-language text  (transcription stream)
    session.output_transcript.*  -> target-language text  (translation stream)
    session.output_audio.*       -> ignored; this app is text-only

That is why there is no separate transcription session any more: one connection
per enabled target carries both feeds, at half the upstream audio and a flat
per-minute price. Sessions are fed captured PCM continuously and reconnect on
their own with backoff, so one failing target never takes down the others.

Event names differ between the GA and the earlier beta shapes of the Realtime
API, so the dispatcher accepts both spellings rather than pinning one.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import ssl
import time
from typing import Any, Callable

import websockets

from . import languages

log = logging.getLogger("relay.realtime")

WS_BASE = "wss://api.openai.com/v1/realtime"
MAX_BACKOFF = 15.0

# Faults that retrying cannot fix. Stop and tell the operator instead of
# hammering the API with a credential it has already rejected.
FATAL_MARKERS = (
    "incorrect api key",
    "invalid_api_key",
    "invalid api key",
    "no api key",
    "insufficient_quota",
    "exceeded your current quota",
    "must be verified",
    "does not have access",
    "http 401",
    "http 403",
)


def _is_fatal(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in FATAL_MARKERS)

# The two transcript directions the translations endpoint emits. Completion
# events are not spelled out in the published guide, so both the ".done" and
# ".completed" forms are accepted; if neither ever arrives the partial buffer is
# still flushed on disconnect.
SOURCE = "source"
TARGET = "target"

# A committed line should end where a sentence does. Includes the CJK forms the
# translation channel emits for those targets.
SENTENCE_END = (".", "?", "!", "\u2026", "\u3002", "\uff01", "\uff1f")

# A held fragment ending in punctuation ("So,") is a finished clause: when the
# next utterance's deltas arrive after a pause they carry no leading space, so
# they would glue on as "So,Why". Only punctuation qualifies -- a pause after a
# letter may be mid-word ("cerez" + "os"), where a space would corrupt it.
CLAUSE_END = tuple(".,;:!?\u2026\u3002\uff01\uff1f\uff0c\u3001")
UTTERANCE_GAP_S = 0.8

SOURCE_DELTA_EVENTS = {
    "session.input_transcript.delta",
    "conversation.item.input_audio_transcription.delta",
}
SOURCE_DONE_EVENTS = {
    "session.input_transcript.done",
    "session.input_transcript.completed",
    "conversation.item.input_audio_transcription.completed",
}
TARGET_DELTA_EVENTS = {
    "session.output_transcript.delta",
    "response.output_text.delta",
    "response.text.delta",
    "response.output_audio_transcript.delta",
}
TARGET_DONE_EVENTS = {
    "session.output_transcript.done",
    "session.output_transcript.completed",
    "response.output_text.done",
    "response.text.done",
    "response.output_audio_transcript.done",
}


def _ssl_context() -> ssl.SSLContext:
    """Trust store that works on a stock python.org install.

    The macOS python.org build ships no root certificates unless the user runs
    Install Certificates.command, so verification fails with
    CERTIFICATE_VERIFY_FAILED. Loading certifi's bundle makes this work out of
    the box on both platforms. Verification stays ON.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:  # pragma: no cover - fall back to the system store
        log.debug("certifi unavailable; using the system trust store")
    return ctx


_SSL = _ssl_context()


async def _connect(url: str, headers: dict[str, str]):
    """websockets renamed extra_headers -> additional_headers in v14."""
    kw = {"max_size": 1 << 24, "ping_interval": 20, "ssl": _SSL}
    try:
        return await websockets.connect(url, additional_headers=headers, **kw)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, **kw)


class RealtimeSession:
    """Base: connection lifecycle, audio pump, backoff, health reporting."""

    kind = "session"

    # The /realtime/translations endpoint namespaces every client event under
    # "session."; it rejects the bare "input_audio_buffer.append" the classic
    # realtime endpoint uses. Override on a subclass if a future endpoint wants
    # the un-prefixed spelling.
    AUDIO_APPEND_TYPE = "session.input_audio_buffer.append"

    # This endpoint streams only "*.delta" -- it never sends a done/completed
    # event -- so a segment is never committed by the API. Without a boundary of
    # our own, open_text grows forever and consecutive utterances are rendered
    # glued together ("...moon." + "The quick..." -> "...moon.The quick..."),
    # because a new utterance's first delta carries no leading space. Commit a
    # channel's partial once its deltas have been quiet this long AND it ends on
    # a sentence boundary. The translation channel lags the source and arrives in
    # bursts, with gaps of over a second *inside* one sentence -- even mid-word --
    # so a bare idle timer chopped "cerezos" into "cerez" + "os". Only a sentence
    # end commits early; anything else waits for SEGMENT_MAX_IDLE_S, which bounds
    # how long a trailing fragment can sit uncommitted.
    SEGMENT_IDLE_S = 1.0
    SEGMENT_MAX_IDLE_S = 10.0

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        model: str,
        noise_reduction: str | None = None,
        on_status: Callable[[], None] | None = None,
    ):
        self.name = name
        self.api_key = api_key
        self.model = model
        self.noise_reduction = noise_reduction
        self.on_status = on_status or (lambda: None)
        # channel -> (on_delta, on_final); filled in by the subclass
        self._sinks: dict[str, tuple[Callable[[str], None], Callable[[str], None]]] = {}

        self.state = "idle"  # idle | connecting | connected | reconnecting | error
        self.error: str | None = None
        self.last_delta_ts: float | None = None
        self.connected_at: float | None = None
        self.reconnects = 0

        self._task: asyncio.Task | None = None
        self._ws: Any = None
        self._audio_q: asyncio.Queue | None = None
        self._stop = asyncio.Event()
        # One partial buffer per channel: the two transcripts interleave on a
        # single socket, so a shared buffer would splice them together.
        self._partials: dict[str, str] = {}
        # channel -> monotonic time of its last delta, for the segment watchdog
        self._last_ch_delta: dict[str, float] = {}
        self.fatal = False

    # -- lifecycle ----------------------------------------------------
    def start(self, audio_q: asyncio.Queue) -> None:
        self._audio_q = audio_q
        self.fatal = False
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"rt:{self.name}")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._set_state("idle")

    def _set_state(self, state: str, error: str | None = None) -> None:
        self.state = state
        self.error = error
        self.on_status()

    # -- connection loop ----------------------------------------------
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._set_state("connecting" if not self.reconnects else "reconnecting")
                url = self._url()
                headers = {"Authorization": f"Bearer {self.api_key}"}
                ws = await _connect(url, headers)
                self._ws = ws
                pump = None
                segmenter = None
                try:
                    await ws.send(json.dumps(self._session_update()))
                    self.connected_at = time.time()
                    self._set_state("connected")
                    backoff = 1.0
                    pump = asyncio.create_task(self._pump_audio(ws))
                    segmenter = asyncio.create_task(self._commit_idle_segments())
                    async for raw in ws:
                        self._handle(json.loads(raw))
                finally:
                    if pump is not None:
                        pump.cancel()
                    if segmenter is not None:
                        segmenter.cancel()
                    with contextlib.suppress(Exception):
                        await ws.close()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                log.warning("[%s] %s", self.name, msg)
                if self.fatal and self.error:
                    # The API already told us why in an `error` event; the
                    # close frame that follows is less useful. Keep the first.
                    self._set_state("error", self.error)
                else:
                    self._set_state("error", msg)
                    if _is_fatal(msg):
                        self.fatal = True
            finally:
                self._ws = None
                self._flush_partial()

            if self._stop.is_set() or self.fatal:
                break
            self.reconnects += 1
            self._set_state("reconnecting", self.error)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                break
            except asyncio.TimeoutError:
                pass
            backoff = min(MAX_BACKOFF, backoff * 2)
        if self.fatal:
            self._set_state("error", self.error)
        else:
            self._set_state("idle")

    async def _commit_idle_segments(self) -> None:
        """Commit a channel's partial once its deltas go quiet.

        Stands in for the done/completed event this endpoint never sends, so
        each utterance lands as its own committed line instead of being appended
        to the previous one.
        """
        while True:
            await asyncio.sleep(0.2)
            now = time.monotonic()
            for channel, text in list(self._partials.items()):
                body = text.strip()
                # Punctuation with no word in it is the tail of the line already
                # committed, not a line of its own. Hold it for the next delta.
                if not body or not any(ch.isalnum() for ch in body):
                    continue
                last = self._last_ch_delta.get(channel)
                if last is None:
                    continue
                idle = now - last
                if idle < self.SEGMENT_IDLE_S:
                    continue
                if idle < self.SEGMENT_MAX_IDLE_S and not body.endswith(SENTENCE_END):
                    continue  # mid-sentence pause -- more of this line is coming
                if not body.endswith(SENTENCE_END):
                    # The backstop fired mid-sentence. Commit only as far as the
                    # last whitespace: a stall can land inside a word ("tierna"
                    # arriving as "tier" + "na"), and splitting there corrupts it.
                    cut = body.rfind(" ")
                    if cut <= 0:
                        continue  # single unfinished word -- keep waiting
                    self._partials[channel] = body[cut:].lstrip()
                    self._last_ch_delta[channel] = now
                    self._emit(channel, 1, body[:cut])
                    continue
                self._partials[channel] = ""
                self._last_ch_delta.pop(channel, None)
                self._emit(channel, 1, body)

    async def _pump_audio(self, ws) -> None:
        """Continuous send -- silence included. No gating (PRD decision)."""
        assert self._audio_q is not None
        while True:
            pcm = await self._audio_q.get()
            payload = base64.b64encode(pcm).decode("ascii")
            await ws.send(json.dumps({"type": self.AUDIO_APPEND_TYPE, "audio": payload}))

    # -- events -------------------------------------------------------
    def _emit(self, channel: str, index: int, text: str) -> None:
        sink = self._sinks.get(channel)
        if sink is not None:
            sink[index](text)

    def _handle(self, evt: dict) -> None:
        etype = evt.get("type", "")
        channel = self._delta_channel(etype)
        if channel is not None:
            delta = evt.get("delta") or ""
            if isinstance(delta, dict):
                delta = delta.get("text", "")
            if delta:
                prev = self._partials.get(channel, "")
                prev_ts = self._last_ch_delta.get(channel)
                now_m = time.monotonic()
                if (prev and prev_ts is not None
                        and (now_m - prev_ts) >= UTTERANCE_GAP_S
                        and prev.endswith(CLAUSE_END)
                        and not delta[:1].isspace()):
                    delta = " " + delta
                self._partials[channel] = prev + delta
                self.last_delta_ts = time.time()
                self._last_ch_delta[channel] = now_m
                self._emit(channel, 0, delta)
            return
        channel = self._final_channel(etype)
        if channel is not None:
            text = evt.get("text") or evt.get("transcript") or self._partials.get(channel, "")
            self._partials[channel] = ""
            self._last_ch_delta.pop(channel, None)
            if text and text.strip():
                self.last_delta_ts = time.time()
                self._emit(channel, 1, text.strip())
            return
        if etype == "error" or etype.endswith(".failed"):
            err = evt.get("error") or evt
            msg = err.get("message") if isinstance(err, dict) else str(err)
            log.error("[%s] API error: %s", self.name, msg)
            self._set_state("error", str(msg))
            if _is_fatal(str(msg)):
                self.fatal = True
                self._stop.set()
            return
        if etype in ("session.created", "session.updated",
                     "transcription_session.created", "transcription_session.updated"):
            log.info("[%s] %s", self.name, etype)

    def _flush_partial(self) -> None:
        """Commit whatever each direction had in flight when the socket died."""
        for channel, text in list(self._partials.items()):
            if text.strip():
                self._emit(channel, 1, text.strip())
        self._partials = {}

    # -- overridden by subclasses -------------------------------------
    def _delta_channel(self, etype: str) -> str | None:
        return None

    def _final_channel(self, etype: str) -> str | None:
        return None

    def _url(self) -> str:
        raise NotImplementedError

    def _session_update(self) -> dict:
        raise NotImplementedError

    def health(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "state": self.state,
            "error": self.error,
            "model": self.model,
            "last_delta_ts": self.last_delta_ts,
            "connected_at": self.connected_at,
            "reconnects": self.reconnects,
            "fatal": self.fatal,
            # Frames this session lost to a stalled uplink (see AudioCapture).
            "dropped_audio": getattr(self._audio_q, "dropped", 0),
        }


class TranslationRelaySession(RealtimeSession):
    """One target language. Audio in; source *and* target transcript text out.

    `gpt-realtime-translate` takes no prompt, no voice and no register control --
    the whole session config is the output language -- so there is nothing here
    for a speaker to talk the model out of. The prompt-injection guard the old
    instruction-driven session needed is structural to this model.
    """

    kind = "translate"

    def __init__(
        self,
        *,
        source_language: str,
        target: str,
        on_source_delta: Callable[[str], None],
        on_source_final: Callable[[str], None],
        on_target_delta: Callable[[str], None],
        on_target_final: Callable[[str], None],
        transcribe_model: str = "",
        segment_idle_s: float | None = None,
        segment_max_idle_s: float | None = None,
        **kw,
    ):
        super().__init__(**kw)
        self.source_language = source_language
        self.target = target
        self.transcribe_model = transcribe_model
        if segment_idle_s is not None:
            self.SEGMENT_IDLE_S = segment_idle_s
        if segment_max_idle_s is not None:
            self.SEGMENT_MAX_IDLE_S = segment_max_idle_s
        self.target_iso = languages.target_iso(target)
        self._sinks = {
            SOURCE: (on_source_delta, on_source_final),
            TARGET: (on_target_delta, on_target_final),
        }

    def _url(self) -> str:
        return f"{WS_BASE}/translations?model={self.model}"

    def _session_update(self) -> dict:
        audio: dict = {"output": {"language": self.target_iso}}
        # No turn detection is sent, and there is no setting for it. The
        # translations endpoint rejects `turn_detection` in session.update
        # ("Unknown parameter: session.audio.input.turn_detection") because a
        # translation session has no turn lifecycle at all -- per the OpenAI
        # cookbook guide [5], "Translation starts from the incoming audio stream
        # itself. There is no response.create, assistant turn, tool call, or
        # conversation state to manage." The endpoint finds phrase boundaries
        # itself; the caption line breaks the operator *can* steer are this
        # app's own, in SEGMENT_IDLE_S / SEGMENT_MAX_IDLE_S below.
        # `turn_detection` (semantic_vad / server_vad, eagerness) belongs to the
        # conversational and /transcription endpoints, not this one, and this
        # one rejects it outright.
        # Only noise reduction is settable on the input side; it is omitted when
        # unset so the endpoint keeps its own default rather than being handed a
        # null.
        # The source-language transcript is opt-in: without an input transcription
        # model the endpoint emits only the translated (output) stream, so the
        # source pane stays empty. Setting it turns on the input_audio_transcription
        # events the SOURCE_* sets listen for.
        inp: dict = {}
        if self.transcribe_model:
            inp["transcription"] = {"model": self.transcribe_model}
        if self.noise_reduction:
            inp["noise_reduction"] = {"type": self.noise_reduction}
        if inp:
            audio["input"] = inp
        return {"type": "session.update", "session": {"audio": audio}}

    def _delta_channel(self, etype: str) -> str | None:
        if etype in SOURCE_DELTA_EVENTS:
            return SOURCE
        if etype in TARGET_DELTA_EVENTS:
            return TARGET
        return None

    def _final_channel(self, etype: str) -> str | None:
        if etype in SOURCE_DONE_EVENTS:
            return SOURCE
        if etype in TARGET_DONE_EVENTS:
            return TARGET
        return None
