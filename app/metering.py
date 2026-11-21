"""Sound-level metering (#149, #604): calibrated SPL meters and 1/3-octave analysers, read from lanes of the audio bus.

The maths is not here. It runs in the Rust core (native/relay-bus, `relay_bus.Bus.open_probe`): a probe taps a lane,
and a Rust thread does the weighting, the time constants, the Leq and the 31 bands on every sample, so a meter never
loses audio while Python is busy. This module keeps the settings, opens and closes the probes to match them, and turns
a probe's finished numbers into the reading the pages and the status feed carry.

Meters. The operator adds as many as they like (`meters` in the setting), each on its own lane, and a board's sound
widget picks which one it reads. A meter measures one lane: `kind` says whether the lane carries a measurement mic
("room") or only the desk's mix ("mix"), and the pages label it so. `calibration_db` is the sound level that a digital
RMS of 0 dBFS stands for: set it by playing a calibrator into the input and calling `calibrate(id, reference_db)`, or by
hand against a reference meter. Until then a reading says `calibrated: false`.

Other tools' readings. A module that reads a measurement from another product (Open Sound Meter, a remote
analyser) registers a source with `register_source(id, label)` in attach(), calls `publish(id, reading)` as
readings arrive and `unregister_source(id)` in detach(). A published reading is levels in dB SPL
(`a_fast`, `a_slow`, `c_fast`, `c_slow`, `leq_a`), optionally the 31 `bands` and its own `warn_db`/`limit_db`.
Boards and the status feed carry them next to Relay's own; core never imports the module.

Core, not a module: it talks to nothing outside Relay.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time

from . import audiobus, channels

log = logging.getLogger("relay.metering")

SILENT_S = 2.0                     # no audio for this long: the lane carries nothing
CAL_MIN, CAL_MAX = 60.0, 160.0
KINDS = ("room", "mix")
MAX_METERS = 8
NAME_MAX = 40
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
BAND_N = 31
BAND_LABELS = ("20", "25", "31.5", "40", "50", "63", "80", "100", "125", "160", "200", "250", "315", "400", "500",
               "630", "800", "1k", "1.25k", "1.6k", "2k", "2.5k", "3.15k", "4k", "5k", "6.3k", "8k", "10k", "12.5k",
               "16k", "20k")
LEVELS = ("a_fast", "a_slow", "c_fast", "c_slow", "leq_a")


# -- configuration ---------------------------------------------------------------------------------------------
def default_meter(n: int = 1) -> dict:
    return {"id": f"m{n}", "name": "Room" if n == 1 else f"Meter {n}", "enabled": True, "lane": 1, "kind": "room",
            "calibration_db": 110.0, "calibrated": False, "warn_db": 85.0, "limit_db": 95.0}


def default_config() -> dict:
    return {"meters": []}


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(n) else round(max(lo, min(hi, n)), 1)


def _meter(raw, taken: set[str], n: int) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = default_meter(n)
    mid = str(raw.get("id") or "").lower()
    if not ID_RE.fullmatch(mid) or mid in taken:
        mid = next(f"m{i}" for i in range(n, n + MAX_METERS + 2) if f"m{i}" not in taken)
    out["id"] = mid
    name = " ".join(str(raw.get("name") or "").split())[:NAME_MAX]
    out["name"] = name or out["name"]
    out["enabled"] = bool(raw.get("enabled", True))
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


def normalize(raw) -> dict:
    """A clean `metering` setting from whatever was saved or posted: out-of-range values are clamped, unknown keys
    dropped, ids made unique. The warning level never sits above the limit."""
    raw = raw if isinstance(raw, dict) else {}
    meters, taken = [], set()
    listed = raw.get("meters")
    for i, m in enumerate(listed[:MAX_METERS] if isinstance(listed, list) else [], start=1):
        meter = _meter(m, taken, i)
        taken.add(meter["id"])
        meters.append(meter)
    return {"meters": meters}


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
    """Keeps one Rust probe per enabled meter and reads their numbers."""

    def __init__(self, target: audiobus.Bus | None = None) -> None:
        self._target = target
        self.config = default_config()
        self._probes: dict[str, tuple[int, int]] = {}          # meter id -> (probe id, lane it was opened on)
        self._lock = threading.RLock()

    @property
    def bus(self) -> audiobus.Bus:
        return self._target or audiobus.bus

    @property
    def running(self) -> bool:
        return bool(self._probes)

    def apply(self, cfg: dict) -> None:
        """Open, retarget or close probes to match the saved setting. A meter whose lane did not change keeps its
        probe, and so its averages."""
        cfg = normalize(cfg)
        with self._lock:
            self.config = cfg
            want = {m["id"]: m for m in cfg["meters"] if m["enabled"]}
            for mid in [k for k, (_, lane) in self._probes.items() if k not in want or want[k]["lane"] != lane]:
                self._close(mid)
            for mid, m in want.items():
                if mid in self._probes:
                    self.bus.engine.set_probe_calibration(self._probes[mid][0], m["calibration_db"])
                    continue
                try:
                    pid = self.bus.engine.open_probe("spl", [m["lane"]], calibration_db=m["calibration_db"])
                except (ValueError, RuntimeError):
                    log.exception("Could not start meter %s", mid)
                    continue
                self._probes[mid] = (pid, m["lane"])
            _report_sources(self)

    def stop(self) -> None:
        with self._lock:
            for mid in list(self._probes):
                self._close(mid)

    def _close(self, mid: str) -> None:
        pid, _ = self._probes.pop(mid)
        try:
            self.bus.engine.close_probe(pid)
        except (ValueError, RuntimeError):
            pass

    def _meter_cfg(self, mid: str) -> dict | None:
        return next((m for m in self.config["meters"] if m["id"] == mid), None)

    def calibrate(self, mid: str, reference_db: float) -> float:
        """Set the offset so the meter's current Slow reading (A-weighted: a 1 kHz calibrator reads the same either
        way) is `reference_db`. Needs a signal; raises CalibrateError, whose sentence is fit for the page."""
        ref = _num(reference_db, 30.0, 140.0, math.nan)
        if math.isnan(ref):
            raise CalibrateError("reference")
        with self._lock:
            got = self._probes.get(mid)
            cfg = self._meter_cfg(mid)
            if got is None or cfg is None:
                raise CalibrateError("off")
            st = self.bus.engine.probe_status(got[0])
            if st["idle_s"] is None or st["idle_s"] > SILENT_S:
                raise CalibrateError("no_signal")
            try:
                cal = self.bus.engine.calibrate_probe(got[0], ref)
            except ValueError as exc:
                raise CalibrateError("reference" if str(exc) == "reference" else "too_quiet") from None
            cal = round(max(CAL_MIN, min(CAL_MAX, cal)), 1)
            cfg.update(calibration_db=cal, calibrated=True)
            return cal

    def snapshot(self, mid: str) -> dict | None:
        """The reading of one meter, with its setting; None for a meter that is not in the setting."""
        cfg = self._meter_cfg(mid)
        if cfg is None:
            return None
        out = {"id": cfg["id"], "name": cfg["name"], "enabled": cfg["enabled"], "lane": cfg["lane"], "kind": cfg["kind"],
               "calibrated": cfg["calibrated"], "calibration_db": cfg["calibration_db"], "warn_db": cfg["warn_db"],
               "limit_db": cfg["limit_db"], "labels": list(BAND_LABELS)}
        got = self._probes.get(mid)
        if not cfg["enabled"] or got is None:
            return {**out, "state": "off", "dropped": 0}
        try:
            st = self.bus.engine.probe_status(got[0])
        except (ValueError, RuntimeError):
            return {**out, "state": "off", "dropped": 0}
        idle = st["idle_s"]
        state = "ok" if idle is not None and idle <= SILENT_S else "no_input"
        return {**out, "state": state, "dropped": st["dropped"],
                **{k: round(st[k], 1) for k in LEVELS}, "bands": [round(b, 1) for b in st["bands"]]}

    def snapshots(self) -> list[dict]:
        with self._lock:
            return [s for s in (self.snapshot(m["id"]) for m in self.config["meters"]) if s is not None]

    def public(self) -> dict | None:
        """What the status feed carries (the viewer port, no login): the levels and the labels that keep the number
        honest, nothing about devices. The first meter that is on is the default reading; every meter, and every other
        tool's source, is also under `sources` by id, which is what a widget told to read from one picks. None when
        nothing is measuring."""
        keep = ("state", "kind", "calibrated", "warn_db", "limit_db", *LEVELS, "bands")
        with self._lock:
            mine = [s for s in self.snapshots() if s["state"] != "off"]
        ext = _external_public()
        if not mine and not ext:
            return None
        sources = {s["id"]: {"label": s["name"], **{k: s[k] for k in keep if k in s}} for s in mine}
        sources.update(ext)
        head = {k: mine[0][k] for k in keep if k in mine[0]} if mine else {"state": "off"}
        return {**head, **({"sources": sources} if sources else {})}


# -- other tools' readings --------------------------------------------------------------------------------------
EXTERNAL_STALE_S = 5.0             # a source that has said nothing for this long reads as "no signal"
MAX_SOURCES = 8
_sources: dict[str, dict] = {}
_ext_lock = threading.Lock()


def _db(v) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(max(-20.0, min(200.0, n)), 1) if not math.isnan(n) and not math.isinf(n) else None


def _report_sources(owner: "Metering | None" = None) -> None:
    """The sound-level sources are audio channels in core (#542): a source is a channel you can meter. Relay's own
    meters and the registered tools' sources both."""
    own = [(m["id"], m["name"]) for m in (owner or meter).config["meters"] if m["enabled"]]
    found = [{"key": k, "kind": "audio", "direction": "source", "gear_name": label} for k, label in own + sources()]
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
    """(id, label) of every registered source from other tools."""
    with _ext_lock:
        return [(k, v["label"]) for k, v in _sources.items()]


def choices() -> list[tuple[str, str]]:
    """(id, label) of everything a sound widget can read: Relay's own meters, then each tool's source."""
    return [(m["id"], m["name"]) for m in meter.config["meters"] if m["enabled"]] + sources()


def publish(source_id: str, reading: dict) -> None:
    """A registered source's newest reading. Unknown ids, junk numbers and a `bands` list that is not 31 numbers are
    dropped, so a module can pass what it parsed."""
    if not isinstance(reading, dict):
        return
    clean: dict = {}
    for k in LEVELS:
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
