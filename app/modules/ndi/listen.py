"""Listening to an NDI source in a browser (#301): the source's sound, the channels chosen, as 16-bit stereo PCM.

A browser on the LAN opens `/ndi/listen/<source>`; the answer is an endless body of little-endian 16-bit stereo samples
and a header with the sample rate, which the page plays through Web Audio. It takes a lease on the shared receiver pool
(the low-bandwidth stream, so a listener does not cost the picture's bandwidth), and mixing is Rust's (`relay_ndi.mix_s16`).
Nothing here opens a sound device: the sound goes to the browser, not to this computer's speakers.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

try:
    import relay_ndi as _native
except ImportError:       # pragma: no cover
    _native = None

MAX_LISTENERS = 4        # open listening streams at once: each is a stream of PCM to a browser
FIRST_WAIT_S = 5.0       # how long a listener waits for the first sound before being told there is none
QUEUE = 40               # chunks held for a slow browser; older ones are dropped


def parse_channels(text: str) -> list[int] | None:
    """"1,2" as [0, 1] (channels count from 1); None for something that is not a list of channel numbers."""
    if not text:
        return []
    out: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part.isdigit() or not 1 <= int(part) <= 64:
            return None
        if int(part) - 1 not in out:
            out.append(int(part) - 1)
    return out[:32]


def pcm(frame: dict, pick: list[int]) -> bytes:
    """One chunk of sound as stereo 16-bit PCM."""
    if _native is None or frame.get("audio") is None:
        return b""
    return _native.mix_s16(frame["audio"], pick)


class Listeners:
    """Counts the open streams so a busy service cannot be swamped by them."""

    def __init__(self) -> None:
        self._n = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self._n >= MAX_LISTENERS:
                return False
            self._n += 1
            return True

    def give(self) -> None:
        with self._lock:
            self._n = max(0, self._n - 1)

    @property
    def count(self) -> int:
        return self._n


class Stream:
    """One browser's listening stream. `feed` runs on the receiver's thread; `chunks` on the event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, pick: list[int]) -> None:
        self.loop, self.pick = loop, pick
        self.q: asyncio.Queue = asyncio.Queue()
        self.rate = 0
        self.channels = 0
        self.lease: Any = None

    def feed(self, frame: dict) -> None:
        data = pcm(frame, self.pick)
        if not data:
            return
        item = (int(frame.get("rate") or 48000), int(frame["audio"].shape[0]), data)
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:
            pass

    def _put(self, item: tuple) -> None:
        while self.q.qsize() >= QUEUE:
            self.q.get_nowait()
        self.q.put_nowait(item)

    async def first(self, timeout: float = FIRST_WAIT_S) -> bool:
        """Wait for the first chunk (it gives the rate); False when the source sends no sound."""
        try:
            item = await asyncio.wait_for(self.q.get(), timeout)
        except asyncio.TimeoutError:
            return False
        self.rate, self.channels = item[0], item[1]
        self._first = item[2]
        return True

    async def chunks(self):
        yield self._first
        while True:
            try:
                item = await asyncio.wait_for(self.q.get(), 10.0)
            except asyncio.TimeoutError:
                return
            yield item[2]

    def close(self) -> None:
        if self.lease is not None:
            self.lease.release()
            self.lease = None
