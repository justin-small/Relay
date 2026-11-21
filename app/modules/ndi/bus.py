"""NDI sound on the 32-lane audio bus (#301, #599): an NDI source's sound as bus lanes, and bus lanes as an NDI sound source.

Everything Relay plays or hears goes through the bus, so NDI joins it the same way every network audio module does: it
registers one **source** and one **output** (`app/audio.py`) while the module is on.

* **Input** `ndi`: patch a lane to the NDI source named in `device` (as the NDI page lists it, "MACHINE (Source)"). All of
  the source's channels (up to 32) become lanes. The bus resamples whatever rate the sender uses.
* **Output** `ndi`: an audio-only NDI source named "Relay - <device>" that the lanes routed to it are written to, so a
  spoken-translation destination or any lane can be picked up by a mixer, a recorder or another Relay. It sends at 48 kHz.

Neither opens a sound device. The receiver is the shared pool's (one per source however many listen) and the sender is the
Rust engine's (`relay_ndi.AudioSink`), which sends 10 ms at a time on NDI's own clock.
"""
from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from ... import audio

ID = "ndi"
LABEL = "NDI sound"
FIRST_WAIT_S = 4.0          # how long opening an input waits for the source's first sound
LOST_AFTER_S = 5.0          # no sound for this long: the input is lost and the bus reconnects it
MAX_CHANNELS = audio.MAX_LANES
PREFIX = "Relay - "


class NdiInput(audio.LaneInput):
    """One NDI source's sound as a multichannel lane input."""

    def __init__(self, listen: Any) -> None:
        self._listen = listen                 # service.hook_listen(source, on_audio) -> lease | None
        self._lease: Any = None
        self._deliver: Any = None
        self._channels = 0
        self._last = 0.0
        self._first = threading.Event()
        self._rate = 48000

    def open(self, selector, deliver) -> audio.SourceInfo:
        name = str(selector or "").strip()
        if not name:
            raise audio.SourceError("Name the NDI source to take sound from.")
        self._deliver = deliver
        self._first.clear()
        self._lease = self._listen(name, self._on_audio)
        if self._lease is None:
            raise audio.SourceError("NDI is not running, or cannot receive that source.")
        if not self._first.wait(FIRST_WAIT_S):
            self.close()
            raise audio.SourceError("That NDI source is not sending any sound.")
        return audio.SourceInfo(rate=self._rate, device={"label": name, "channels": self._channels}, channel="all")

    def _on_audio(self, frame: dict) -> None:
        planar = frame.get("audio")
        if planar is None or getattr(planar, "ndim", 0) != 2 or planar.shape[1] == 0:
            return
        if not self._channels:
            self._channels = min(int(planar.shape[0]), MAX_CHANNELS)
            self._rate = int(frame.get("rate") or 48000)
        block = np.ascontiguousarray(planar[: self._channels].T, dtype=np.float32)     # (frames, channels)
        if block.shape[1] < self._channels:                       # a chunk with fewer channels: the rest are silent
            block = np.pad(block, ((0, 0), (0, self._channels - block.shape[1])))
        self._last = time.monotonic()
        self._first.set()
        deliver = self._deliver
        if deliver is not None:
            deliver(block)

    def close(self) -> None:
        lease, self._lease, self._deliver = self._lease, None, None
        if lease is not None:
            lease.release()

    def alive(self) -> bool:
        return self._lease is not None and (not self._last or time.monotonic() - self._last < LOST_AFTER_S)


class NdiSource(audio.AudioSource):
    id, label = ID, LABEL

    def __init__(self, listen: Any) -> None:
        self._listen = listen

    def open(self, selector, channel, deliver) -> audio.SourceInfo:
        raise audio.SourceError("NDI sound is patched to a lane on the Audio bus page.")

    def lane_input(self) -> audio.LaneInput:
        return NdiInput(self._listen)


class NdiSink(audio.LaneSink):
    """Lanes routed to an NDI audio source: `write` queues, Rust sends."""

    def __init__(self, sink: Any, channels: int) -> None:
        self._sink = sink
        self.rate = int(sink.rate)
        self.channels = channels

    def free(self) -> int:
        return int(self._sink.free())

    def queued(self) -> int:
        return int(self._sink.queued())

    def write(self, block: np.ndarray) -> int:
        return int(self._sink.write(np.ascontiguousarray(block, dtype=np.float32)))

    def close(self) -> None:
        self._sink.close()

    @property
    def underruns(self) -> int:
        return int(self._sink.underruns)


def sink_name(selector: Any) -> str:
    name = " ".join(str(selector or "").split())[:60]
    return PREFIX + (name or "Sound")


class NdiOutput(audio.AudioOutput):
    id, label = ID, LABEL

    def __init__(self, backend: Any) -> None:
        self._backend = backend               # callable returning the NDI backend (or None when NDI is off)

    def play(self, pcm: bytes, rate: int = audio.TARGET_RATE, selector=None) -> None:
        """Nothing Relay plays goes to NDI directly: it is routed to the output on the Audio bus page."""

    def lane_sink(self, selector, channels: int) -> audio.LaneSink:
        backend = self._backend()
        if backend is None or not hasattr(backend, "open_audio_sink"):
            raise audio.SourceError("NDI is not running.")
        try:
            sink = backend.open_audio_sink(sink_name(selector), max(1, min(int(channels), MAX_CHANNELS)), audio.LANE_RATE)
        except Exception:
            raise audio.SourceError("NDI would not create that sound output.") from None
        return NdiSink(sink, max(1, min(int(channels), MAX_CHANNELS)))
