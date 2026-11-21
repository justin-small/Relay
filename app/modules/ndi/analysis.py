"""NDI analysis (#300): stream health, frame timing, picture scopes and audio loudness for one source at a time.

Relay's own receivers feed it (the shared pool, #294); NDI Analysis and CloudCast Scope are not bundled. What is measured
follows the CloudCast Scope manual (cloudcastsystemsau.github.io/cloudcast-scope-manual), which the issue names:

* **Timing** (`Timing`): every video frame's arrival, from which the interval min/avg/max and deviation, the measured
  frame rate, jitter, late frames and long gaps come. Always on for a source with a receiver, so the sources list can show a
  health dot. A still picture sends no frames, so a gap there is not a fault: it shows as "quiet", not as broken.
* **Picture** (`scopes`): waveform, RGB parade, vectorscope and a luma histogram, with the readouts and alarms the manual
  lists. 8-bit code values, legal range 16 to 235. Only computed when asked, from the newest frame.
* **Audio** (`AudioMeter`): per-channel RMS and sample peak with hold, true peak (BS.1770-4), clipping, DC, EBU R128
  momentary, short-term and integrated loudness with loudness range, correlation and a spectrum.
* **Recording** (`Recording`): per-frame timing to a CSV for a set time.

The data rate is the decoded picture's size, because the SDK does not report the bytes that crossed the network.
Not covered: SCTE-104 (needs the VANC ancillary data the SDK does not hand over) and audio/video sync (needs the
frames' timestamps, which the receiver does not pass on yet).
"""
from __future__ import annotations

import collections
import csv
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

try:                           # a Relay install has numpy and the Rust engine; a bare checkout may not
    import numpy as np
    import relay_ndi as _native
except ImportError:            # pragma: no cover
    np = None
    _native = None

LATE_FACTOR = 2.0          # an interval this many frame times long is a late frame
LONG_GAP_S = 0.150         # a gap longer than this is flagged whatever the frame rate
QUIET_S = 2.0              # no frame for this long: quiet (a still picture), or stalled
HEALTH_WINDOW_S = 10.0
KEEP = 600                 # frame arrivals kept
MAX_SCOPE_SAMPLES = 200_000
BLACK_PEAK, FROZEN_DIFF, GAMUT_FRACTION, GAMUT_MIN = 20, 0.05, 0.001, 64


# ------------------------------------------------------------------------------------------------- timing
class Timing:
    """Frame arrivals for one source. `record` runs on the receiver's thread, `stats` on any."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t: collections.deque = collections.deque(maxlen=KEEP)     # (arrival, decoded bytes)
        self.frames = self.late = self.long_gaps = 0
        self.bytes = 0
        self.fps = 0.0                                                   # as the sender declares it
        self.size = (0, 0)
        self.started = time.monotonic()
        self._events: collections.deque = collections.deque(maxlen=200)  # (arrival, "late" | "gap")

    def record(self, now: float, nbytes: int, fps: float = 0.0, size: tuple[int, int] = (0, 0)) -> str:
        """Note a frame; returns "" , "late" or "gap" for what this arrival was."""
        with self._lock:
            flag = ""
            if self._t:
                dt = now - self._t[-1][0]
                frame = 1.0 / self.fps if self.fps > 0 else 0.0
                if dt > LONG_GAP_S and dt > (frame or 0.0) * LATE_FACTOR:
                    flag = "gap"
                    self.long_gaps += 1
                elif frame and dt > frame * LATE_FACTOR:
                    flag = "late"
                if flag:
                    self.late += 1
                    self._events.append((now, flag))
            self._t.append((now, nbytes))
            self.frames += 1
            self.bytes += nbytes
            if fps:
                self.fps = fps
            if size[0]:
                self.size = size
            return flag

    def stats(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            ts = list(self._t)
            events = [e for e in self._events if now - e[0] <= HEALTH_WINDOW_S]
            fps, frames, late, gaps = self.fps, self.frames, self.late, self.long_gaps
        out: dict[str, Any] = {"frames": frames, "late": late, "long_gaps": gaps, "declared_fps": fps,
                               "recent_late": len(events), "since_last_s": round(now - ts[-1][0], 2) if ts else None}
        if len(ts) < 3:
            out.update(interval_ms=None, measured_fps=0.0, jitter_ms=None, rate_mbps=0.0, frame_bytes=None)
            return out
        iv = [(b[0] - a[0]) * 1000.0 for a, b in zip(ts, ts[1:])]
        recent = iv[-300:]
        avg = sum(recent) / len(recent)
        sd = math.sqrt(sum((x - avg) ** 2 for x in recent) / len(recent))
        nominal = 1000.0 / fps if fps else avg
        jit = [abs(x - nominal) for x in recent]
        sizes = [b for _, b in ts]
        span = ts[-1][0] - ts[0][0]
        out.update(
            interval_ms={"min": round(min(recent), 2), "avg": round(avg, 2), "max": round(max(recent), 2), "sd": round(sd, 2)},
            measured_fps=round(1000.0 / avg, 3) if avg else 0.0,
            jitter_ms={"mean": round(sum(jit) / len(jit), 2), "peak": round(max(jit), 2)},
            rate_mbps=round(sum(sizes[1:]) * 8 / span / 1e6, 2) if span > 0 else 0.0,
            frame_bytes={"min": min(sizes), "avg": round(sum(sizes) / len(sizes)), "max": max(sizes)})
        return out


def health(t: dict, dropped: int = 0) -> tuple[str, str]:
    """("ok" | "warn" | "bad" | "quiet" | "unknown", why) from Timing.stats and the SDK's dropped-frame count. The
    thresholds are the manual's: frame rate off nominal by 0.2% is amber and 1% red; jitter above a quarter of a frame is
    amber and above half a frame is red."""
    if not t or not t.get("frames"):
        return "unknown", "Not being received"
    since = t.get("since_last_s")
    if since is not None and since > QUIET_S:
        return "quiet", "No frames for %.0f s: a still picture sends none, so this is not always a fault" % since
    iv = t.get("interval_ms")
    if iv is None:
        return "unknown", "Measuring"
    nominal = 1000.0 / t["declared_fps"] if t.get("declared_fps") else iv["avg"]
    off = abs(t["measured_fps"] - t["declared_fps"]) / t["declared_fps"] if t.get("declared_fps") else 0.0
    jit = t["jitter_ms"]
    if t["recent_late"] >= 3 or off > 0.01 or jit["mean"] > nominal / 2 or dropped:
        return "bad", ("%d late or long gaps in the last %d s" % (t["recent_late"], HEALTH_WINDOW_S)
                       if t["recent_late"] >= 3 else "Dropping frames" if dropped else "Frame rate or timing is off")
    if t["recent_late"] or off > 0.002 or jit["mean"] > nominal / 4:
        return "warn", "Timing is a little uneven"
    return "ok", "Steady"


# ------------------------------------------------------------------------------------------------- picture
def scopes(frame: dict, prev: Any = None) -> tuple[dict | None, Any]:
    """The picture scopes and readouts for one frame, and the 128x128 luma grid to pass as `prev` next time (frame
    difference). None when the pixel format is not handled. Computed in Rust (`relay_ndi.scopes`, #606)."""
    data = frame.get("data")
    w, h = int(frame.get("width") or 0), int(frame.get("height") or 0)
    if _native is None or w < 2 or h < 2 or not data:
        return None, prev
    cc = str(frame.get("fourcc", "")).upper().split(".")[-1]
    return _native.scopes(bytes(data) if not isinstance(data, bytes) else data, w, h, cc, prev)


# ------------------------------------------------------------------------------------------------- audio
LEVEL_FLOOR = -120.0


class AudioMeter:
    """Meters one source's audio from the frames it sends: `feed(samples, rate)` with samples shaped (channels, n).
    The measuring is Rust's (`relay_ndi.AudioMeter`, #606); this keeps the lock and the clock."""

    MAX_CHANNELS = 8

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._m = _native.AudioMeter() if _native is not None else None

    @property
    def rate(self) -> int:
        return self._m.rate if self._m is not None else 0

    @property
    def ch(self) -> int:
        return self._m.ch if self._m is not None else 0

    def reset(self) -> None:
        if self._m is not None:
            self._m.reset()

    def feed(self, samples: Any, rate: int, now: float | None = None) -> None:
        if self._m is None or samples is None or getattr(samples, "ndim", 0) != 2 or samples.shape[1] == 0 or rate <= 0:
            return
        now = time.monotonic() if now is None else now
        with self.lock:
            self._m.feed(np.ascontiguousarray(samples[: self.MAX_CHANNELS], dtype=np.float32), int(rate), float(now))

    def reset_stats(self) -> None:
        with self.lock:
            self.reset()

    def snapshot(self, now: float | None = None) -> dict | None:
        if self._m is None:
            return None
        now = time.monotonic() if now is None else now
        with self.lock:
            return self._m.snapshot(float(now))


# ------------------------------------------------------------------------------------------------- source info
def parse_meta(xml: str) -> dict:
    """What a sender says about itself in NDI's connection metadata: <ndi_product .../> (name, model, version, serial)
    and <ndi_capabilities .../> (PTZ, recordable, KVM). Unproved against a real device; a field it lacks is left out."""
    out: dict[str, Any] = {}
    prod = re.search(r"<ndi_product\b([^>]*)/?>", xml or "")
    if prod:
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', prod.group(1)))
        for key, name in (("long_name", "name"), ("short_name", "short"), ("model_name", "model"),
                          ("version", "version"), ("manufacturer", "maker"), ("serial", "serial")):
            if attrs.get(key):
                out[name] = attrs[key][:80]
    cap = re.search(r"<ndi_capabilities\b([^>]*)/?>", xml or "")
    if cap:
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', cap.group(1)))
        out["ptz"] = attrs.get("ntk_ptz") == "true"
        out["recordable"] = attrs.get("ntk_record") == "true" or attrs.get("ntk_rec") == "true"
        out["kvm"] = attrs.get("ntk_kvm") == "true"
    return out


def source_type(rec: dict) -> str:
    """NDI or NDI|HX, from the instance name the sender advertises ("PTZ-1 (NDI HX2)")."""
    return "NDI|HX" if re.search(r"\bHX\d?\b", (rec.get("source") or "") + " " + (rec.get("machine") or ""), re.I) else "NDI"


# ------------------------------------------------------------------------------------------------- recording
MAX_RECORD_S = 3600
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class Recording:
    """Per-frame timing for one source to a CSV for `seconds`. The file stays until the next recording replaces it."""

    def __init__(self, folder: Path, source: str, seconds: int) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.path = folder / (SAFE.sub("_", source).strip("_")[:60] + "-" + stamp + ".csv")
        self.source, self.seconds = source, max(1, min(MAX_RECORD_S, int(seconds)))
        self.began = time.monotonic()
        self.rows = 0
        self.done = False
        self._last: float | None = None
        self._f = open(self.path, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        self._w.writerow(["seconds", "interval_ms", "frame_bytes", "width", "height", "flag"])
        self._lock = threading.Lock()

    def frame(self, now: float, nbytes: int, size: tuple[int, int], flag: str) -> None:
        with self._lock:
            if self.done:
                return
            if now - self.began >= self.seconds:
                self._close()
                return
            self._w.writerow([round(now - self.began, 4), "" if self._last is None else round((now - self._last) * 1000, 3),
                              nbytes, size[0], size[1], flag])
            self._last = now
            self.rows += 1

    def stop(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if not self.done:
            self.done = True
            try:
                self._f.close()
            except OSError:
                pass

    def status(self) -> dict:
        elapsed = time.monotonic() - self.began
        return {"source": self.source, "seconds": self.seconds, "elapsed": round(min(elapsed, self.seconds), 1),
                "rows": self.rows, "active": not self.done}


# ------------------------------------------------------------------------------------------------- per source
class Track:
    """Everything measured for one source. Timing always; scopes and audio only while an analysis is open."""

    def __init__(self) -> None:
        self.timing = Timing()
        self.meter = AudioMeter()
        self.latest: dict | None = None        # newest video frame, kept only while `watch` is set
        self.watch = False
        self.prev_grid = None
        self.meta: dict = {}
        self.dropped = 0                        # the SDK's own count
        self.recording: Recording | None = None
        self.lock = threading.Lock()


class Analyzer:
    """Per-source tracks, fed by the receiver pool's callbacks. `folder` is where recordings go."""

    def __init__(self, folder) -> None:
        self._folder = folder
        self.tracks: dict[str, Track] = {}
        self.last_recording: dict[str, Recording] = {}
        self._lock = threading.Lock()

    def _track(self, source: str) -> Track:
        with self._lock:
            return self.tracks.setdefault(source, Track())

    def drop(self, source: str) -> None:
        """The source's receiver is gone: its numbers are no longer live."""
        with self._lock:
            t = self.tracks.pop(source, None)
        if t is not None and t.recording is not None:
            t.recording.stop()

    # ---- the receiver's thread
    def on_video(self, source: str, frame: dict, now: float | None = None) -> None:
        t = self._track(source)
        now = time.monotonic() if now is None else now
        nbytes = len(frame.get("data") or b"")
        size = (int(frame.get("width") or 0), int(frame.get("height") or 0))
        flag = t.timing.record(now, nbytes, float(frame.get("fps") or 0.0), size)
        if t.watch:
            t.latest = frame
        rec = t.recording
        if rec is not None:
            rec.frame(now, nbytes, size, flag)

    def on_audio(self, source: str, frame: dict) -> None:
        t = self.tracks.get(source)
        if t is not None and t.watch and frame.get("audio") is not None:
            t.meter.feed(frame["audio"], int(frame.get("rate") or 0))

    def on_meta(self, source: str, meta: dict) -> None:
        self._track(source).meta.update(meta)

    def on_perf(self, source: str, perf: dict) -> None:
        self._track(source).dropped = int((perf.get("video") or {}).get("frames_dropped") or 0)

    # ---- any thread
    def watch(self, source: str, on: bool) -> None:
        t = self._track(source)
        t.watch = on
        if not on:
            t.latest = None
            t.prev_grid = None

    def health(self, source: str) -> tuple[str, str]:
        t = self.tracks.get(source)
        if t is None:
            return "unknown", "Not being received"
        return health(t.timing.stats(), t.dropped)

    def snapshot(self, source: str, rec: dict | None = None, want_scopes: bool = True) -> dict:
        t = self.tracks.get(source)
        rec = rec or {}
        if t is None:
            return {"source": source, "live": False, "health": "unknown", "why": "Not being received"}
        st = t.timing.stats()
        hs, why = health(st, t.dropped)
        out: dict[str, Any] = {
            "source": source, "live": True, "health": hs, "why": why,
            "info": {"type": source_type(rec), "host": rec.get("address", ""), "port": rec.get("port", 0),
                     "groups": rec.get("groups", []), "format": _format(t, rec), **t.meta},
            "timing": st, "dropped": t.dropped, "audio": t.meter.snapshot(),
            "recording": t.recording.status() if t.recording else None}
        frame = t.latest
        if want_scopes and frame is not None:
            with t.lock:
                try:
                    sc, t.prev_grid = scopes(frame, t.prev_grid)
                except Exception:             # a malformed frame must not take the page down
                    sc = None
            if sc is not None:
                out["picture"] = sc
        return out

    def record(self, source: str, seconds: int) -> Recording | None:
        t = self.tracks.get(source)
        if t is None:
            return None
        if t.recording is not None:
            t.recording.stop()
        t.recording = self.last_recording[source] = Recording(self._folder, source, seconds)
        return t.recording

    def stop_recording(self, source: str) -> None:
        rec = self.last_recording.get(source)
        if rec is not None:
            rec.stop()
        t = self.tracks.get(source)
        if t is not None:
            t.recording = None


def _format(t: Track, rec: dict) -> dict:
    w, h = t.timing.size
    return {"width": w or rec.get("width", 0), "height": h or rec.get("height", 0), "fps": t.timing.fps or rec.get("fps", 0),
            "audio_rate": t.meter.rate, "audio_channels": t.meter.ch}
