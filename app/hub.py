"""Caption fan-out.

One `Channel` per (stream, lang). Each holds the current open segment plus a
ring buffer of committed lines so a late-joining viewer sees context
immediately instead of a blank screen.

Wire shape (server -> viewer), per the PRD interface contract:
    {"stream","lang","seq","delta","final","ts"}
plus "text" (the full segment so far) so a viewer that missed deltas can
resync without replaying them.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

import orjson

from .redact import Redactor

MAX_QUEUE = 256

# On an open segment, resend the full text only every Nth delta as a resync
# anchor; the deltas in between carry just the increment. Bytes per segment
# would otherwise grow with the square of its length -- fine at 250 ms VAD
# segments, costly when VAD misses and a segment runs to hundreds of words.
RESYNC_EVERY = 20


def sse_frame(msg: dict, seq: int | None = None) -> bytes:
    """A ready-to-write SSE frame, serialised exactly once at publish time.

    orjson emits UTF-8 directly (no `ensure_ascii` dance), which matters for
    accented text. A truthy `seq` becomes the `id:` line so a browser can send
    `Last-Event-ID` and resume instead of resetting on reconnect.
    """
    prefix = b"id: %d\n" % seq if seq else b""
    return prefix + b"data: " + orjson.dumps(msg) + b"\n\n"


class Channel:
    def __init__(self, stream: str, lang: str, history_lines: int = 40, blocklist=()):
        self.stream = stream
        self.lang = lang
        self.redactor = Redactor(blocklist)
        self.history: deque[dict] = deque(maxlen=history_lines)
        self.open_text = ""
        self.open_seq = 0          # seq of the latest delta in the open segment
        self.delta_count = 0       # deltas since the last resync anchor
        self.subscribers: set[asyncio.Queue] = set()
        self.last_delta_ts: float | None = None

    def key(self) -> str:
        return f"{self.stream}:{self.lang}"

    def snapshot(self) -> dict:
        return {
            "type": "snapshot",
            "stream": self.stream,
            "lang": self.lang,
            "lines": list(self.history),
            "open": self.open_text,
            "ts": time.time(),
        }


class Hub:
    def __init__(self):
        self.channels: dict[str, Channel] = {}
        self._seq = 0
        self._status_subs: set[asyncio.Queue] = set()
        self.history_lines = 40
        self.blocklist: list[str] = []

    def set_blocklist(self, terms) -> None:
        """Apply a blocklist to every channel, current and future.

        Takes effect on the next delta -- no restart, no session cycle.
        """
        from .redact import parse_terms

        self.blocklist = parse_terms(terms)
        for ch in self.channels.values():
            ch.redactor.set_terms(self.blocklist)

    # -- channels -----------------------------------------------------
    def channel(self, stream: str, lang: str) -> Channel:
        key = f"{stream}:{lang}"
        ch = self.channels.get(key)
        if ch is None:
            ch = Channel(stream, lang, self.history_lines, self.blocklist)
            self.channels[key] = ch
        return ch

    def reset(self, stream: str, lang: str) -> None:
        ch = self.channel(stream, lang)
        ch.open_text = ""
        ch.open_seq = 0
        ch.delta_count = 0
        ch.redactor.reset()
        ch.history.clear()
        self._fanout(ch, ch.snapshot())

    # -- publishing ---------------------------------------------------
    def publish_delta(self, stream: str, lang: str, delta: str) -> None:
        if not delta:
            return
        ch = self.channel(stream, lang)
        # Blocklist terms are dropped here, before any viewer sees them. The
        # redactor may withhold this delta entirely if it could still become a
        # banned term -- in which case there is simply nothing to send yet.
        visible = ch.redactor.feed(delta)
        if not visible:
            return
        self._seq += 1
        ch.open_text += visible
        ch.open_seq = self._seq
        ch.last_delta_ts = time.time()
        ch.delta_count += 1
        msg = {
            "stream": stream,
            "lang": lang,
            "seq": self._seq,
            "delta": visible,
            "final": False,
            "ts": ch.last_delta_ts,
        }
        # Full text only on the periodic anchor; the viewer appends the delta
        # otherwise, and a reconnect resyncs from history (see subscribe()).
        if ch.delta_count % RESYNC_EVERY == 0:
            msg["text"] = ch.open_text
        self._fanout(ch, msg)

    def publish_final(self, stream: str, lang: str, text: str | None = None) -> None:
        ch = self.channel(stream, lang)
        tail = ch.redactor.flush()
        if text is not None:
            # The API gave us the settled segment: redact it in one pass so the
            # committed line is authoritative, whatever the delta chunking was.
            final_text = ch.redactor.whole(text).strip()
        else:
            final_text = (ch.open_text + tail).strip()
        ch.redactor.reset()
        ch.open_text = ""
        ch.open_seq = 0
        ch.delta_count = 0
        if not final_text:
            return
        self._seq += 1
        ch.last_delta_ts = time.time()
        line = {"seq": self._seq, "text": final_text, "ts": ch.last_delta_ts}
        ch.history.append(line)
        self._fanout(
            ch,
            {
                "stream": stream,
                "lang": lang,
                "seq": self._seq,
                "delta": "",
                "text": final_text,
                "final": True,
                "ts": ch.last_delta_ts,
            },
        )

    def _fanout(self, ch: Channel, msg: dict) -> None:
        # Serialise once here, not once per subscriber: 300 phones on a feed
        # would otherwise re-encode the same delta 300 times, several times a
        # second. Every queue gets the identical finished frame.
        self._put_frame(ch.subscribers, sse_frame(msg, msg.get("seq")))

    @staticmethod
    def _put_frame(subs: set[asyncio.Queue], frame: bytes) -> None:
        dead = []
        for q in subs:
            try:
                q.put_nowait(frame)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            subs.discard(q)

    def broadcast_viewers(self, msg: dict) -> None:
        """Fan a control message (e.g. a target-set change) to every viewer,
        across all channels. Serialised once, no `id:` -- it is not a caption."""
        frame = sse_frame(msg)
        for ch in list(self.channels.values()):
            self._put_frame(ch.subscribers, frame)

    # -- subscriptions ------------------------------------------------
    async def subscribe(
        self, stream: str, lang: str, last_id: int | None = None
    ) -> tuple[Channel, asyncio.Queue]:
        ch = self.channel(stream, lang)
        q: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        ch.subscribers.add(q)
        for frame in self._resume_frames(ch, last_id):
            await q.put(frame)
        return ch, q

    def _resume_frames(self, ch: Channel, last_id: int | None) -> list[bytes]:
        """Frames to prime a new subscriber. On a reconnect with a known
        position we replay only what was missed -- the committed lines after
        `last_id` plus the current open segment -- so the reader keeps their
        place. If the client has fallen further behind than the ring buffer
        holds, fall back to a full snapshot (which the viewer treats as a
        rebuild)."""
        if last_id:
            oldest = ch.history[0]["seq"] if ch.history else None
            if oldest is None or oldest <= last_id + 1:
                frames = []
                for line in ch.history:
                    if line["seq"] > last_id:
                        frames.append(sse_frame({
                            "stream": ch.stream, "lang": ch.lang, "seq": line["seq"],
                            "delta": "", "text": line["text"], "final": True,
                            "ts": line["ts"],
                        }, line["seq"]))
                if ch.open_text and ch.open_seq > last_id:
                    frames.append(sse_frame({
                        "stream": ch.stream, "lang": ch.lang, "seq": ch.open_seq,
                        "delta": "", "text": ch.open_text, "final": False,
                        "ts": ch.last_delta_ts,
                    }, ch.open_seq))
                return frames
        return [sse_frame(ch.snapshot())]

    def unsubscribe(self, ch: Channel, q: asyncio.Queue) -> None:
        ch.subscribers.discard(q)

    def viewer_count(self) -> int:
        return sum(len(c.subscribers) for c in self.channels.values())

    # -- admin status stream ------------------------------------------
    def subscribe_status(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._status_subs.add(q)
        return q

    def unsubscribe_status(self, q: asyncio.Queue) -> None:
        self._status_subs.discard(q)

    def push_status(self, payload: dict[str, Any]) -> None:
        for q in list(self._status_subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self._status_subs.discard(q)


hub = Hub()
