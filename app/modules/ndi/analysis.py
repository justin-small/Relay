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

import base64
import collections
import csv
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

try:                           # a Relay install has numpy; a bare checkout may not
    import numpy as np
except ImportError:            # pragma: no cover
    np = None

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
def _planes(frame: dict):
    """(Y, Cb, Cr, rgb_function) as uint8 arrays at the frame's size: 4:2:2 chroma is repeated. None when the pixel format is
    not handled. For RGB sources Y, Cb, Cr are derived with BT.709 (601 at 576 lines or fewer), limited range."""
    w, h, data = int(frame.get("width") or 0), int(frame.get("height") or 0), frame.get("data")
    if np is None or w < 2 or h < 2 or not data:
        return None
    cc = str(frame.get("fourcc", "")).upper().split(".")[-1]
    kr, kb = (0.299, 0.114) if h <= 576 else (0.2126, 0.0722)
    kg = 1 - kr - kb
    if cc == "UYVY":
        w -= w % 2
        a = np.frombuffer(data, dtype=np.uint8, count=w * h * 2).reshape(h, w // 2, 4)
        y = np.empty((h, w), dtype=np.uint8)
        y[:, 0::2], y[:, 1::2] = a[:, :, 1], a[:, :, 3]
        return y, np.repeat(a[:, :, 0], 2, axis=1), np.repeat(a[:, :, 2], 2, axis=1), (kr, kb), True
    if cc in ("BGRA", "BGRX", "RGBA", "RGBX"):
        a = np.frombuffer(data, dtype=np.uint8, count=w * h * 4).reshape(h, w, 4).astype(np.float32)
        r, g, b = (a[:, :, 2], a[:, :, 1], a[:, :, 0]) if cc.startswith("BGR") else (a[:, :, 0], a[:, :, 1], a[:, :, 2])
        y = kr * r + kg * g + kb * b
        cb, cr = (b - y) / (2 * (1 - kb)), (r - y) / (2 * (1 - kr))
        c8 = lambda v: np.clip(np.rint(v), 0, 255).astype(np.uint8)       # noqa: E731
        return c8(16 + y * 219 / 255), c8(128 + cb * 224 / 255), c8(128 + cr * 224 / 255), (kr, kb), False
    return None


def _rgb_from_ycc(y, cb, cr, k):
    """R'G'B' (float, 0..255 nominal) from limited-range Y'CbCr samples."""
    kr, kb = k
    yy = (y.astype(np.float32) - 16) * 255 / 219
    pb, pr = (cb.astype(np.float32) - 128) * 255 / 224, (cr.astype(np.float32) - 128) * 255 / 224
    r = yy + 2 * (1 - kr) * pr
    b = yy + 2 * (1 - kb) * pb
    g = (yy - kr * r - kb * b) / (1 - kr - kb)
    return r, g, b


def _grid(level: "np.ndarray", cols: int) -> "np.ndarray":
    """A 256 (level, top is 255) by `cols` density of how many samples fell at each level in each column bucket."""
    h, w = level.shape
    xs = (np.arange(w) * cols // w).astype(np.int32)
    idx = (np.broadcast_to(xs, (h, w)).astype(np.int64) * 256 + level.astype(np.int64)).ravel()
    return np.bincount(idx, minlength=cols * 256).reshape(cols, 256).T[::-1]


def _pack(counts: "np.ndarray") -> dict:
    """Counts to 0..255 brightness on a log scale (so a faint trace still shows), base64 for the browser."""
    m = float(counts.max()) or 1.0
    img = (np.log1p(counts) / math.log1p(m) * 255).astype(np.uint8)
    return {"w": int(img.shape[1]), "h": int(img.shape[0]), "b64": base64.b64encode(img.tobytes()).decode("ascii")}


def scopes(frame: dict, prev: "np.ndarray | None" = None) -> tuple[dict | None, "np.ndarray | None"]:
    """The picture scopes and readouts for one frame, and the 128x128 luma grid to pass as `prev` next time (frame
    difference). None when the pixel format is not handled."""
    p = _planes(frame)
    if p is None:
        return None, prev
    y, cb, cr, k, _ = p
    h, w = y.shape
    s = max(1, math.ceil(math.sqrt(h * w / MAX_SCOPE_SAMPLES)))
    ys, cbs, crs = y[::s, ::s], cb[::s, ::s], cr[::s, ::s]
    r, g, b = _rgb_from_ycc(ys, cbs, crs, k)
    lvl = lambda v: np.clip(np.rint(v * 219 / 255 + 16), 0, 255).astype(np.int32)      # noqa: E731
    hist = np.bincount(y.ravel(), minlength=256)
    sub, sup = int((y < 16).sum()), int((y > 235).sum())
    lr, lg, lb = lvl(r), lvl(g), lvl(b)
    bad = (lr < 13) | (lr > 238) | (lg < 13) | (lg > 238) | (lb < 13) | (lb > 238)
    illegal = int(bad.sum()) * s * s
    vec = np.bincount(((cbs.astype(np.int64) >> 1) * 128 + (crs.astype(np.int64) >> 1)).ravel(), minlength=128 * 128)
    grid = y[:: max(1, h // 128), :: max(1, w // 128)][:128, :128].astype(np.float32)
    diff = float(np.abs(grid - prev).mean()) if prev is not None and prev.shape == grid.shape else None
    rows, cols = y.max(axis=1), y.max(axis=0)
    black = lambda v: int(np.argmax(v > BLACK_PEAK)) if (v > BLACK_PEAK).any() else len(v)      # noqa: E731
    ar = round(w / h, 4)
    out = {
        "waveform": _pack(_grid(ys.astype(np.int32), 256)),
        "parade": [_pack(_grid(c, 128)) for c in (lr, lg, lb)],
        "vectorscope": _pack(vec.reshape(128, 128).T[::-1]),
        "histogram": [int(v) for v in hist],
        "read": {"luma_min": int(y.min()), "luma_max": int(y.max()), "luma_mean": round(float(y.mean()), 1),
                 "sub_black": sub, "super_white": sup, "illegal_rgb": illegal,
                 "frame_diff": round(diff, 3) if diff is not None else None,
                 "sharpness": round(float(np.abs(np.diff(ys.astype(np.int16), axis=1)).mean()), 2),
                 "letterbox": [black(rows), black(rows[::-1])], "pillarbox": [black(cols), black(cols[::-1])],
                 "aspect": ar, "matrix": "BT.601" if h <= 576 else "BT.709", "width": w, "height": h},
    }
    gamut_px = illegal
    alarms = []
    if int(y.max()) <= BLACK_PEAK:
        alarms.append(["BLACK", "bad"])
    if diff is not None and diff < FROZEN_DIFF:
        alarms.append(["FROZEN", "bad"])
    if gamut_px >= GAMUT_MIN and gamut_px / (w * h) > GAMUT_FRACTION:
        alarms.append(["GAMUT", "warn"])
    out["alarms"] = alarms
    return out, grid


# ------------------------------------------------------------------------------------------------- audio
LEVEL_FLOOR = -120.0


def _db(v: float) -> float:
    return round(20 * math.log10(v), 1) if v > 1e-6 else LEVEL_FLOOR


def _k_coeffs(fs: float):
    """K-weighting (BS.1770): a high shelf then a high-pass, as normalised biquads for the sample rate."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k = math.tan(math.pi * f0 / fs)
    vh = 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = ((vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
             2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    f0, q = 38.13547087602444, 0.5003270373238773
    k = math.tan(math.pi * f0 / fs)
    a0 = 1 + k / q + k * k
    high = (1.0, -2.0, 1.0, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0)
    return shelf, high


def _true_peak_taps(os_: int = 4, taps: int = 96) -> "np.ndarray":
    """BS.1770-4 Annex 2: 4x oversampling with a 96-tap Blackman-windowed sinc, as 4 phases of 24 taps."""
    n = np.arange(taps) - (taps - 1) / 2.0
    h = np.sinc(n / os_) * np.blackman(taps)
    return np.stack([h[p::os_] / h[p::os_].sum() for p in range(os_)])


def _biquad(x: list, c: tuple, st: list) -> list:
    b0, b1, b2, a1, a2 = c
    z1, z2 = st
    out = []
    ap = out.append
    for v in x:                       # transposed direct form II
        o = b0 * v + z1
        z1 = b1 * v - a1 * o + z2
        z2 = b2 * v - a2 * o
        ap(o)
    st[0], st[1] = z1, z2
    return out


def _lufs(power: float) -> float:
    return -0.691 + 10 * math.log10(power) if power > 0 else LEVEL_FLOOR


class AudioMeter:
    """Meters one source's audio from the frames it sends: `feed(samples, rate)` with samples shaped (channels, n)."""

    MAX_CHANNELS = 8

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.rate = 0
        self.ch = 0
        self.last = None
        self.rms, self.peak, self.dc, self.hold, self.hold_t, self.hold_upd = [], [], [], [], [], []
        self.tpeak: list[float] = []
        self.clips = 0
        self.clip_at = 0.0
        self.silent_since: float | None = None
        self.corr = 0.0
        self.spectrum: list[float] = []
        self._tail = None
        self._st: list = []
        self._buf: list[list[float]] = []
        self._sub: collections.deque = collections.deque(maxlen=30)       # 100 ms powers, newest last
        self._blocks: dict[int, list] = {}                                  # 0.1 LU bin -> [count, power sum]
        self._short: dict[int, int] = {}
        self.momentary = self.short = None
        self._taps = _true_peak_taps() if np is not None else None

    def _setup(self, ch: int, rate: int) -> None:
        self.rate, self.ch = rate, ch
        self.rms, self.peak, self.dc = [LEVEL_FLOOR] * ch, [LEVEL_FLOOR] * ch, [0.0] * ch
        self.hold, self.hold_t, self.hold_upd, self.tpeak = [LEVEL_FLOOR] * ch, [0.0] * ch, [0.0] * ch, [LEVEL_FLOOR] * ch
        shelf, high = _k_coeffs(float(rate))
        self._k = (shelf, high)
        self._st = [([0.0, 0.0], [0.0, 0.0]) for _ in range(ch)]
        self._buf = [[] for _ in range(ch)]
        self._tail = np.zeros((ch, 23), dtype=np.float32)
        w = [1.0] * ch
        if ch == 6:
            w = [1.0, 1.0, 1.0, 0.0, 1.41, 1.41]
        elif ch == 5:
            w = [1.0, 1.0, 1.0, 1.41, 1.41]
        self._w = w

    def feed(self, samples: "np.ndarray", rate: int, now: float | None = None) -> None:
        if np is None or samples is None or samples.ndim != 2 or samples.shape[1] == 0 or rate <= 0:
            return
        now = time.monotonic() if now is None else now
        x = np.asarray(samples[: self.MAX_CHANNELS], dtype=np.float32)
        with self.lock:
            if x.shape[0] != self.ch or rate != self.rate:
                self._setup(x.shape[0], rate)
            n = x.shape[1]
            win = x[:, -max(1, int(rate * 0.05)):]
            for i in range(self.ch):
                a = np.abs(x[i])
                pk = _db(float(a.max()))
                self.peak[i] = pk
                self.rms[i] = _db(float(math.sqrt(float((win[i] ** 2).mean()))))
                self.dc[i] = round(float(x[i].mean()), 4)
                if pk >= self.hold[i]:
                    self.hold[i], self.hold_t[i], self.hold_upd[i] = pk, now, now
                elif now - self.hold_t[i] > 1.5:      # held 1.5 s, then falls at 22 dB a second
                    fall = 22.0 * (now - max(self.hold_upd[i], self.hold_t[i] + 1.5))
                    self.hold[i], self.hold_upd[i] = max(pk, self.hold[i] - fall), now
                clip = int((a >= 1.0).sum())
                if clip:
                    self.clips += clip
                    self.clip_at = now
            ext = np.concatenate([self._tail, x], axis=1)
            self._tail = ext[:, -23:]
            for i in range(self.ch):
                top = max(float(np.abs(np.convolve(ext[i], ph, mode="valid")).max()) for ph in self._taps)
                tp = _db(top)
                self.tpeak[i] = max(self.tpeak[i], tp)
            if self.silent_since is None and max(self.rms) < -60:
                self.silent_since = now
            elif max(self.rms) >= -60:
                self.silent_since = None
            if self.ch >= 2:
                l_, r_ = x[0].astype(np.float64), x[1].astype(np.float64)
                den = math.sqrt(float((l_ ** 2).sum() * (r_ ** 2).sum()))
                c = float((l_ * r_).sum() / den) if den > 0 else 0.0
                self.corr = max(-1.0, min(1.0, 0.8 * self.corr + 0.2 * c))
            if n >= 1024:
                seg = x[0][-1024:] * np.hanning(1025)[:-1].astype(np.float32)
                mag = np.abs(np.fft.rfft(seg))[1:513] / 512.0
                self.spectrum = [round(max(-100.0, 20 * math.log10(float(m) + 1e-9)), 1) for m in mag]
            self._loudness(x, now)
            self.last = now

    def _loudness(self, x: "np.ndarray", now: float) -> None:
        block = max(1, self.rate // 10)
        shelf, high = self._k
        for i in range(self.ch):
            if self._w[i] == 0.0:
                continue
            y = _biquad(_biquad(x[i].tolist(), shelf, self._st[i][0]), high, self._st[i][1])
            self._buf[i].extend(v * v for v in y)
        while all(len(self._buf[i]) >= block for i in range(self.ch) if self._w[i]):
            tot = 0.0
            for i in range(self.ch):
                if not self._w[i]:
                    continue
                tot += self._w[i] * (sum(self._buf[i][:block]) / block)
                del self._buf[i][:block]
            self._sub.append(tot)
            if len(self._sub) >= 4:
                m = sum(list(self._sub)[-4:]) / 4
                self.momentary = _lufs(m)
                lk = _lufs(m)
                if lk > -70:
                    bin_ = int(round(lk * 10))
                    rec = self._blocks.setdefault(bin_, [0, 0.0])
                    rec[0] += 1
                    rec[1] += m
            if len(self._sub) >= 30:
                s = sum(self._sub) / 30
                self.short = _lufs(s)
                if self.short > -70:
                    b = int(round(self.short * 10))
                    self._short[b] = self._short.get(b, 0) + 1

    def integrated(self) -> float | None:
        recs = [(b, c, p) for b, (c, p) in self._blocks.items()]
        if not recs:
            return None
        n = sum(c for _, c, _ in recs)
        rel = _lufs(sum(p for _, _, p in recs) / n) - 10
        kept = [(c, p) for b, c, p in recs if b / 10 > rel]
        cnt = sum(c for c, _ in kept)
        return round(_lufs(sum(p for _, p in kept) / cnt), 1) if cnt else None

    def lra(self) -> float | None:
        if not self._short:
            return None
        n = sum(self._short.values())
        rel = _lufs(sum(10 ** ((b / 10 + 0.691) / 10) * c for b, c in self._short.items()) / n) - 20
        vals = sorted((b / 10, c) for b, c in self._short.items() if b / 10 > rel)
        total = sum(c for _, c in vals)
        if total < 2:
            return None

        def pct(q: float) -> float:
            acc = 0
            for v, c in vals:
                acc += c
                if acc >= q * total:
                    return v
            return vals[-1][0]

        return round(pct(0.95) - pct(0.10), 1)

    def reset_stats(self) -> None:
        with self.lock:
            self.reset()

    def snapshot(self, now: float | None = None) -> dict | None:
        now = time.monotonic() if now is None else now
        with self.lock:
            if self.last is None or now - self.last > 2.0 or not self.ch:
                return None
            clip_on = now - self.clip_at < 2.5
            ch = []
            for i in range(self.ch):
                ch.append({"rms": self.rms[i], "peak": self.peak[i], "hold": self.hold[i], "true_peak": self.tpeak[i],
                           "dc": self.dc[i], "mute": self.peak[i] <= LEVEL_FLOOR})
            silent = self.silent_since is not None and now - self.silent_since >= 2.0
            corr = round(self.corr, 2) if self.ch >= 2 else None
            alarms = []
            if silent:
                alarms.append(["SILENCE", "warn"])
            if self.clips:
                alarms.append(["CLIP", "warn"])
            if corr is not None and corr < -0.5:
                alarms.append(["PHASE", "warn"])
            return {"rate": self.rate, "channels": ch, "clips": self.clips, "clip_now": clip_on,
                    "momentary": None if self.momentary is None else round(self.momentary, 1),
                    "short": None if self.short is None else round(self.short, 1),
                    "integrated": self.integrated(), "lra": self.lra(), "correlation": corr,
                    "spectrum": self.spectrum, "alarms": alarms}


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
