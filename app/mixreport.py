"""The stream-mix report (#149, #604): while the stream mix carries sound, one row a second of BS.1770 loudness, true
peak, left/right correlation and octave bands, saved per service, and a verdict on it afterwards.

The stream mix is a stereo pair of lanes on the audio bus (app/audiobus.py). Patch both from the same device so the
two stay in step; correlation needs that. A "service" is Relay's own session (the engine's run); with none running,
rows go to one file per day. A row is written only while the mix carries sound (above `silence_dbfs`).

The maths is not here. A loudness probe in the Rust core (native/relay-bus, `relay_bus.Bus.open_probe("loudness")`)
reads both lanes on its own thread and writes one finished row a second: momentary and short-term loudness, the
running integrated loudness (exact BS.1770 gating) and loudness range, sample and true peak, correlation and the octave
levels. Python saves those rows and judges them; it computes no loudness and does no frequency analysis.

Files. `<state folder>/mixreports/<session>.jsonl`: a header line, then one JSON row per second:
    {"t": epoch, "m": momentary LUFS, "s": short-term LUFS, "i": integrated LUFS so far, "lra": loudness range LU,
     "pk": sample peak dBFS, "tp": true peak dBTP, "co": L/R correlation,
     "oct": [10 octave levels, 31.5 Hz to 16 kHz, dBFS], "r": 1 when rehearsal}
Core: it reads Relay's own bus and talks to nothing outside.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from pathlib import Path

from . import audiobus, paths

log = logging.getLogger("relay.mixreport")

OCTAVES = (31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0)
OCTAVE_LABELS = ("31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
MAX_FILES = 100
MAX_ROWS = 86400 * 6                 # a file stops growing after six hours of sound
ID_RE = re.compile(r"[A-Za-z0-9-]{1,40}")
SERIES_POINTS = 600
BASS_HEAVY_DB, THIN_DB = 8.0, -12.0
WANDER_LU = 12.0

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


# -- judging -----------------------------------------------------------------------------------------------------
def _r(v):
    """A number from the probe, rounded to a tenth for the file; None stays None."""
    return None if v is None else round(float(v), 1)


def _energy_mean(values: list[float]) -> float | None:
    if not values:
        return None
    return 10.0 * math.log10(sum(10.0 ** (v / 10.0) for v in values) / len(values))


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
    pks = [r["tp"] if isinstance(r.get("tp"), (int, float)) else r["pk"] for r in rows
           if isinstance(r.get("tp"), (int, float)) or isinstance(r.get("pk"), (int, float))]
    cos = [r["co"] for r in rows if isinstance(r.get("co"), (int, float))]
    # The programme loudness is the meter's own running value (exact BS.1770 gating, done in Rust) at the last row.
    integ = next((r["i"] for r in reversed(rows) if isinstance(r.get("i"), (int, float))), None)
    lra = next((r["lra"] for r in reversed(rows) if isinstance(r.get("lra"), (int, float))), None)
    out.update(integrated_lufs=integ, loudness_range=lra, loudest_momentary=round(max(ms), 1) if ms else None,
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
                f"True peak reached {top} dBTP; {over} second{'s' if over != 1 else ''} above {cfg['peak_limit_dbfs']}.")
        else:
            add("Peaks", "pass", f"True peak {top} dBTP, under the {cfg['peak_limit_dbfs']} limit.")
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
    head = ["time", "momentary_lufs", "short_term_lufs", "integrated_lufs", "sample_peak_dbfs", "true_peak_dbtp",
            "lr_correlation"] + \
           [f"{lab}_hz_dbfs" for lab in OCTAVE_LABELS] + ["rehearsal"]
    lines = [",".join(head)]
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["t"])) if isinstance(r.get("t"), (int, float)) else ""
        oct_ = r.get("oct") if isinstance(r.get("oct"), list) else []
        cells = [t, r.get("m"), r.get("s"), r.get("i"), r.get("pk"), r.get("tp"), r.get("co")] + \
                [oct_[i] if i < len(oct_) else ""
                 for i in range(len(OCTAVES))] + [1 if r.get("r") else 0]
        lines.append(",".join("" if c is None else str(c) for c in cells))
    return "\n".join(lines) + "\n"


# -- the recorder ------------------------------------------------------------------------------------------------
class Recorder:
    """Keeps a loudness probe on the stream mix's two lanes and saves the rows it writes, a second at a time."""

    def __init__(self, target: audiobus.Bus | None = None, root: Path | None = None) -> None:
        self._target = target
        self._root = root
        self.config = default_config()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._probe: tuple[int, int, int] | None = None      # probe id, left lane, right lane
        self._programme: str | None = None                   # the session the probe's integrated loudness belongs to
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
                self._open_probe_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _open_probe_locked(self) -> None:
        """A probe on the configured lanes; kept as it is when the lanes did not change, so the programme's
        integrated loudness is not lost to a change of target or tolerance."""
        cfg = self.config
        if self._probe is not None and self._probe[1:] == (cfg["left"], cfg["right"]):
            self.bus.engine.set_probe_silence(self._probe[0], cfg["silence_dbfs"])
            return
        self._close_probe_locked()
        pid = self.bus.engine.open_probe("loudness", [cfg["left"], cfg["right"]], silence_dbfs=cfg["silence_dbfs"])
        self._probe = (pid, cfg["left"], cfg["right"])

    def _close_probe_locked(self) -> None:
        probe, self._probe = self._probe, None
        if probe is not None:
            try:
                self.bus.engine.close_probe(probe[0])
            except (ValueError, RuntimeError):
                pass

    def _start_locked(self) -> None:
        self._stop = threading.Event()
        self.session, self.rows, self.latest, self.error = None, 0, None, ""
        self._programme = None
        self._open_probe_locked()
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="relay-mixreport", daemon=True)
        self._thread.start()

    def _stop_locked(self) -> None:
        thread, self._thread = self._thread, None
        self._stop.set()
        if thread is not None:
            thread.join(timeout=3.0)
        self._close_probe_locked()

    def _run(self, stop: threading.Event) -> None:
        while not stop.wait(1.0):
            try:
                self.tick()
            except Exception:
                log.exception("stream-mix report tick failed")

    def tick(self, now: float | None = None) -> list[dict]:
        """Take the rows the probe wrote since the last tick and save those where the mix carried sound. Returns the
        rows saved. Public for tests."""
        now = time.time() if now is None else now
        with self._lock:
            probe = self._probe
            rehearsal = self.config["rehearsal"]
        if probe is None:
            return []
        fresh = self.bus.engine.probe_rows(probe[0])
        programme = self._session_for(now)[0]
        if self._programme is not None and programme != self._programme:
            self.bus.engine.reset_probe(probe[0])         # a new service: its integrated loudness starts from nothing
            self._programme = programme
            return []
        self._programme = programme
        saved = []
        for i, r in enumerate(fresh):
            if not r["sound"]:
                continue
            t = int(now - (len(fresh) - 1 - i))           # the newest row is this second; older ones are earlier
            row = {"t": t, "m": _r(r["momentary"]), "s": _r(r["short_term"]), "i": _r(r["integrated"]),
                   "lra": _r(r["range_lu"]), "pk": _r(r["sample_peak_dbfs"]), "tp": _r(r["true_peak_dbtp"]),
                   "co": r["correlation"], "oct": [_r(o) for o in r["octaves"]], **({"r": 1} if rehearsal else {})}
            self.latest = row
            self._save(row, now)
            saved.append(row)
        return saved

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
