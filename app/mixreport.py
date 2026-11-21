"""The stream-mix report (#149): while the stream mix carries sound, one row a second of BS.1770 loudness, sample
peak, left/right correlation and octave bands, saved per service, and a verdict on it afterwards.

The stream mix is a stereo pair of lanes on the audio bus (app/audiobus.py). Patch both from the same device so the
two stay in step; correlation needs that. A "service" is Relay's own session (the engine's run); with none running,
rows go to one file per day. A row is written only while the mix carries sound (above `silence_dbfs`).

Loudness. K-weighted (the BS.1770 shelf and high-pass, evaluated per FFT bin from their published 48 kHz
coefficients) mean square of the last 400 ms is the momentary loudness, the last 3 s the short-term. Integrated
loudness is judged afterwards from the saved momentary values with BS.1770's two gates (absolute -70 LUFS, then 10 LU
under the ungated level). It is built from one momentary value a second, not the standard's 75 % overlapped blocks,
so it can differ from a hardware meter by a few tenths of a LU. Peak is the sample peak, not the true peak.

Files. `<state folder>/mixreports/<session>.jsonl`: a header line, then one JSON row per second:
    {"t": epoch, "m": momentary LUFS, "s": short-term LUFS, "pk": peak dBFS, "co": L/R correlation,
     "oct": [10 octave levels, 31.5 Hz to 16 kHz, dBFS], "r": 1 when rehearsal}
numpy only. Core: it reads Relay's own bus and talks to nothing outside.
"""
from __future__ import annotations

import collections
import json
import logging
import math
import re
import threading
import time
from pathlib import Path

import numpy as np

from . import audiobus, paths

log = logging.getLogger("relay.mixreport")

RATE = audiobus.RATE
FRAME, HOP = 4096, 2048
MOMENTARY_S, SHORT_S = 0.4, 3
ABS_GATE, REL_GATE = -70.0, 10.0
OCTAVES = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
OCTAVE_LABELS = ("31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
MAX_FILES = 100
MAX_ROWS = 86400 * 6                 # a file stops growing after six hours of sound
ID_RE = re.compile(r"[A-Za-z0-9-]{1,40}")
SERIES_POINTS = 600
BASS_HEAVY_DB, THIN_DB = 8.0, -12.0
WANDER_LU = 12.0

_K1_B, _K1_A = (1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585)
_K2_B, _K2_A = (1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621)


def default_config() -> dict:
    return {"enabled": False, "left": 1, "right": 2, "silence_dbfs": -50.0, "target_lufs": -16.0,
            "tolerance_lu": 2.0, "peak_limit_dbfs": -1.0, "rehearsal": False}


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return default if n != n else round(max(lo, min(hi, n)), 1)


def normalize(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = default_config()
    out["enabled"] = bool(raw.get("enabled"))
    out["rehearsal"] = bool(raw.get("rehearsal"))
    for k, d in (("left", 1), ("right", 2)):
        try:
            out[k] = max(1, min(audiobus.LANES, int(raw.get(k, d))))
        except (TypeError, ValueError):
            out[k] = d
    out["silence_dbfs"] = _num(raw.get("silence_dbfs"), -90.0, -10.0, -50.0)
    out["target_lufs"] = _num(raw.get("target_lufs"), -40.0, -5.0, -16.0)
    out["tolerance_lu"] = _num(raw.get("tolerance_lu"), 0.5, 10.0, 2.0)
    out["peak_limit_dbfs"] = _num(raw.get("peak_limit_dbfs"), -20.0, 0.0, -1.0)
    return out


# -- the maths ---------------------------------------------------------------------------------------------------
def _biquad_power(b, a, f: np.ndarray) -> np.ndarray:
    z = np.exp(-2j * np.pi * f / RATE)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    return np.square(np.abs(num / den))


def k_weights(n: int = FRAME) -> np.ndarray:
    """The squared K-weighting gain of each rfft bin of an n-sample FFT, DC silenced."""
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    w = _biquad_power(_K1_B, _K1_A, f) * _biquad_power(_K2_B, _K2_A, f)
    w[0] = 0.0
    return w


_HANN = np.hanning(FRAME)
_HANN_MS = float(np.mean(np.square(_HANN)))
_KW = k_weights()
_OCT_BINS = [(max(1, int(math.ceil(fc / math.sqrt(2) / (RATE / FRAME)))),
              max(2, int(math.ceil(fc * math.sqrt(2) / (RATE / FRAME))))) for fc in OCTAVES]


def _power(x: np.ndarray) -> np.ndarray | None:
    """Mean one-sided power spectrum of Hann frames of x, scaled so sums over bins are mean squares."""
    if len(x) < FRAME:
        return None
    frames = np.lib.stride_tricks.sliding_window_view(x.astype(np.float64, copy=False), FRAME)[::HOP]
    p = np.square(np.abs(np.fft.rfft(frames * _HANN, axis=1)))
    p[:, 1:-1] *= 2.0
    return p.mean(axis=0) / (FRAME * FRAME * _HANN_MS)


def k_ms(x: np.ndarray) -> float:
    p = _power(x)
    return float(np.dot(p, _KW)) if p is not None else 0.0


def lufs(ms: float) -> float | None:
    return round(-0.691 + 10.0 * math.log10(ms), 1) if ms > 1e-12 else None


def dbfs(v: float) -> float:
    return round(20.0 * math.log10(v), 1) if v > 1e-9 else -120.0


def analyse_second(left: np.ndarray, right: np.ndarray, silence_dbfs: float,
                   recent: collections.deque) -> dict | None:
    """One row for about a second of stereo samples, or None when the mix is silent. `recent` carries the last
    SHORT_S seconds' K-weighted mean squares between calls."""
    n = min(len(left), len(right))
    if n < FRAME:
        return None
    left, right = left[-n:].astype(np.float64), right[-n:].astype(np.float64)
    mono = 0.5 * (left + right)
    if dbfs(float(np.sqrt(np.mean(np.square(mono))))) <= silence_dbfs and \
            dbfs(float(max(np.sqrt(np.mean(np.square(left))), np.sqrt(np.mean(np.square(right)))))) <= silence_dbfs:
        recent.clear()
        return None
    last = int(MOMENTARY_S * RATE)
    m = lufs(k_ms(left[-last:]) + k_ms(right[-last:]))
    recent.append(k_ms(left) + k_ms(right))
    while len(recent) > SHORT_S:
        recent.popleft()
    s = lufs(sum(recent) / len(recent))
    sl, sr = left - left.mean(), right - right.mean()
    den = math.sqrt(float(np.dot(sl, sl)) * float(np.dot(sr, sr)))
    co = round(float(np.dot(sl, sr)) / den, 2) if den > 1e-12 else None
    p = _power(mono)
    octs = [dbfs(math.sqrt(float(p[a:b].sum()))) for a, b in _OCT_BINS] if p is not None else [-120.0] * 10
    return {"m": m, "s": s, "pk": dbfs(float(max(np.abs(left).max(), np.abs(right).max()))), "co": co, "oct": octs}


# -- judging -----------------------------------------------------------------------------------------------------
def _energy_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in values) / len(values))


def integrated(momentary: list[float]) -> float | None:
    """BS.1770 gated loudness of momentary values: absolute gate, then 10 LU under what remains."""
    above = [m for m in momentary if m > ABS_GATE]
    if not above:
        return None
    rel = _energy_mean(above) - REL_GATE
    kept = [m for m in above if m > rel]
    out = _energy_mean(kept)
    return round(out, 1) if out is not None else None


def judge(rows: list[dict], cfg: dict) -> dict:
    """The numbers and verdicts for a list of rows. Every verdict is {check, status: pass|warn|fail, text}."""
    cfg = normalize(cfg)
    out: dict = {"seconds": len(rows), "verdicts": [], "overall": "pass", "oct_labels": list(OCTAVE_LABELS)}
    if not rows:
        out["overall"] = "warn"
        out["verdicts"].append({"check": "Sound", "status": "warn", "text": "No sound was recorded."})
        return out
    ms = [r["m"] for r in rows if isinstance(r.get("m"), (int, float))]
    ss = [r["s"] for r in rows if isinstance(r.get("s"), (int, float))]
    pks = [r["pk"] for r in rows if isinstance(r.get("pk"), (int, float))]
    cos = [r["co"] for r in rows if isinstance(r.get("co"), (int, float))]
    integ = integrated(ms)
    out.update(integrated_lufs=integ, loudest_momentary=round(max(ms), 1) if ms else None,
               max_short_term=round(max(ss), 1) if ss else None, peak_dbfs=round(max(pks), 1) if pks else None,
               correlation=round(sum(cos) / len(cos), 2) if cos else None)
    octs = [r["oct"] for r in rows if isinstance(r.get("oct"), list) and len(r["oct"]) == len(OCTAVES)]
    avg = [round(_energy_mean([o[i] for o in octs]), 1) for i in range(len(OCTAVES))] if octs else []
    out["octaves"] = avg
    v = out["verdicts"]

    def add(check, status, text):
        v.append({"check": check, "status": status, "text": text})

    t, tol = cfg["target_lufs"], cfg["tolerance_lu"]
    if integ is None:
        add("Loudness", "warn", "Too quiet to measure the programme loudness.")
    else:
        off = integ - t
        word = "louder" if off > 0 else "quieter"
        if abs(off) <= tol:
            add("Loudness", "pass", f"{integ} LUFS integrated, within {tol} LU of the {t} target.")
        else:
            add("Loudness", "warn" if abs(off) <= 2 * tol else "fail",
                f"{integ} LUFS integrated: {abs(off):.1f} LU {word} than the {t} target.")
    if pks:
        over = sum(1 for p in pks if p > cfg["peak_limit_dbfs"])
        top = max(pks)
        if over:
            add("Peaks", "fail" if top >= 0.0 else "warn",
                f"Sample peak reached {top} dBFS; {over} second{'s' if over != 1 else ''} above {cfg['peak_limit_dbfs']}.")
        else:
            add("Peaks", "pass", f"Sample peak {top} dBFS, under the {cfg['peak_limit_dbfs']} limit.")
    if ss and len(ss) >= 10:
        spread = max(ss) - min(ss)
        add("Consistency", "warn" if spread > WANDER_LU else "pass",
            f"Short-term loudness ranged {spread:.1f} LU" + (": the level wandered." if spread > WANDER_LU else "."))
    if cos:
        neg = 100.0 * sum(1 for c in cos if c < 0) / len(cos)
        mean = sum(cos) / len(cos)
        if neg > 25:
            add("Phase", "fail", f"{neg:.0f}% of the time left and right were out of phase: the mix may vanish in mono.")
        elif neg > 10:
            add("Phase", "warn", f"{neg:.0f}% of the time left and right were out of phase.")
        elif mean > 0.98:
            add("Phase", "pass", "The mix is effectively mono (left and right match).")
        else:
            add("Phase", "pass", f"Left and right agree (correlation {mean:.2f}).")
    if len(avg) == len(OCTAVES):
        low, mid = _energy_mean(avg[0:3]), _energy_mean(avg[3:7])
        tilt = low - mid
        if tilt > BASS_HEAVY_DB:
            add("Balance", "warn", f"Bass heavy: the low octaves sit {tilt:.0f} dB above the mids.")
        elif tilt < THIN_DB:
            add("Balance", "warn", f"Thin: the low octaves sit {-tilt:.0f} dB under the mids.")
        else:
            add("Balance", "pass", "Low end and mids are in proportion.")
    rank = {"pass": 0, "warn": 1, "fail": 2}
    out["overall"] = max((x["status"] for x in v), key=lambda s: rank[s], default="pass")
    return out


def series(rows: list[dict]) -> list[dict]:
    """The rows thinned to at most SERIES_POINTS for a graph: each point is the loudest in its stretch."""
    if len(rows) <= SERIES_POINTS:
        return [{"t": r.get("t"), "m": r.get("m"), "s": r.get("s"), "pk": r.get("pk")} for r in rows]
    step = len(rows) / SERIES_POINTS
    out = []
    for i in range(SERIES_POINTS):
        chunk = rows[int(i * step):int((i + 1) * step)] or rows[-1:]
        top = max(chunk, key=lambda r: r["m"] if isinstance(r.get("m"), (int, float)) else -999)
        out.append({"t": top.get("t"), "m": top.get("m"), "s": top.get("s"), "pk": top.get("pk")})
    return out


# -- files -------------------------------------------------------------------------------------------------------
def folder() -> Path:
    from . import config

    return config.STATE_DIR / "mixreports"


def list_sessions(root: Path | None = None) -> list[dict]:
    root = root or folder()
    out = []
    if not root.is_dir():
        return out
    for p in sorted(root.glob("*.jsonl"), reverse=True)[:MAX_FILES]:
        if not ID_RE.fullmatch(p.stem):
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                head = json.loads(f.readline() or "{}")
                seconds = sum(1 for _ in f)
        except (OSError, ValueError):
            continue
        out.append({"id": p.stem, "started": head.get("started"), "label": head.get("label", ""),
                    "seconds": seconds})
    return out


def read_session(session_id: str, root: Path | None = None) -> tuple[dict, list[dict]] | None:
    """(header, rows) of a saved session, or None when there is no such one. Bad lines are skipped."""
    if not ID_RE.fullmatch(session_id or ""):
        return None
    p = (root or folder()) / f"{session_id}.jsonl"
    try:
        with p.open("r", encoding="utf-8") as f:
            head = json.loads(f.readline() or "{}")
            rows = []
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
                if len(rows) >= MAX_ROWS:
                    break
    except (OSError, ValueError):
        return None
    return head if isinstance(head, dict) else {}, rows


def csv_text(rows: list[dict]) -> str:
    head = ["time", "momentary_lufs", "short_term_lufs", "sample_peak_dbfs", "lr_correlation"] + \
           [f"{lab}_hz_dbfs" for lab in OCTAVE_LABELS] + ["rehearsal"]
    lines = [",".join(head)]
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])) if isinstance(r.get("t"), (int, float)) else ""
        oct_ = r.get("oct") if isinstance(r.get("oct"), list) else []
        cells = [t, r.get("m"), r.get("s"), r.get("pk"), r.get("co")] + [oct_[i] if i < len(oct_) else ""
                                                                         for i in range(len(OCTAVES))] + \
                [1 if r.get("r") else 0]
        lines.append(",".join("" if c is None else str(c) for c in cells))
    return "\n".join(lines) + "\n"


# -- the recorder ------------------------------------------------------------------------------------------------
def lane_tail(lane: audiobus.Lane, n: int) -> tuple[np.ndarray, int]:
    """The newest n samples of a lane and its running total. The first call starts keeping the lane (audiobus.Lane.tail)."""
    return lane.tail(n)


class Recorder:
    def __init__(self, target: audiobus.Bus | None = None, root: Path | None = None) -> None:
        self._target = target
        self._root = root
        self.config = default_config()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._recent: collections.deque = collections.deque()
        self._last = (0, 0)
        self.session: str | None = None
        self.rows = 0
        self.latest: dict | None = None
        self.error = ""

    @property
    def bus(self) -> audiobus.Bus:
        return self._target or audiobus.bus

    @property
    def running(self) -> bool:
        return self._thread is not None

    def apply(self, cfg: dict) -> None:
        cfg = normalize(cfg)
        with self._lock:
            self.config = cfg
            if not cfg["enabled"]:
                self._stop_locked()
            elif self._thread is None:
                self._start_locked()
            else:
                self._last = (0, 0)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _start_locked(self) -> None:
        self._stop = threading.Event()
        self._recent.clear()
        self._last = (0, 0)
        self.session, self.rows, self.latest, self.error = None, 0, None, ""
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="relay-mixreport", daemon=True)
        self._thread.start()

    def _stop_locked(self) -> None:
        thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None:
            thread.join(timeout=3.0)

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                log.exception("stream-mix report tick failed")

    def tick(self, now: float | None = None) -> dict | None:
        """Read the newest second from both lanes and save a row when the mix carries sound. Public for tests."""
        cfg = self.config
        now = time.time() if now is None else now
        lo, ro = self.bus.lane(cfg["left"]), self.bus.lane(cfg["right"])
        wl, wr = lo.written, ro.written
        n = min(wl - self._last[0], wr - self._last[1]) if self._last != (0, 0) else RATE
        self._last = (wl, wr)
        if n < FRAME:
            return None
        left, _ = lane_tail(lo, n)
        right, _ = lane_tail(ro, n)
        row = analyse_second(left, right, cfg["silence_dbfs"], self._recent)
        if row is None:
            return None
        row = {"t": int(now), **row, **({"r": 1} if cfg["rehearsal"] else {})}
        self.latest = row
        self._save(row, now)
        return row

    def _session_for(self, now: float) -> tuple[str, float, str]:
        from .engine import engine

        if engine.running and engine.started_at:
            t = float(engine.started_at)
            return time.strftime("%Y%m%d-%H%M%S", time.localtime(t)), t, "Captions session"
        t = time.mktime(time.strptime(time.strftime("%Y%m%d", time.localtime(now)), "%Y%m%d"))
        return "day-" + time.strftime("%Y%m%d", time.localtime(now)), t, "No captions session running"

    def _save(self, row: dict, now: float) -> None:
        sid, started, label = self._session_for(now)
        root = self._root or folder()
        try:
            paths.make_private_dir(root)
            p = root / f"{sid}.jsonl"
            fresh = not p.exists()
            if sid != self.session:
                self.session, self.rows = sid, 0 if fresh else sum(1 for _ in p.open("rb")) - 1
            if self.rows >= MAX_ROWS:
                return
            with p.open("a", encoding="utf-8") as f:
                if fresh:
                    f.write(json.dumps({"v": 1, "id": sid, "started": started, "label": label}) + "\n")
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
            self.rows += 1
            self.error = ""
            if fresh:
                self._prune(root)
        except OSError:
            self.error = "Could not save the report. Check the disk has room."

    @staticmethod
    def _prune(root: Path) -> None:
        files = sorted(root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[MAX_FILES:]:
            try:
                p.unlink()
            except OSError:
                pass

    def status(self) -> dict:
        return {"running": self.running, "session": self.session, "rows": self.rows, "latest": self.latest,
                "error": self.error, "labels": list(OCTAVE_LABELS)}


recorder = Recorder()
