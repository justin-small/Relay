"""The 32-lane audio bus: many inputs in, a crosspoint matrix, many outputs out (#92).

Inputs. Any device of any registered AudioSource (PortAudio, or a module's network stream) can be patched to
any of the 32 lanes. A device is opened once however many lanes use it. Each lane is a mono float32 stream at
LANE_RATE (48 kHz), metered on its own, and any number of consumers subscribe to it: the engine (through the
"lane" audio source below), a recorder, a module, or an output.

Matrix. An output channel is the sum of the lanes routed to it, each with a gain. Devices run on their own
clocks, so an output reads each lane through a small resampler whose ratio is nudged to hold a steady fill
(a fraction of a percent, inaudible), instead of clicking when two clocks drift apart.

Outputs. A multichannel sink (a PortAudio device, or a module's network sender) of up to 32 channels. Relay's
own sounds (alert tones, spoken translation) can be played into an output through the "bus" AudioOutput.

Off until configured: with `audio_bus.enabled` false nothing here opens a device, and the main capture
(audio.AudioCapture) is untouched either way.

Core, not a module: every module that brings audio in or out registers with it through app/audio.py, and core
never imports a network client for it.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import soxr

from . import audio, channels, config, db, routing
from .audio import LANE_RATE as RATE, MAX_LANES as LANES

log = logging.getLogger("relay.audiobus")

RING_S = 2.0                      # how much of each lane is kept for the outputs to read
DEFAULT_LATENCY_MS = 60           # an output's total buffering; passthrough latency is about this
MIN_LATENCY_MS, MAX_LATENCY_MS = 20, 500
MAX_DRIFT_TRIM = 0.005            # an output speeds up or slows down by at most 0.5% to follow another clock
TICK_S = 0.005                    # how often an output tops up its sink
CHUNK_FRAMES = 960                # the most an output produces in one go (20 ms)
GAIN_MIN_DB, GAIN_MAX_DB = -90.0, 24.0
BUS_OUTPUT = "bus"                # the AudioOutput id that plays Relay's own sounds into a bus output
LANE_SOURCE = "lane"              # the AudioSource id that lets the engine take a lane as its input

LOST_SENTENCE = "The audio input stopped. Check the device is plugged in; Relay reconnects on its own."
OUT_LOST_SENTENCE = "The audio output stopped. Check the device is plugged in; Relay reconnects on its own."


# -- configuration ---------------------------------------------------------------------------------------------
def default_config() -> dict:
    return {"enabled": False, "lanes": [], "outputs": [], "matrix": []}


def _int(v, lo: int, hi: int, default: int, drop: bool = False) -> int:
    """`v` as an int inside lo..hi: clamped, or `default` when `drop` (a lane number out of range is not the
    nearest lane, it is nothing)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if drop and not lo <= n <= hi:
        return default
    return max(lo, min(hi, n))


def normalize(raw) -> dict:
    """A clean `audio_bus` setting from whatever was saved or posted: out-of-range or duplicate entries are
    dropped rather than refused, so a hand-edited config never stops Relay starting."""
    raw = raw if isinstance(raw, dict) else {}
    lanes, seen = [], set()
    for item in raw.get("lanes") or []:
        if not isinstance(item, dict):
            continue
        n = _int(item.get("lane"), 1, LANES, 0, drop=True)
        if not n or n in seen:
            continue
        seen.add(n)
        ch = item.get("channel", 0)
        ch = "mix" if isinstance(ch, str) and ch.strip().lower() == "mix" else _int(ch, 0, 255, 0)
        device = item.get("device")
        lanes.append({
            "lane": n,
            "name": str(item.get("name") or "").strip()[:40],
            "source": (str(item.get("source") or audio.PORTAUDIO).strip().lower() or audio.PORTAUDIO)[:40],
            "device": None if device in (None, "") else str(device)[:200],
            "channel": ch,
        })
    lanes.sort(key=lambda x: x["lane"])
    outputs, ids = [], set()
    for item in raw.get("outputs") or []:
        if not isinstance(item, dict):
            continue
        oid = "".join(c for c in str(item.get("id") or "").strip().lower().replace(" ", "_")
                      if c.isalnum() or c == "_")[:24]
        if not oid or oid in ids or oid == BUS_OUTPUT:
            continue
        ids.add(oid)
        device = item.get("device")
        outputs.append({
            "id": oid,
            "name": str(item.get("name") or "").strip()[:40],
            "output": (str(item.get("output") or audio.PORTAUDIO).strip().lower() or audio.PORTAUDIO)[:40],
            "device": None if device in (None, "") else str(device)[:200],
            "channels": _int(item.get("channels"), 1, LANES, 2),
            "latency_ms": _int(item.get("latency_ms"), MIN_LATENCY_MS, MAX_LATENCY_MS, DEFAULT_LATENCY_MS),
        })
    chans = {o["id"]: o["channels"] for o in outputs}
    matrix, cells = [], set()
    for item in raw.get("matrix") or []:
        if not isinstance(item, dict):
            continue
        lane, oid = _int(item.get("in"), 1, LANES, 0, drop=True), str(item.get("out") or "")
        ch = _int(item.get("ch"), 1, LANES, 0, drop=True)
        if not lane or oid not in chans or not ch or ch > chans[oid] or (lane, oid, ch) in cells:
            continue
        cells.add((lane, oid, ch))
        try:
            gain = float(item.get("gain_db", 0.0))
        except (TypeError, ValueError):
            gain = 0.0
        if math.isnan(gain):
            gain = 0.0
        matrix.append({"in": lane, "out": oid, "ch": ch, "gain_db": round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, gain)), 2)})
    matrix.sort(key=lambda x: (x["out"], x["ch"], x["in"]))
    return {"enabled": bool(raw.get("enabled")), "lanes": lanes, "outputs": outputs, "matrix": matrix}


PROVIDER = "audiobus"


def report_channels(cfg: dict) -> dict[str, str] | None:
    """Tell core which audio channels the bus has (#542): each patched lane is a source and each channel of each output a
    destination, so the console, the intercom and a rundown can pick one by ref, and a route (app/routing.py, #544) can
    feed one lane into one output channel. Returns {key: channel ref}, or None while the database is not ready."""
    found = [{"key": f"lane{s['lane']}", "kind": "audio", "direction": "source", "number": s["lane"],
              "gear_name": s["name"] or f"Lane {s['lane']}"} for s in cfg["lanes"]]
    for o in cfg["outputs"]:
        shown = o["name"] or o["id"]
        found += [{"key": f"out:{o['id']}:{ch}", "kind": "audio", "direction": "destination", "number": ch,
                   "gear_name": shown if o["channels"] == 1 else f"{shown} {ch}"} for ch in range(1, o["channels"] + 1)]
    return channels.report(PROVIDER, found)


def _split_out(key: str) -> tuple[str, int] | None:
    """("<output id>", channel from 1) for a destination end key "out:<id>:<n>"."""
    oid, _, ch = key[4:].rpartition(":") if key.startswith("out:") else ("", "", "")
    return (oid, int(ch)) if oid and ch.isdigit() else None


def matrix_from_routes() -> list[dict]:
    """The crosspoints the bus should run, [{"in": lane, "out": output id, "ch", "gain_db"}], built from core routes: each
    output channel takes the lanes routed to it, each with its gain. Empty while the database is not ready."""
    cells: list[dict] = []
    try:
        world = channels.listing("audio")
        for c in world:
            for e in c["ends"]:
                where = _split_out(e["key"]) if e["provider"] == PROVIDER and e["direction"] == "destination" and not e["missing"] else None
                if where is None:
                    continue
                for w in routing.wanted(c["ref"]):
                    lanes = [int(x["key"][4:]) for x in w["ends"]
                             if x["provider"] == PROVIDER and x["key"].startswith("lane") and x["key"][4:].isdigit()]
                    if lanes:
                        cells.append({"in": lanes[0], "out": where[0], "ch": where[1], "gain_db": w["gain_db"] or 0.0})
    except db.Unavailable:
        log.warning("The audio bus matrix could not be read: the database is down")
    return cells


def save_matrix(cfg: dict, cells: list[dict]) -> None:
    """Make `cells` (what the page sends) the routes: every output channel takes exactly the lanes listed for it."""
    refs = report_channels(cfg) or {}
    per: dict[str, list[tuple[str, float]]] = {}
    for c in cells:
        lane = channels.ensure(PROVIDER, f"lane{c['in']}", "audio", "source", f"Lane {c['in']}")
        if lane:
            per.setdefault(f"out:{c['out']}:{c['ch']}", []).append((lane, c["gain_db"]))
    for key, ref in refs.items():
        if key.startswith("out:"):
            routing.replace(ref, per.get(key, []), apply=False)


def runtime_config(raw: dict) -> dict:
    """The bus setting with the matrix core routes hold. A matrix saved in config before #544 moves to routes first, once."""
    cfg = normalize(raw)
    try:
        if cfg["matrix"]:
            cells = cfg["matrix"]
            save_matrix(cfg, cells)
            config.save({"audio_bus": {**cfg, "matrix": []}}, replace=("audio_bus",))
        else:
            report_channels(cfg)
    except db.Unavailable:
        log.warning("The audio bus matrix could not be moved: the database is down")
    return {**cfg, "matrix": matrix_from_routes()}


def db_to_gain(db: float) -> float:
    return 0.0 if db <= GAIN_MIN_DB else 10.0 ** (db / 20.0)


# -- meters ------------------------------------------------------------------------------------------------------
class Meter:
    """Level, decaying peak-hold and clipping for one stream of float samples (the panel's 1 Hz poll reads it)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.level = 0.0
        self.peak = 0.0
        self._hold = 0.0
        self._ts = 0.0
        self.clipped = 0
        self._clip_until = 0.0

    def feed(self, block: np.ndarray, now: float) -> None:
        if not block.size:
            return
        mag = np.abs(block)
        self.level = float(np.sqrt(np.mean(np.square(block))))
        self.peak = float(mag.max())
        clipped = int(np.count_nonzero(mag >= audio.CLIP_LEVEL))
        if clipped:
            self.clipped += clipped
            self._clip_until = now + audio.CLIP_HOLD_S
        elapsed = (now - self._ts) if self._ts else 0.0
        self._ts = now
        decayed = self._hold * (10.0 ** (-(audio.PEAK_DECAY_DB_PER_S * elapsed) / 20.0)) if elapsed > 0 else self._hold
        self._hold = max(self.peak, decayed)

    @property
    def clipping(self) -> bool:
        return time.time() < self._clip_until

    def snapshot(self) -> dict:
        return {"rms_dbfs": round(audio.dbfs(self.level), 1), "peak_dbfs": round(audio.dbfs(self._hold), 1),
                "clipping": self.clipping, "clipped_samples": self.clipped}


# -- lanes -------------------------------------------------------------------------------------------------------
class Lane:
    """One mono stream at RATE. `push` is called from the input's thread; consumers never block it."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.name = ""
        self.size = int(RATE * RING_S)
        self.ring = np.zeros(self.size, dtype=np.float32)
        self.written = 0                       # total samples pushed; the ring holds the last `size`
        self.lock = threading.Lock()
        self.meter = Meter()
        self.last_ts = 0.0
        self._subs: dict[int, Callable[[np.ndarray], None]] = {}
        self._next = 1
        self._sub_lock = threading.Lock()

    def push(self, block: np.ndarray) -> None:
        n = len(block)
        if not n:
            return
        now = time.time()
        if n > self.size:
            block = block[-self.size:]
            n = self.size
        with self.lock:
            start = self.written % self.size
            first = min(n, self.size - start)
            self.ring[start:start + first] = block[:first]
            if first < n:
                self.ring[: n - first] = block[first:]
            self.written += n
        self.last_ts = now
        self.meter.feed(block, now)
        for cb in tuple(self._subs.values()):
            try:
                cb(block)
            except Exception:  # a consumer's fault must not stop the lane
                log.exception("lane %d consumer failed", self.index)

    def reset(self) -> None:
        with self.lock:
            self.ring[:] = 0.0
            self.written = 0
        self.meter.reset()
        self.last_ts = 0.0

    def subscribe(self, callback: Callable[[np.ndarray], None]) -> int:
        """Call `callback(block)` with every block (mono float32 at RATE) on the input's thread: it must not
        block. Any number of subscribers may take the same lane. Returns a token for `unsubscribe`."""
        with self._sub_lock:
            token, self._next = self._next, self._next + 1
            self._subs = {**self._subs, token: callback}
        return token

    def unsubscribe(self, token: int) -> None:
        with self._sub_lock:
            self._subs = {k: v for k, v in self._subs.items() if k != token}

    def subscribe_queue(self, loop: asyncio.AbstractEventLoop, maxsize: int = 100) -> tuple[int, asyncio.Queue]:
        """The same for async code: blocks arrive in an asyncio.Queue on `loop`; a full queue drops its oldest."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        q.dropped = 0

        def put(block: np.ndarray) -> None:
            if q.full():
                try:
                    q.get_nowait()
                    q.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(block)

        return self.subscribe(lambda b: loop.call_soon_threadsafe(put, b)), q

    @property
    def subscribers(self) -> int:
        return len(self._subs)


# -- inputs ------------------------------------------------------------------------------------------------------
def _key(source: str, device) -> tuple[str, str]:
    return source, str(device or "").strip().lower()


class _Tap:
    """Lane `lane` takes `channel` (an index, or "mix") of an input, resampled to RATE when the device is not."""

    def __init__(self, lane: Lane, channel) -> None:
        self.lane, self.channel = lane, channel
        self.stream: soxr.ResampleStream | None = None
        self.rate = RATE

    def prepare(self, rate: int) -> None:
        self.rate = rate
        self.stream = None if rate == RATE else soxr.ResampleStream(rate, RATE, 1, dtype="float32", quality="HQ")

    def feed(self, block: np.ndarray) -> None:
        channels = block.shape[1]
        if self.channel == "mix":
            x = block.mean(axis=1, dtype=np.float32) if channels > 1 else block[:, 0]
        else:
            x = block[:, min(int(self.channel), channels - 1)]
        if self.stream is not None:
            x = self.stream.resample_chunk(np.ascontiguousarray(x, dtype=np.float32))
        self.lane.push(np.ascontiguousarray(x, dtype=np.float32))


class _Intake:
    """One open device, shared by every lane patched to it."""

    def __init__(self, source: str, device) -> None:
        self.source, self.device = source, device
        self.input: audio.LaneInput | None = None
        self.info: audio.SourceInfo | None = None
        self.taps: tuple[_Tap, ...] = ()
        self.error: str | None = None
        self.last_block = 0.0
        self.next_try = 0.0
        self.blocks = 0
        self.dropped_closed = 0   # frames dropped by inputs of this intake that have since closed

    @property
    def state(self) -> str:
        return "open" if self.input is not None and not self.error else ("waiting" if self.error else "closed")

    def deliver(self, block: np.ndarray) -> None:
        if block.ndim != 2 or not len(block):
            return
        self.last_block = time.time()
        self.blocks += 1
        for tap in self.taps:
            try:
                tap.feed(block)
            except Exception:
                log.exception("lane %d could not take its channel", tap.lane.index)

    def label(self) -> str:
        return (self.info.device.get("label") if self.info else None) or str(self.device or "default input")

    def open(self) -> bool:
        src = audio.get_source(self.source)
        if src is None:
            self.error = "That audio source is not available. Check its module is switched on."
            return False
        li = src.lane_input()
        try:
            info = li.open(self.device, self.deliver)
        except audio.SourceError as exc:
            self.error = str(exc)
            return False
        except Exception:
            log.exception("audio source %s failed to open", self.source)
            self.error = "The audio source could not be opened."
            return False
        self.input, self.info, self.error = li, info, None
        self.last_block = time.time()
        for tap in self.taps:
            tap.prepare(int(info.rate))
        return True

    def close(self) -> None:
        li, self.input = self.input, None
        if li is not None:
            self.dropped_closed += li.dropped
            try:
                li.close()
            except Exception:
                log.exception("audio input %s did not close cleanly", self.source)

    @property
    def dropped(self) -> int:
        return self.dropped_closed + (self.input.dropped if self.input is not None else 0)


# -- outputs -----------------------------------------------------------------------------------------------------
@dataclass
class _Inject:
    samples: np.ndarray          # mono, at RATE
    channels: tuple[int, ...]    # 0-based output channels it is added to
    pos: int = 0


class _Output:
    """One open sink with its crosspoints. A thread tops its queue up to a target fill, reading each lane it
    needs through a drift-following resampler."""

    def __init__(self, bus: "Bus", spec: dict) -> None:
        self.bus = bus
        self.spec = spec
        self.id = spec["id"]
        self.channels = spec["channels"]
        self.latency_ms = spec["latency_ms"]
        self.sink: audio.LaneSink | None = None
        self.error: str | None = None
        self.next_try = 0.0
        self.matrix = np.zeros((0, self.channels), dtype=np.float32)
        self.lanes_used: tuple[int, ...] = ()
        self.meters = [Meter() for _ in range(self.channels)]
        self.underruns = 0              # times a routed lane had nothing for the output
        self.resyncs = 0
        self.pos: dict[int, float] = {}  # lane -> read position (a sample count, fractional)
        self.ratio: dict[int, float] = {}
        self._pending = np.zeros((0, self.channels), dtype=np.float32)
        self._resampler: soxr.ResampleStream | None = None
        self._injects: list[_Inject] = []
        self._inject_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- routing --------------------------------------------------------------------------------------------
    def set_matrix(self, cells: list[dict]) -> None:
        lanes = sorted({c["in"] for c in cells})
        m = np.zeros((len(lanes), self.channels), dtype=np.float32)
        for c in cells:
            m[lanes.index(c["in"]), c["ch"] - 1] = db_to_gain(c["gain_db"])
        self.matrix, self.lanes_used = m, tuple(lanes)   # one swap: the thread reads both together
        self.pos = {k: v for k, v in self.pos.items() if k in lanes}

    def inject(self, samples: np.ndarray, channels: tuple[int, ...]) -> None:
        with self._inject_lock:
            self._injects.append(_Inject(samples, channels))

    def stop_injects(self) -> None:
        with self._inject_lock:
            self._injects.clear()

    # -- sink -----------------------------------------------------------------------------------------------
    def open(self) -> bool:
        out = audio.get_output(self.spec["output"])
        if out is None:
            self.error = "That audio output is not available. Check its module is switched on."
            return False
        try:
            sink = out.lane_sink(self.spec["device"], self.channels)
        except audio.SourceError as exc:
            self.error = str(exc)
            return False
        except Exception:
            log.exception("audio output %s failed to open", self.spec["output"])
            self.error = "The audio output could not be opened."
            return False
        self.sink, self.error = sink, None
        self._resampler = None if sink.rate == RATE else soxr.ResampleStream(
            RATE, sink.rate, self.channels, dtype="float32", quality="HQ")
        self._pending = np.zeros((0, self.channels), dtype=np.float32)
        self.pos.clear()
        return True

    def close_sink(self) -> None:
        sink, self.sink = self.sink, None
        if sink is not None:
            try:
                sink.close()
            except Exception:
                log.exception("audio output %s did not close cleanly", self.spec["output"])

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"lane-out-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self.close_sink()

    # -- the mix ---------------------------------------------------------------------------------------------
    def _read_lane(self, lane: Lane, n: int) -> np.ndarray:
        """`n` samples of `lane` at the pace of this output's clock: the read position follows the lane's
        write position, trimming the speed by up to MAX_DRIFT_TRIM to hold the target lag."""
        target = int(RATE * self.latency_ms / 2000)
        with lane.lock:
            w = lane.written
            size = lane.size
            r = self.pos.get(lane.index)
            if r is None or r > w or w - r > size - 4 * CHUNK_FRAMES or w - r > 6 * target + CHUNK_FRAMES:
                if r is not None:
                    self.resyncs += 1
                r = float(max(0, w - target))
            lag = w - r
            ratio = 1.0 + max(-MAX_DRIFT_TRIM, min(MAX_DRIFT_TRIM, (lag - target) / RATE * 0.5))
            end = r + n * ratio
            if w == 0 or end + 1 >= w:
                self.underruns += 1
                self.pos[lane.index] = r
                return np.zeros(n, dtype=np.float32)
            at = r + np.arange(n) * ratio
            i0 = np.floor(at).astype(np.int64)
            frac = (at - i0).astype(np.float32)
            a = lane.ring[i0 % size]
            b = lane.ring[(i0 + 1) % size]
        self.pos[lane.index] = r + n * ratio
        self.ratio[lane.index] = ratio
        return a * (1.0 - frac) + b * frac

    def _mix(self, n: int) -> np.ndarray:
        lanes, matrix = self.lanes_used, self.matrix
        if lanes:
            cols = np.stack([self._read_lane(self.bus.lanes[i - 1], n) for i in lanes], axis=1)
            mixed = cols @ matrix
        else:
            mixed = np.zeros((n, self.channels), dtype=np.float32)
        with self._inject_lock:
            for inj in list(self._injects):
                take = inj.samples[inj.pos:inj.pos + n]
                for c in inj.channels:
                    if c < self.channels:
                        mixed[: len(take), c] += take
                inj.pos += len(take)
                if inj.pos >= len(inj.samples):
                    self._injects.remove(inj)
        now = time.time()
        for c in range(self.channels):
            self.meters[c].feed(mixed[:, c], now)
        return np.clip(mixed, -1.0, 1.0)

    def _run(self) -> None:
        while not self._stop.wait(TICK_S):
            sink = self.sink
            if sink is None:
                continue
            try:
                self._tick(sink)
            except Exception:
                log.exception("audio output %s failed", self.id)
                self.error = OUT_LOST_SENTENCE
                self.close_sink()

    def _tick(self, sink: audio.LaneSink) -> None:
        if len(self._pending):
            took = sink.write(self._pending)
            self._pending = self._pending[took:]
            if len(self._pending):
                return
        target_q = int(sink.rate * self.latency_ms / 2000)
        want = min(target_q - sink.queued(), sink.free())
        if want < 1:
            return
        n_sink = min(want, int(CHUNK_FRAMES * sink.rate / RATE) or CHUNK_FRAMES)
        n_lane = max(1, int(math.ceil(n_sink * RATE / sink.rate)))
        block = self._mix(n_lane)
        if self._resampler is not None:
            block = self._resampler.resample_chunk(np.ascontiguousarray(block, dtype=np.float32))
        took = sink.write(block)
        if took < len(block):
            self._pending = block[took:]

    def snapshot(self) -> dict:
        sink = self.sink
        return {
            "id": self.id, "name": self.spec["name"], "output": self.spec["output"],
            "device": self.spec["device"], "channels": self.channels, "latency_ms": self.latency_ms,
            "state": "open" if sink is not None else ("waiting" if self.error else "closed"),
            "error": self.error,
            "underruns": self.underruns + (sink.underruns if sink is not None else 0),
            "resyncs": self.resyncs,
            "queued_ms": round(1000 * sink.queued() / sink.rate, 1) if sink is not None else 0.0,
            "meters": [m.snapshot() for m in self.meters],
        }


# -- the bus -----------------------------------------------------------------------------------------------------
class Bus:
    def __init__(self) -> None:
        self.lanes = [Lane(i + 1) for i in range(LANES)]
        self.config = default_config()
        self.running = False
        self._intakes: dict[tuple[str, str], _Intake] = {}
        self._outputs: dict[str, _Output] = {}
        self._lock = threading.RLock()
        self._super: threading.Thread | None = None
        self._stop = threading.Event()

    # -- configuration ---------------------------------------------------------------------------------------
    def apply(self, cfg: dict) -> None:
        """Make the bus match `cfg`, touching only what changed: a device or output that is the same as before
        keeps running, so changing one feed does not disturb another."""
        cfg = normalize(cfg)
        report_channels(cfg)
        with self._lock:
            self.config = cfg
            if not cfg["enabled"]:
                self._stop_all()
                return
            self._apply_inputs(cfg)
            self._apply_outputs(cfg)
            self.running = True
            if self._super is None or not self._super.is_alive() or self._stop.is_set():
                self._stop = threading.Event()        # a fresh one: an old supervisor still winding down keeps its own
                self._super = threading.Thread(target=self._supervise, args=(self._stop,),
                                               name="lane-supervisor", daemon=True)
                self._super.start()

    def _apply_inputs(self, cfg: dict) -> None:
        wanted: dict[tuple[str, str], list[dict]] = {}
        patched = set()
        for spec in cfg["lanes"]:
            wanted.setdefault(_key(spec["source"], spec["device"]), []).append(spec)
            patched.add(spec["lane"])
        for key in [k for k in self._intakes if k not in wanted]:
            self._intakes.pop(key).close()
        for lane in self.lanes:
            if lane.index not in patched:
                lane.name = ""
                if lane.last_ts:
                    lane.reset()
        for key, specs in wanted.items():
            intake = self._intakes.get(key)
            if intake is None:
                intake = self._intakes[key] = _Intake(specs[0]["source"], specs[0]["device"])
            taps = []
            for spec in specs:
                lane = self.lanes[spec["lane"] - 1]
                lane.name = spec["name"]
                taps.append(_Tap(lane, spec["channel"]))
            for tap in taps:
                tap.prepare(int(intake.info.rate) if intake.info else RATE)
            intake.taps = tuple(taps)          # one swap, so the device thread never sees a half-built list
            if intake.input is None and not intake.error:
                intake.open()

    def _apply_outputs(self, cfg: dict) -> None:
        wanted = {o["id"]: o for o in cfg["outputs"]}
        for oid in [k for k, o in self._outputs.items() if k not in wanted or wanted[k] != o.spec]:
            self._outputs.pop(oid).stop()
        for oid, spec in wanted.items():
            out = self._outputs.get(oid)
            if out is None:
                out = self._outputs[oid] = _Output(self, spec)
                out.set_matrix([c for c in cfg["matrix"] if c["out"] == oid])
                out.open()
                out.start()
            else:
                out.set_matrix([c for c in cfg["matrix"] if c["out"] == oid])

    def _stop_all(self) -> None:
        self._stop.set()
        for out in self._outputs.values():
            out.stop()
        self._outputs.clear()
        for intake in self._intakes.values():
            intake.close()
        self._intakes.clear()
        for lane in self.lanes:
            lane.name = ""
            lane.reset()
        self.running = False

    def stop(self) -> None:
        with self._lock:
            self._stop_all()
        if self._super is not None and self._super is not threading.current_thread():
            self._super.join(timeout=2.0)
        self._super = None

    # -- unplug / replug ---------------------------------------------------------------------------------------
    def _supervise(self, stop: threading.Event) -> None:
        while not stop.wait(0.5):
            now = time.time()
            with self._lock:
                for intake in list(self._intakes.values()):
                    self._check_intake(intake, now)
                for out in list(self._outputs.values()):
                    self._check_output(out, now)

    def _check_intake(self, intake: _Intake, now: float) -> None:
        li = intake.input
        if li is not None and (not li.alive() or now - intake.last_block > audio.LOST_AFTER_S):
            log.warning("Audio input %s lost; waiting for it to come back", intake.label())
            intake.close()
            intake.error = LOST_SENTENCE
            intake.next_try = now + audio.RETRY_EVERY_S
        elif li is None and intake.error and now >= intake.next_try:
            had = intake.error
            if not intake.open():
                intake.error = had if had == LOST_SENTENCE else intake.error
                intake.next_try = now + audio.RETRY_EVERY_S

    def _check_output(self, out: _Output, now: float) -> None:
        sink = out.sink
        if sink is not None and not sink.alive():
            log.warning("Audio output %s lost; waiting for it to come back", out.id)
            out.close_sink()
            out.error = OUT_LOST_SENTENCE
            out.next_try = now + audio.RETRY_EVERY_S
        elif sink is None and now >= out.next_try:
            if not out.open():
                out.next_try = now + audio.RETRY_EVERY_S

    # -- helpers for consumers -----------------------------------------------------------------------------
    def lane(self, number: int) -> Lane:
        if not 1 <= int(number) <= LANES:
            raise ValueError(f"lanes are numbered 1 to {LANES}")
        return self.lanes[int(number) - 1]

    def output(self, output_id: str) -> _Output | None:
        return self._outputs.get(output_id)

    # -- status ----------------------------------------------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            patch = {s["lane"]: s for s in self.config["lanes"]}
            lanes = []
            for lane in self.lanes:
                spec = patch.get(lane.index)
                intake = self._intakes.get(_key(spec["source"], spec["device"])) if spec else None
                lanes.append({
                    "lane": lane.index, "name": lane.name, "patched": spec is not None,
                    "source": spec["source"] if spec else None, "device": spec["device"] if spec else None,
                    "channel": spec["channel"] if spec else None,
                    "state": intake.state if intake else "unpatched",
                    "error": intake.error if intake else None,
                    "subscribers": lane.subscribers,
                    **lane.meter.snapshot(),
                })
            inputs = [{"source": i.source, "device": i.device, "label": i.label(), "state": i.state,
                       "error": i.error, "rate": int(i.info.rate) if i.info else None,
                       "channels": int(i.info.device.get("channels", 0)) if i.info else None,
                       "lanes": [t.lane.index for t in i.taps], "dropped": i.dropped}
                      for i in self._intakes.values()]
            return {"running": self.running, "lane_rate": RATE, "lanes": lanes, "inputs": inputs,
                    "outputs": [o.snapshot() for o in self._outputs.values()], "matrix": self.config["matrix"]}


bus = Bus()


# -- the engine's way in: a lane as an audio source -------------------------------------------------------------
class LaneSource(audio.AudioSource):
    """`audio_source: "lane"` with `audio_device: "<lane number>"` feeds the main capture from a lane, so the
    captions can take any patched input. The source is the lane's mono stream at RATE."""

    id = LANE_SOURCE
    label = "Audio bus lane"

    def __init__(self, target: Bus | None = None) -> None:
        self._bus = target
        self._lane: Lane | None = None
        self._token = 0

    @property
    def bus(self) -> Bus:
        return self._bus or bus

    def open(self, selector, channel, deliver) -> audio.SourceInfo:
        self.close()
        try:
            lane = self.bus.lane(int(str(selector).strip().lower().replace("lane", "") or 0))
        except (TypeError, ValueError):
            raise audio.SourceError(f"Pick a lane from 1 to {LANES}.") from None
        if not self.bus.running:
            raise audio.SourceError("The audio bus is not running. Switch it on and patch an input to this lane.")
        self._lane, self._token = lane, lane.subscribe(deliver)
        return audio.SourceInfo(rate=RATE, device={"label": f"Lane {lane.index}" + (f": {lane.name}" if lane.name else ""),
                                                   "channels": 1}, channel=0)

    def close(self) -> None:
        lane, self._lane = self._lane, None
        if lane is not None:
            lane.unsubscribe(self._token)

    def alive(self) -> bool:
        lane = self._lane
        return lane is not None and self.bus.running


# -- Relay's own sounds into a bus output ---------------------------------------------------------------------
class BusOutput(audio.AudioOutput):
    """`play` with selector "<output id>" or "<output id>:1,2" (1-based channels; default: every channel of a
    one- or two-channel output, else the first two) adds the clip to that bus output's mix."""

    id = BUS_OUTPUT
    label = "Audio bus output"

    def __init__(self, target: Bus | None = None) -> None:
        self._bus = target

    @property
    def bus(self) -> Bus:
        return self._bus or bus

    def play(self, pcm: bytes, rate: int = audio.TARGET_RATE, selector=None) -> None:
        try:
            oid, _, chans = str(selector or "").partition(":")
            out = self.bus.output(oid.strip().lower())
            if out is None:
                log.warning("No bus output %r for this sound", oid)
                return
            mono = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            if int(rate) != RATE:
                mono = soxr.resample(mono, int(rate), RATE).astype(np.float32)
            wanted = [int(c) - 1 for c in chans.split(",") if c.strip().isdigit()] or list(range(min(2, out.channels)))
            out.inject(mono, tuple(c for c in wanted if 0 <= c < out.channels))
        except Exception:
            log.exception("Could not play into the audio bus")

    def stop(self) -> None:
        for out in list(self.bus._outputs.values()):
            out.stop_injects()


audio.register_source(LaneSource())
audio.register_output(BusOutput())


# -- core routes (#544) ---------------------------------------------------------------------------------------------
def _core_apply(end: dict, wanted: list[dict]) -> str:
    """A route into an output channel changed (from any page): run the bus on the matrix the routes now say."""
    try:
        bus.apply({**bus.config, "matrix": matrix_from_routes()})
    except Exception:
        log.exception("The audio bus could not take a route")
        return "The audio bus did not take that route."
    return ""


def _core_read(end: dict) -> list[dict] | None:
    """The lanes the bus is really mixing into an output channel, with their gains; None while the bus is off."""
    where = _split_out(end["key"])
    if where is None or not bus.config["enabled"] or not bus.running:
        return None
    return [{"provider": PROVIDER, "key": f"lane{c['in']}", "gain_db": c["gain_db"]}
            for c in bus.config["matrix"] if c["out"] == where[0] and c["ch"] == where[1]]


routing.provide_core(PROVIDER, {"exclusive": False, "gain": True, "apply": _core_apply, "read": _core_read})
