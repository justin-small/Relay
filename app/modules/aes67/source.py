"""The AES67 network stream as an audio source and a bus input (#266). Registered with core while the module is on
(`audio.register_source`), so the engine reads it like any sound device: `audio_source` "aes67", `audio_device` the
stream id (`<group>:<port>`), `input_channel` a channel number from 0 or "mix"."""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np

from ...audio import AudioSource, LaneInput, SourceError, SourceInfo
from .receiver import Receiver
from .sdp import Stream

SOURCE_ID = "aes67"
NOT_FOUND = "The saved AES67 stream is not announced on the network."


def pick(block: np.ndarray, channel: int | str) -> np.ndarray:
    """One channel of a (frames, channels) block, or "mix": the average of all of them."""
    channels = block.shape[1]
    if isinstance(channel, str) and channel.strip().lower() == "mix" and channels > 1:
        return block.mean(axis=1, dtype=np.float32)
    try:
        i = int(channel)
    except (TypeError, ValueError):
        i = 0
    return block[:, min(max(0, i), channels - 1)].copy()


def _info(stream: Stream, channel: int | str, channels: int) -> SourceInfo:
    label = f"AES67: {stream.name}"
    return SourceInfo(rate=stream.rate, device={"label": label, "channels": channels, "stream": stream.id},
                      channel=channel)


class AES67Source(AudioSource):
    id = SOURCE_ID
    label = "AES67 network stream"

    def __init__(self, find: Callable[[str], Stream | None]) -> None:
        self._find = find
        self._rx: Receiver | None = None
        self._lock = threading.Lock()

    def open(self, selector, channel, deliver) -> SourceInfo:
        self.close()
        stream = self._find(str(selector or ""))
        if stream is None:
            raise SourceError(NOT_FOUND)
        if not stream.usable:
            raise SourceError("That AES67 stream cannot be received: " + stream.problem)
        mix = isinstance(channel, str) and channel.strip().lower() == "mix" and stream.channels > 1
        rx = Receiver(stream, lambda block: deliver(pick(block, "mix" if mix else channel)))
        rx.start()
        with self._lock:
            self._rx = rx
        return _info(stream, "mix" if mix else min(max(0, int(channel) if str(channel).isdigit() else 0),
                                                   stream.channels - 1), stream.channels)

    def close(self) -> None:
        with self._lock:
            rx, self._rx = self._rx, None
        if rx is not None:
            rx.stop()

    def alive(self) -> bool:
        rx = self._rx
        return rx is not None and rx.alive()

    def notice(self, selector) -> str | None:
        stream = self._find(str(selector or ""))
        if stream is None:
            return NOT_FOUND
        return None if stream.usable else stream.problem

    def stats(self) -> dict | None:
        rx = self._rx
        return rx.stats() if rx else None

    def lane_input(self) -> LaneInput:
        return AES67LaneInput(self._find)


class AES67LaneInput(LaneInput):
    """Every channel of the stream to the bus, from a receiver of its own."""

    def __init__(self, find: Callable[[str], Stream | None]) -> None:
        self._find = find
        self._rx: Receiver | None = None

    def open(self, selector, deliver) -> SourceInfo:
        self.close()
        stream = self._find(str(selector or ""))
        if stream is None:
            raise SourceError(NOT_FOUND)
        if not stream.usable:
            raise SourceError("That AES67 stream cannot be received: " + stream.problem)
        rx = Receiver(stream, deliver)
        rx.start()
        self._rx = rx
        return _info(stream, 0, stream.channels)

    def close(self) -> None:
        rx, self._rx = self._rx, None
        if rx is not None:
            rx.stop()

    def alive(self) -> bool:
        return self._rx is not None and self._rx.alive()
