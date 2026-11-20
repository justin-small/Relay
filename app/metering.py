"""Sound-level metering (#149): a calibrated SPL meter and a 1/3-octave RTA, read from one lane of the audio bus.

The tap. A lane is a mono stream at 48 kHz (app/audiobus.py), taken before the caption path's 24 kHz downsample, so
the RTA reaches 20 kHz. This module subscribes to the lane like any other consumer. The subscription runs on the
input's thread, so it only copies the block into a small queue and returns; a worker thread does the FFTs. A full
queue drops the block (counted), never waits: the captions are never delayed by analysis.

What it measures. Every hop (2048 samples, about 43 ms) it takes a Hann-windowed 4096-sample FFT for the weighted sound
level (a rectangular window leaks a low tone into bins the weighting treats differently, and reads 100 Hz almost
2 dB off) and a Hann-windowed 8192-sample FFT for the bands. The level
is A- and C-weighted, each with Fast (125 ms) and Slow (1 s) exponential time weighting, plus an A-weighted Leq over
the last minute (energy averaged, never dB averaged). The RTA is the 31 base-10 third-octave bands from 20 Hz to
20 kHz, Z-weighted, in dB SPL.

Calibration. `calibration_db` is the SPL that a digital RMS of 0 dBFS stands for. Set it by playing a calibrator
into the input and calling `calibrate(reference_db)`, or by hand against a reference meter. Until that is done the
numbers say `calibrated: false`, and `kind` says whether the lane carries a measurement mic ("room") or only the
desk's mix ("mix"): a desk feed measures the mix, not the room, and the pages label it so.

numpy only (no scipy): the weighting curves are the analytic IEC 61672 ones, applied per FFT bin.

Other tools' readings. A module that reads a measurement from another product (Open Sound Meter, a remote
analyser) registers a source with `register_source(id, label)` in attach(), calls `publish(id, reading)` as
readings arrive and `unregister_source(id)` in detach(). A published reading is levels in dB SPL
(`a_fast`, `a_slow`, `c_fast`, `c_slow`, `leq_a`), optionally the 31 `bands` and its own `warn_db`/`limit_db`.
Boards and the status feed carry them next to Relay's own; core never imports the module.

Core, not a module: it talks to nothing outside Relay.
"""
from __future__ import annotations

import collections
import logging
import math
import queue
import threading
import time

import numpy as np

from . import audiobus, channels

log = logging.getLogger("relay.metering")

RATE = audiobus.RATE
HOP = 2048                         # samples per analysis step (about 43 ms)
WINDOW = 8192                      # the RTA's FFT length: 5.9 Hz per bin
LEVEL_WINDOW = 4096                # the level's FFT length: 11.7 Hz per bin, 85 ms
FAST_S, SLOW_S = 0.125, 1.0        # IEC 61672 time weightings
BAND_S = 0.5                       # the RTA's smoothing
LEQ_S = 60.0                       # the window of the running Leq
QUEUE_BLOCKS = 64                  # blocks waiting for analysis; more are dropped, never waited for
SILENT_S = 2.0                     # no block for this long: the lane carries nothing
FLOOR_DB = 0.0                     # nothing reads below this; a silent lane shows the floor, not -inf
CAL_MIN, CAL_MAX = 60.0, 160.0
KINDS = ("room", "mix")
BAND_N = 31
BAND_CENTRES = tuple(round(1000.0 * 10.0 ** (0.1 * n), 1) for n in range(-17, 14))   # 19.9 Hz ... 19953 Hz
BAND_LABELS = ("20", "25", "31.5", "40", "50", "63", "80", "100", "125", "160", "200", "250", "315", "400", "500",
               "630", "800", "1k", "1.25k", "1.6k", "2k", "2.5k", "3.15k", "4k", "5k", "6.3k", "8k", "10k", "12.5k",
               "16k", "20k")


# -- configuration ---------------------------------------------------------------------------------------------
def default_config() -> dict:
    return {"enabled": False, "lane": 1, "kind": "room", "calibration_db": 110.0, "calibrated": False,
            "warn_db": 85.0, "limit_db": 95.0}


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(n) else round(max(lo, min(hi, n)), 1)


def normalize(raw) -> dict:
    """A clean `metering` setting from whatever was saved or posted: out-of-range values are clamped, unknown keys
    dropped. The warning level never sits above the limit."""
    raw = raw if isinstance(raw, dict) else {}
    out = default_config()
    out["enabled"] = bool(raw.get("enabled"))
    try:
        out["lane"] = max(1, min(audiobus.LANES, int(raw.get("lane", 1))))
    except (TypeError, ValueError):
        pass
    out["kind"] = raw.get("kind") if raw.get("kind") in KINDS else "room"
    out["calibration_db"] = _num(raw.get("calibration_db"), CAL_MIN, CAL_MAX, 110.0)
    out["calibrated"] = bool(raw.get("calibrated"))
    out["warn_db"] = _num(raw.get("warn_db"), 40.0, 140.0, 85.0)
    out["limit_db"] = max(out["warn_db"], _num(raw.get("limit_db"), 40.0, 140.0, 95.0))
    return out


# -- the maths ---------------------------------------------------------------------------------------------------
def a_weight_db(f: np.ndarray) -> np.ndarray:
    f2 = np.square(f)
    ra = (12194.0 ** 2 * f2 * f2) / ((f2 + 20.6 ** 2) * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
                                      * (f2 + 12194.0 ** 2))
    return 20.0 * np.log10(np.maximum(ra, 1e-30)) + 2.0


def c_weight_db(f: np.ndarray) -> np.ndarray:
    f2 = np.square(f)
    rc = (12194.0 ** 2 * f2) / ((f2 + 20.6 ** 2) * (f2 + 12194.0 ** 2))
    return 20.0 * np.log10(np.maximum(rc, 1e-30)) + 0.06


def _bin_weights(n: int) -> tuple[np.ndarray, np.ndarray]:
    """The squared A and C gain of each rfft bin of an n-sample FFT, with the DC bin silenced."""
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    f[0] = 1.0
    a, c = np.square(10.0 ** (a_weight_db(f) / 20.0)), np.square(10.0 ** (c_weight_db(f) / 20.0))
    a[0] = c[0] = 0.0
    return a, c


def _band_edges() -> list[tuple[int, int]]:
    """For each third-octave band, the first and last-plus-one rfft bin of the WINDOW FFT. A band that holds no bin
    (the lowest ones) takes the nearest."""
    res = RATE / WINDOW
    out = []
    for fc in (1000.0 * 10.0 ** (0.1 * n) for n in range(-17, 14)):
        lo, hi = fc * 10.0 ** -0.05, fc * 10.0 ** 0.05
        a, b = int(math.ceil(lo / res)), int(math.ceil(hi / res))
        if b <= a:
            a = int(round(fc / res))
            b = a + 1
        out.append((max(1, a), min(WINDOW // 2 + 1, b)))
    return out


def level_db(ms: float, calibration_db: float) -> float:
    """dB SPL of a mean square (1.0 = 0 dBFS RMS)."""
    return calibration_db + 10.0 * math.log10(ms) if ms > 1e-14 else FLOOR_DB


def weighted_ms(spectrum: np.ndarray, n: int, w2: np.ndarray, window_ms: float) -> float:
    """The weighted mean square of an n-sample windowed block (Parseval over the one-sided spectrum), `window_ms`
    being the mean square of the window itself."""
    p = np.square(np.abs(spectrum))
    p[1:-1] *= 2.0
    return float(np.dot(p, w2)) / (n * n * window_ms)


class Analyzer:
    """Feed it mono 48 kHz blocks; read the levels. No threads here: `feed` does the work in the caller's thread."""

    def __init__(self, calibration_db: float = 110.0) -> None:
        self.calibration_db = calibration_db
        self._wa, self._wc = _bin_weights(LEVEL_WINDOW)
        self._edges = _band_edges()
        self._hann = np.hanning(WINDOW).astype(np.float64)
        self._hann_ms = float(np.mean(np.square(self._hann)))
        self._hann_l = np.hanning(LEVEL_WINDOW).astype(np.float64)
        self._hann_l_ms = float(np.mean(np.square(self._hann_l)))
        self._buf = np.zeros(0, dtype=np.float64)
        self._tail = np.zeros(WINDOW, dtype=np.float64)
        self.reset()

    def reset(self) -> None:
        self._ms = {"a_fast": 0.0, "a_slow": 0.0, "c_fast": 0.0, "c_slow": 0.0}
        self._bands = np.zeros(BAND_N)
        self._leq: collections.deque = collections.deque()
        self._leq_sum = 0.0
        self.hops = 0

    def feed(self, block: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, block.astype(np.float64, copy=False)])
        while len(self._buf) >= HOP:
            hop, self._buf = self._buf[:HOP], self._buf[HOP:]
            self._step(hop)

    def _step(self, hop: np.ndarray) -> None:
        dt = HOP / RATE
        self._tail = np.concatenate([self._tail[HOP:], hop])
        spec = np.fft.rfft(self._tail[-LEVEL_WINDOW:] * self._hann_l)
        ms_a = weighted_ms(spec, LEVEL_WINDOW, self._wa, self._hann_l_ms)
        ms_c = weighted_ms(spec, LEVEL_WINDOW, self._wc, self._hann_l_ms)
        for key, ms, tau in (("a_fast", ms_a, FAST_S), ("a_slow", ms_a, SLOW_S),
                             ("c_fast", ms_c, FAST_S), ("c_slow", ms_c, SLOW_S)):
            k = 1.0 - math.exp(-dt / tau)
            self._ms[key] += (ms - self._ms[key]) * k
        self._leq.append(ms_a)
        self._leq_sum += ms_a
        while len(self._leq) * dt > LEQ_S:
            self._leq_sum -= self._leq.popleft()
        if self.hops >= WINDOW // HOP - 1:
            p = np.square(np.abs(np.fft.rfft(self._tail * self._hann)))
            p *= 2.0 / (WINDOW * WINDOW * self._hann_ms)
            p[0] = 0.0
            ms = np.array([float(p[a:b].sum()) for a, b in self._edges])
            k = 1.0 - math.exp(-dt / BAND_S)
            self._bands += (ms - self._bands) * k
        self.hops += 1

    def snapshot(self) -> dict:
        cal = self.calibration_db
        leq = (self._leq_sum / len(self._leq)) if self._leq else 0.0
        return {"a_fast": round(level_db(self._ms["a_fast"], cal), 1), "a_slow": round(level_db(self._ms["a_slow"], cal), 1),
                "c_fast": round(level_db(self._ms["c_fast"], cal), 1), "c_slow": round(level_db(self._ms["c_slow"], cal), 1),
                "leq_a": round(level_db(max(leq, 0.0), cal), 1),
                "bands": [round(level_db(float(m), cal), 1) for m in self._bands]}


# -- the service -------------------------------------------------------------------------------------------------
# What the calibrate route may say, by reason. The route answers from this table, never from the exception's text.
CALIBRATE_SENTENCES = {
    "reference": "Enter the level the calibrator says, in dB, between 30 and 140.",
    "off": "Switch the meter on first.",
    "no_signal": "No sound is arriving on that lane. Patch the measurement mic and play the calibrator.",
    "too_quiet": "The calibrator is too quiet to read. Raise the mic gain or move it closer.",
}


class CalibrateError(ValueError):
    """A refusal; `reason` is a key of CALIBRATE_SENTENCES."""

    def __init__(self, reason: str) -> None:
        super().__init__(CALIBRATE_SENTENCES[reason])
        self.reason = reason


class Metering:
    """Taps one lane on the bus and keeps the Analyzer current from a worker thread."""

    def __init__(self, target: audiobus.Bus | None = None) -> None:
        self._target = target
        self.config = default_config()
        self.analyzer = Analyzer()
        self._lane = None
        self._token = 0
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.dropped = 0
        self.last_block = 0.0

    @property
    def bus(self) -> audiobus.Bus:
        return self._target or audiobus.bus

    @property
    def running(self) -> bool:
        return self._thread is not None

    def apply(self, cfg: dict) -> None:
        """Start, retarget or stop to match the saved setting. A change of lane or calibration keeps what it can."""
        cfg = normalize(cfg)
        with self._lock:
            old, self.config = self.config, cfg
            self.analyzer.calibration_db = cfg["calibration_db"]
            if not cfg["enabled"]:
                self._stop_locked()
                return
            if self._thread is None or old["lane"] != cfg["lane"] or not old["enabled"]:
                self._stop_locked()
                self._start_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _start_locked(self) -> None:
        self.analyzer.reset()
        self.dropped = 0
        self.last_block = 0.0
        self._stop = threading.Event()
        self._q = queue.Queue(maxsize=QUEUE_BLOCKS)
        self._lane = self.bus.lane(self.config["lane"])
        self._token = self._lane.subscribe(self._on_block)
        self._thread = threading.Thread(target=self._run, args=(self._stop, self._q), name="relay-metering",
                                        daemon=True)
        self._thread.start()

    def _stop_locked(self) -> None:
        lane, self._lane = self._lane, None
        if lane is not None:
            lane.unsubscribe(self._token)
        thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None:
            thread.join(timeout=2.0)

    def _on_block(self, block: np.ndarray) -> None:
        """On the input's thread: copy and return. Never waits, never analyses."""
        self.last_block = time.time()
        try:
            self._q.put_nowait(np.array(block, dtype=np.float32, copy=True))
        except queue.Full:
            self.dropped += 1

    def _run(self, stop: threading.Event, q: queue.Queue) -> None:
        while not stop.is_set():
            try:
                block = q.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self.analyzer.feed(block)
            except Exception:
                log.exception("metering analysis failed")

    def calibrate(self, reference_db: float) -> float:
        """Set the offset so the current Slow reading (A-weighted: a 1 kHz calibrator reads the same either way) is
        `reference_db`. Needs a signal; raises ValueError with a sentence fit for the page when there is none."""
        ref = _num(reference_db, 30.0, 140.0, math.nan)
        if math.isnan(ref):
            raise CalibrateError("reference")
        with self._lock:
            if self._thread is None:
                raise CalibrateError("off")
            if time.time() - self.last_block > SILENT_S:
                raise CalibrateError("no_signal")
            ms = self.analyzer._ms["a_slow"]
            if ms <= 1e-12:
                raise CalibrateError("too_quiet")
            cal = max(CAL_MIN, min(CAL_MAX, ref - 10.0 * math.log10(ms)))
            self.analyzer.calibration_db = round(cal, 1)
            self.config = {**self.config, "calibration_db": round(cal, 1), "calibrated": True}
            return self.config["calibration_db"]

    def snapshot(self) -> dict:
        cfg = self.config
        out = {"enabled": cfg["enabled"], "lane": cfg["lane"], "kind": cfg["kind"], "calibrated": cfg["calibrated"],
               "calibration_db": cfg["calibration_db"], "warn_db": cfg["warn_db"], "limit_db": cfg["limit_db"],
               "dropped": self.dropped, "labels": list(BAND_LABELS)}
        if not cfg["enabled"] or not self.running:
            return {**out, "state": "off"}
        state = "ok" if time.time() - self.last_block <= SILENT_S else "no_input"
        return {**out, "state": state, **self.analyzer.snapshot()}

    def public(self) -> dict | None:
        """What the status feed carries (the viewer port, no login): the levels and the labels that keep the
        number honest, nothing about devices. None when the meter is off."""
        snap = self.snapshot()
        ext = _external_public()
        if snap["state"] == "off" and not ext:
            return None
        keep = ("state", "kind", "calibrated", "warn_db", "limit_db", "a_fast", "a_slow", "c_fast", "c_slow", "leq_a",
                "bands")
        out = {k: snap[k] for k in keep if k in snap}
        return {**out, **({"sources": ext} if ext else {})}


# -- other tools' readings --------------------------------------------------------------------------------------
EXTERNAL_STALE_S = 5.0             # a source that has said nothing for this long reads as "no signal"
MAX_SOURCES = 8
_LEVELS = ("a_fast", "a_slow", "c_fast", "c_slow", "leq_a")
_sources: dict[str, dict] = {}
_ext_lock = threading.Lock()


def _db(v) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(max(-20.0, min(200.0, n)), 1) if not math.isnan(n) and not math.isinf(n) else None


def _report_sources() -> None:
    """The registered sound-level sources are audio channels in core (#542): a source is a channel you can meter."""
    found = [{"key": k, "kind": "audio", "direction": "source", "gear_name": label} for k, label in sources()]
    channels.report("metering", found)


def register_source(source_id: str, label: str) -> None:
    with _ext_lock:
        if source_id in _sources or len(_sources) < MAX_SOURCES:
            _sources[source_id] = {"label": str(label)[:40], "reading": None, "ts": 0.0}
    _report_sources()


def unregister_source(source_id: str) -> None:
    with _ext_lock:
        _sources.pop(source_id, None)
    _report_sources()


def sources() -> list[tuple[str, str]]:
    """(id, label) of every registered source, for the board widgets' choice of where to read."""
    with _ext_lock:
        return [(k, v["label"]) for k, v in _sources.items()]


def publish(source_id: str, reading: dict) -> None:
    """A registered source's newest reading. Unknown ids, junk numbers and a `bands` list that is not 31 numbers are
    dropped, so a module can pass what it parsed."""
    if not isinstance(reading, dict):
        return
    clean: dict = {}
    for k in _LEVELS:
        v = _db(reading.get(k))
        if v is not None:
            clean[k] = v
    bands = reading.get("bands")
    if isinstance(bands, (list, tuple)) and len(bands) == BAND_N:
        vals = [_db(b) for b in bands]
        if all(v is not None for v in vals):
            clean["bands"] = vals
    for k in ("warn_db", "limit_db"):
        v = _db(reading.get(k))
        if v is not None:
            clean[k] = v
    with _ext_lock:
        entry = _sources.get(source_id)
        if entry is not None and clean:
            entry["reading"], entry["ts"] = clean, time.time()


def _external_public() -> dict:
    now = time.time()
    out = {}
    with _ext_lock:
        for k, v in _sources.items():
            r = v["reading"]
            state = "ok" if r and now - v["ts"] <= EXTERNAL_STALE_S else "no_input"
            out[k] = {"label": v["label"], "state": state, "calibrated": True, "kind": "room", **(r or {})}
    return out


meter = Metering()
