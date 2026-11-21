"""The remote intercom's mixer: its own Rust audio engine, with no sound device (#605).

Each audio track in a channel's LiveKit room is an input lane of this engine, fed from Python with `push` (a frame
in, no mixing); each channel's mix is an output of the engine, drained with `pull` on the Cloudflare track's 20 ms
clock. The summing, gain and buffering happen in Rust, on threads that do not wait for Python, so a busy event loop
costs timing (the frame is pulled a little late) and not samples.

Audio from the main bus (a program feed, a talkback lane) reaches a channel through a link that runs in Rust from the
main engine's lane into this engine's input (`relay_bus` links): Python only says which lane goes to which channel.

The engine has 32 lanes in all. A track or feed past that is refused (`add_track` returns None) and left out of the mix.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger("relay.intercom_remote")

RATE = 48_000
FRAME = RATE // 50                  # 20 ms, what Opus and aiortc work in
LANES = 32
LATENCY_MS = 120                    # each mix is held about 60 ms deep: at 40 ms a real LiveKit room underran constantly
_FULL = 32768.0


def _input(source: str, device: str) -> str:
    """The engine's id for an external input (`source:device`, lower case)."""
    return f"{source}:{device}".strip().lower()


def out_id(channel_id: int) -> str:
    return f"ch{int(channel_id)}"


class MixBus:
    """The engine, and the lanes, outputs and links it needs for the tracks and feeds that are live now."""

    def __init__(self, backend: str = "none") -> None:
        import relay_bus

        self.engine = relay_bus.Bus(backend)
        self._lock = threading.RLock()
        self._channels: set[int] = set()
        self._lanes: dict[tuple[str, str], dict[str, Any]] = {}     # (kind, key) -> {lane, channel, gain_db, link}
        self._applied = False

    # ------------------------------------------------------------------ the configuration
    def _free_lane(self) -> int | None:
        used = {v["lane"] for v in self._lanes.values()}
        return next((n for n in range(1, LANES + 1) if n not in used), None)

    def _apply(self) -> None:
        lanes, matrix = [], []
        for (kind, key), v in self._lanes.items():
            lanes.append({"lane": v["lane"], "name": key, "source": kind, "device": key, "channel": 0})
            matrix.append({"in": v["lane"], "out": out_id(v["channel"]), "ch": 1, "gain_db": v["gain_db"]})
        outputs = [{"id": out_id(c), "name": f"Channel {c}", "output": "mix", "device": None, "channels": 1,
                    "latency_ms": LATENCY_MS} for c in sorted(self._channels)]
        self.engine.apply({"enabled": True, "lanes": lanes, "outputs": outputs, "matrix": matrix})
        self._applied = True

    # ------------------------------------------------------------------ channels
    def open_channel(self, channel_id: int) -> None:
        with self._lock:
            if channel_id not in self._channels:
                self._channels.add(channel_id)
                self._apply()

    def close_channel(self, channel_id: int) -> None:
        with self._lock:
            if channel_id not in self._channels:
                return
            for kind, key in [k for k, v in self._lanes.items() if v["channel"] == channel_id]:
                self._drop(kind, key)
            self._channels.discard(channel_id)
            self._apply()

    def pull(self, channel_id: int) -> np.ndarray:
        """The channel's next 20 ms as int16 mono: silence when nothing is playing."""
        try:
            got = self.engine.pull(out_id(channel_id), FRAME).reshape(-1)
        except ValueError:          # the channel was closed while its track was still asking: nothing to play
            return np.zeros(FRAME, np.int16)
        return np.clip(got * _FULL, -_FULL, _FULL - 1).astype(np.int16)

    # ------------------------------------------------------------------ tracks (from the rooms)
    def add_track(self, channel_id: int, key: str) -> bool:
        """Make `key` (one room track) an input of the channel's mix. False when the engine has no lane left."""
        with self._lock:
            if channel_id not in self._channels or ("track", key) in self._lanes:
                return channel_id in self._channels
            lane = self._free_lane()
            if lane is None:
                log.warning("intercom_remote: all %d mixer lanes are in use; %s is left out of channel %s",
                            LANES, key, channel_id)
                return False
            self._lanes[("track", key)] = {"lane": lane, "channel": channel_id, "gain_db": 0.0, "link": None}
            self._apply()
            return True

    def push(self, key: str, samples: np.ndarray) -> None:
        """One frame of a track: int16 mono at 48 kHz. A track that is gone is ignored."""
        pcm = np.asarray(samples, dtype=np.float32) * (1.0 / _FULL)
        try:
            self.engine.push(_input("track", key), pcm, RATE)
        except ValueError:          # the track was dropped between the frame arriving and now
            pass

    def drop_track(self, key: str) -> None:
        with self._lock:
            if ("track", key) in self._lanes:
                self._drop("track", key)
                self._apply()

    # ------------------------------------------------------------------ feeds (from the main bus)
    def add_feed(self, channel_id: int, source: Any, lane: int, gain_db: float = 0.0) -> bool:
        """Carry lane `lane` of the bus `source` (a `relay_bus.Bus`, usually the main bus's engine) into the channel's
        mix at `gain_db`. The audio goes Rust to Rust; Python is not in its path."""
        key = f"{channel_id}/{lane}"
        with self._lock:
            if channel_id not in self._channels:
                return False
            if ("feed", key) in self._lanes:
                self._lanes[("feed", key)]["gain_db"] = float(gain_db)       # a new gain, same link
                self._apply()
                return True
            mine = self._free_lane()
            if mine is None:
                log.warning("intercom_remote: all %d mixer lanes are in use; main lane %s is left out of channel %s",
                            LANES, lane, channel_id)
                return False
            self._lanes[("feed", key)] = {"lane": mine, "channel": channel_id, "gain_db": float(gain_db), "link": None}
            self._apply()           # the input must exist before the link starts carrying audio to it
            try:
                self._lanes[("feed", key)]["link"] = self.engine.link_from(source, lane, _input("feed", key))
            except Exception:
                del self._lanes[("feed", key)]
                self._apply()
                raise
            return True

    def drop_feed(self, channel_id: int, lane: int) -> None:
        with self._lock:
            key = f"{channel_id}/{lane}"
            if ("feed", key) in self._lanes:
                self._drop("feed", key)
                self._apply()

    def feed_status(self, channel_id: int, lane: int) -> dict | None:
        """`{carried, refused, dropped}` frames of the feed, or None when there is no such feed."""
        with self._lock:
            v = self._lanes.get(("feed", f"{channel_id}/{lane}"))
            return self.engine.link_status(v["link"]) if v and v["link"] is not None else None

    def _drop(self, kind: str, key: str) -> None:
        v = self._lanes.pop((kind, key))
        if v["link"] is not None:
            self.engine.close_link(v["link"])

    # ------------------------------------------------------------------ life
    def status(self) -> dict:
        return self.engine.status()

    def close(self) -> None:
        with self._lock:
            for kind, key in list(self._lanes):
                self._drop(kind, key)
            self._channels.clear()
            self.engine.close()
