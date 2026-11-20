"""The NDI single-source viewer (#296): one source, re-served to browsers as MJPEG.

A **feed** is one source being watched. It holds a lease on the shared receiver pool (so ten people watching one
camera, or the viewer plus a multiview, cost one receiver), encodes the newest frame to JPEG at most `view_fps` times a
second, and hands that same JPEG to every browser watching it. Nothing is encoded while nobody is watching, and a feed
closes (and gives its lease back) when its last viewer leaves. `view_max` feeds can be open at once, so a busy
service cannot swamp the server; a viewer beyond that is told to try later.

Frames arrive on the receiver's thread; browsers are asked on the event loop. The feed wakes them with
`call_soon_threadsafe`. WebRTC would add lower latency and audio; MJPEG is the simple start.
"""
from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
from typing import Any

log = logging.getLogger("relay.ndi.viewer")

BOUNDARY = "relayndiframe"
IDLE_WAIT_S = 10.0       # a viewer waits this long for a frame before the stream gives up and the browser retries


def to_rgb(frame: dict, max_width: int):
    """The frame as a Pillow RGB image no wider than `max_width`, or None for a pixel format not handled here
    (and when Pillow or numpy is missing). Handles the formats NDI delivers: UYVY (the default), BGRA/BGRX and RGBA/RGBX."""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    w, h, data = int(frame.get("width") or 0), int(frame.get("height") or 0), frame.get("data")
    if w < 2 or h < 2 or not data:
        return None
    cc = str(frame.get("fourcc", "")).upper().split(".")[-1]
    try:
        if cc in ("BGRA", "BGRX", "RGBA", "RGBX"):
            a = np.frombuffer(data, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4)
            rgb = a[:, :, 2::-1] if cc.startswith("BGR") else a[:, :, :3]
            img = Image.fromarray(np.ascontiguousarray(rgb), "RGB")
        elif cc == "UYVY":
            img = _uyvy(np, Image, data, w, h)
        else:
            return None
    except (ValueError, TypeError):
        return None
    if img.width > max_width:
        img = img.resize((max_width, max(1, round(img.height * max_width / img.width))))
    return img


def _uyvy(np, Image, data, w: int, h: int):
    """UYVY (4:2:2) to RGB, BT.709 limited range, with the chroma of each pixel pair shared by both."""
    w -= w % 2
    a = np.frombuffer(data, dtype=np.uint8, count=w * h * 2).reshape(h, w // 2, 4).astype(np.float32)
    u, y0, v, y1 = a[:, :, 0] - 128.0, (a[:, :, 1] - 16.0) * 1.1644, a[:, :, 2] - 128.0, (a[:, :, 3] - 16.0) * 1.1644
    r = 1.7927 * v
    g = -0.2132 * u - 0.5329 * v
    b = 2.1124 * u
    out = np.empty((h, w, 3), dtype=np.uint8)
    for i, y in ((0, y0), (1, y1)):
        out[:, i::2, 0] = np.clip(y + r, 0, 255)
        out[:, i::2, 1] = np.clip(y + g, 0, 255)
        out[:, i::2, 2] = np.clip(y + b, 0, 255)
    return Image.fromarray(out, "RGB")


def encode(frame: dict, max_width: int, quality: int) -> bytes | None:
    """One NDI video frame as JPEG bytes, or None when it cannot be."""
    img = to_rgb(frame, max_width)
    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class Feed:
    def __init__(self, source: str, owner: "Viewers | None") -> None:
        self.source = source
        self.owner = owner
        self.jpeg = b""
        self.seq = 0
        self.size = (0, 0)
        self.viewers = 0
        self._last = 0.0
        self._lease: Any = None
        self._waiters: list[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = []
        self._lock = threading.Lock()
        self.failed = False

    # ---- the receiver's thread
    def on_frame(self, frame: dict) -> None:
        now = time.monotonic()
        s = self.owner.settings()
        if self.viewers <= 0 or now - self._last < 1.0 / s["view_fps"]:
            return
        self._last = now
        try:
            jpeg = encode(frame, s["view_width"], s["view_quality"])
        except Exception:
            log.exception("encoding an NDI frame failed")
            jpeg = None
        if jpeg is None:
            self.failed = True
            return
        self.publish(jpeg, (int(frame.get("width", 0)), int(frame.get("height", 0))))

    def publish(self, jpeg: bytes, size: tuple[int, int]) -> None:
        """Set the newest picture and wake every browser waiting for one (any thread)."""
        with self._lock:
            self.jpeg, self.seq, self.size = jpeg, self.seq + 1, size
            waiters, self._waiters = self._waiters, []
        for loop, fut in waiters:
            try:
                loop.call_soon_threadsafe(lambda f=fut: f.done() or f.set_result(None))
            except RuntimeError:
                pass

    # ---- the event loop
    async def next(self, after: int, timeout: float = IDLE_WAIT_S) -> tuple[int, bytes] | None:
        """(sequence, JPEG) of a frame newer than `after`, waiting for one; None when none came within `timeout`."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        with self._lock:
            if self.seq != after and self.jpeg:
                return self.seq, self.jpeg
            self._waiters.append((loop, fut))
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            with self._lock:
                self._waiters = [w for w in self._waiters if w[1] is not fut]
        with self._lock:
            return (self.seq, self.jpeg) if self.jpeg else None


class Viewers:
    """The open feeds. `subscribe` is core's `ndi_subscribe` (the shared pool); `settings` gives the viewer limits."""

    def __init__(self, subscribe, settings) -> None:
        self.subscribe, self.settings = subscribe, settings
        self.feeds: dict[str, Feed] = {}
        self._lock = threading.Lock()

    def open(self, source: str) -> Feed | None:
        """Start (or join) the feed for `source`. None when NDI is off or cannot receive it, or `view_max` other
        sources are already being watched."""
        with self._lock:
            feed = self.feeds.get(source)
            if feed is None:
                if len(self.feeds) >= self.settings()["view_max"]:
                    return None
                feed = Feed(source, self)
                feed.viewers = 1          # counted before the first frame can arrive
                lease = self.subscribe(source, feed.on_frame)
                if lease is None:
                    return None
                feed._lease = lease
                self.feeds[source] = feed
                return feed
            feed.viewers += 1
            return feed

    def close(self, feed: Feed) -> None:
        with self._lock:
            feed.viewers -= 1
            if feed.viewers > 0:
                return
            if self.feeds.get(feed.source) is feed:
                del self.feeds[feed.source]
        if feed._lease is not None:
            feed._lease()

    def count(self) -> dict[str, int]:
        with self._lock:
            return {s: f.viewers for s, f in self.feeds.items()}

    def close_all(self) -> None:
        with self._lock:
            feeds, self.feeds = list(self.feeds.values()), {}
        for f in feeds:
            f.viewers = 0
            if f._lease is not None:
                f._lease()


async def mjpeg(feed: Feed):
    """The multipart body for one browser: each new JPEG as a part, until the source goes quiet."""
    seq = 0
    while True:
        got = await feed.next(seq)
        if got is None:
            return
        seq, jpeg = got
        yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n").encode() + jpeg + b"\r\n"
