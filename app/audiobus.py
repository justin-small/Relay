"""The 32-lane audio bus: many inputs in, a crosspoint matrix, many outputs out (#92).

Inputs. Any device of any registered AudioSource (PortAudio, or a module's network stream) can be patched to
any of the 32 lanes. A device is opened once however many lanes use it. Each lane is a mono float32 stream at
LANE_RATE (48 kHz), metered on its own, and any number of consumers subscribe to it: the engine (through the
"lane" audio source below), a recorder, a module, or an output.

Matrix. An output channel is the sum of the lanes routed to it, each with a gain. Devices run on their own
clocks, so an output reads each lane through a small resampler whose ratio is nudged to hold a steady fill
(a fraction of a percent, inaudible), instead of clicking when two clocks drift apart.

The Rust core. All of that, and every sound device, runs in `relay_bus` (native/relay-bus, built by
tools/build_bus_wheel.py): no Python runs on an audio thread, so a busy Python process cannot starve the audio.
This file is the Python side: the saved setting (normalize), core routes, a module's network sources (read here
and handed to the core with `push`) and senders (fed here from the core with `pull`), and lane readers for the
Python consumers (a tap per subscriber, read on a thread of its own).

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
READ_FRAMES = 4800                # the most a lane reader takes in one go (100 ms); what has built up arrives as one block
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


# -- the engine ---------------------------------------------------------------------------------------------------
# The mixing, resampling and metering are done by the Rust core (native/relay-bus, the `relay_bus` wheel, #601): no
# Python runs on an audio thread. What stays here is what only Python can do: reading a module's network source
# and handing it over, driving a module's network sender, and handing lane audio to the Python consumers.
def _meter(m: dict) -> dict:
    """The panel's meter shape (dBFS) from the engine's linear one."""
    return {"rms_dbfs": round(audio.dbfs(m["rms"]), 1), "peak_dbfs": round(audio.dbfs(m["peak"]), 1),
            "clipping": bool(m["clipping"]), "clipped_samples": int(m["clipped_samples"])}


def input_id(source: str, device) -> str:
    """The engine's name for an input: `source:device`, lower-cased (config::input_id in the Rust core)."""
    return f"{str(source).strip().lower()}:{str(device or '').strip().lower()}"


def _key(source: str, device) -> tuple[str, str]:
    return source, (device or "").lower()


class _Reader:
    """A reader of one lane: a tap in the engine and a Python thread that hands each block to `callback`. The
    thread is ours and may be slow; the engine's audio threads never wait for it (a slow reader loses frames)."""

    def __init__(self, bus: "Bus", lane: int, callback: Callable[[np.ndarray], None]) -> None:
        self.bus, self.lane, self.callback = bus, lane, callback
        self.tap = bus.engine.open_tap(lane)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"lane-{lane}-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        eng = self.bus.engine
        while not self._stop.is_set():
            try:
                block, _ = eng.read_tap(self.tap, READ_FRAMES, 0.1)
            except ValueError:
                return                                  # the tap is gone
            if len(block):
                try:
                    self.callback(block)
                except Exception:  # a consumer's fault must not stop the lane
                    log.exception("lane %d consumer failed", self.lane)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        try:
            self.bus.engine.close_tap(self.tap)
        except (ValueError, RuntimeError):
            pass


class Lane:
    """One mono stream at RATE. Consumers `subscribe` and are called with float32 blocks from a thread of their
    own; they never hold up the audio."""

    def __init__(self, bus: "Bus", index: int) -> None:
        self.bus = bus
        self.index = index
        self.name = ""
        self._subs: dict[int, _Reader] = {}
        self._next = 1
        self._sub_lock = threading.Lock()
        self._tail: _Tail | None = None

    def subscribe(self, callback: Callable[[np.ndarray], None]) -> int:
        """Call `callback(block)` with every block (mono float32 at RATE) on a thread of its own. Any number of
        subscribers may take the same lane. Returns a token for `unsubscribe`."""
        reader = _Reader(self.bus, self.index, callback)
        with self._sub_lock:
            token, self._next = self._next, self._next + 1
            self._subs[token] = reader
        return token

    def unsubscribe(self, token: int) -> None:
        with self._sub_lock:
            reader = self._subs.pop(token, None)
        if reader is not None:
            reader.close()

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

    def tail(self, n: int) -> tuple[np.ndarray, int]:
        """The newest `n` samples of the lane (up to the last RING_S seconds) and the running total of samples seen.
        The first call starts keeping the lane; before that there is nothing to return."""
        if self._tail is None:
            self._tail = _Tail(self)
        return self._tail.read(n)

    @property
    def written(self) -> int:
        return self.tail(0)[1]

    def stop_tail(self) -> None:
        tail, self._tail = self._tail, None
        if tail is not None:
            tail.close()


class _Tail:
    """The last RING_S seconds of one lane, kept for the readers that look back (the stream-mix report)."""

    def __init__(self, lane: Lane) -> None:
        self.size = int(RATE * RING_S)
        self.ring = np.zeros(self.size, dtype=np.float32)
        self.written = 0
        self.lock = threading.Lock()
        self.token = lane.subscribe(self._push)
        self.lane = lane

    def _push(self, block: np.ndarray) -> None:
        n = len(block)
        if n > self.size:
            block, n = block[-self.size:], self.size
        with self.lock:
            start = self.written % self.size
            first = min(n, self.size - start)
            self.ring[start:start + first] = block[:first]
            if first < n:
                self.ring[: n - first] = block[first:]
            self.written += n

    def read(self, n: int) -> tuple[np.ndarray, int]:
        with self.lock:
            w = self.written
            n = max(0, min(n, w, self.size))
            idx = np.arange(w - n, w) % self.size
            return self.ring[idx].copy(), w

    def close(self) -> None:
        self.lane.unsubscribe(self.token)


# -- network sources and senders: the part Python drives ------------------------------------------------------------
class _PyIntake:
    """A module's network source (not a sound device): Python reads it and hands each block to the engine, which
    resamples, picks channels and meters it like a device's. A device (source "portaudio") is the engine's own."""

    def __init__(self, bus: "Bus", source: str, device) -> None:
        self.bus = bus
        self.source, self.device = source, device
        self.id = input_id(source, device)
        self.input: audio.LaneInput | None = None
        self.info: audio.SourceInfo | None = None
        self.rate = 0
        self.lanes: tuple[int, ...] = ()
        self.error: str | None = None
        self.last_block = 0.0
        self.next_try = 0.0
        self.blocks = 0
        self.dropped_closed = 0

    @property
    def state(self) -> str:
        return "open" if self.input is not None and not self.error else ("waiting" if self.error else "closed")

    def deliver(self, block: np.ndarray) -> None:
        if block.ndim != 2 or not len(block) or not self.rate:
            return
        self.last_block = time.time()
        self.blocks += 1
        try:
            self.bus.engine.push(self.id, np.ascontiguousarray(block, dtype=np.float32), self.rate)
        except (ValueError, RuntimeError):
            pass                                          # the engine is being reconfigured; this block is lost

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
        self.input, self.info, self.error, self.rate = li, info, None, int(info.rate)
        self.last_block = time.time()
        return True

    def close(self) -> None:
        li, self.input, self.rate = self.input, None, 0
        if li is not None:
            self.dropped_closed += li.dropped
            try:
                li.close()
            except Exception:
                log.exception("audio input %s did not close cleanly", self.source)

    @property
    def dropped(self) -> int:
        return self.dropped_closed + (self.input.dropped if self.input is not None else 0)


class _PyOutput:
    """A module's network sender (not a sound device): a thread keeps its sink topped up to a target fill with
    audio the engine mixes for it (`pull`). A device (output "portaudio") is the engine's own."""

    def __init__(self, bus: "Bus", spec: dict) -> None:
        self.bus = bus
        self.spec = spec
        self.id = spec["id"]
        self.channels = spec["channels"]
        self.latency_ms = spec["latency_ms"]
        self.sink: audio.LaneSink | None = None
        self.error: str | None = None
        self.next_try = 0.0
        self.underruns = 0
        self._pending = np.zeros((0, self.channels), dtype=np.float32)
        self._resampler: soxr.ResampleStream | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

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
        return True

    def close_sink(self) -> None:
        sink, self.sink = self.sink, None
        if sink is not None:
            self.underruns += sink.underruns
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
        block = self.bus.engine.pull(self.id, n_lane)
        if self._resampler is not None:
            block = self._resampler.resample_chunk(np.ascontiguousarray(block, dtype=np.float32))
        took = sink.write(block)
        if took < len(block):
            self._pending = block[took:]


class _OutputRef:
    """What a consumer gets from `Bus.output(id)`: the channel count and a way to add a clip to the mix."""

    def __init__(self, bus: "Bus", spec: dict) -> None:
        self.bus, self.id, self.channels, self.spec = bus, spec["id"], spec["channels"], spec

    def inject(self, samples: np.ndarray, channels: tuple[int, ...], rate: int = RATE) -> None:
        """Mix mono `samples` into the given output channels (counted from 0) in the next block."""
        self.bus.engine.inject(self.id, list(channels), np.ascontiguousarray(samples, dtype=np.float32), int(rate))

    def stop_injects(self) -> None:
        self.bus.engine.stop_injects(self.id)


# -- the bus -----------------------------------------------------------------------------------------------------
class Bus:
    def __init__(self, backend: str = "cpal") -> None:
        self.backend = backend                  # "cpal": real devices; "fake": the engine's own test devices
        self._engine = None
        self.lanes = [Lane(self, i + 1) for i in range(LANES)]
        self.config = default_config()
        self.running = False
        self._intakes: dict[str, _PyIntake] = {}
        self._outputs: dict[str, _PyOutput] = {}
        self._lock = threading.RLock()
        self._super: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def engine(self):
        """The Rust engine, made on first use (it starts its device watcher and supervisor threads)."""
        with self._lock:
            if self._engine is None:
                import relay_bus
                self._engine = relay_bus.Bus(self.backend)
            return self._engine

    # -- configuration ---------------------------------------------------------------------------------------
    def apply(self, cfg: dict) -> None:
        """Make the bus match `cfg`, touching only what changed: a device or output that is the same as before
        keeps running, so changing one feed does not disturb another."""
        cfg = normalize(cfg)
        report_channels(cfg)
        with self._lock:
            self.config = cfg
            for lane in self.lanes:
                lane.name = ""
            if not cfg["enabled"]:
                self._stop_all()
                return
            for spec in cfg["lanes"]:
                self.lanes[spec["lane"] - 1].name = spec["name"]
            self._stop_outputs(cfg)             # before the engine forgets them: a sender must not pull from a gone output
            self.engine.apply(cfg)
            self._apply_intakes(cfg)
            self._start_outputs(cfg)
            self.running = True
            if self._super is None or not self._super.is_alive() or self._stop.is_set():
                self._stop = threading.Event()        # a fresh one: an old supervisor still winding down keeps its own
                self._super = threading.Thread(target=self._supervise, args=(self._stop,),
                                               name="lane-supervisor", daemon=True)
                self._super.start()

    def _apply_intakes(self, cfg: dict) -> None:
        wanted: dict[str, list[dict]] = {}
        for spec in cfg["lanes"]:
            if spec["source"] != audio.PORTAUDIO:
                wanted.setdefault(input_id(spec["source"], spec["device"]), []).append(spec)
        for key in [k for k in self._intakes if k not in wanted]:
            self._intakes.pop(key).close()
        for key, specs in wanted.items():
            intake = self._intakes.get(key)
            if intake is None:
                intake = self._intakes[key] = _PyIntake(self, specs[0]["source"], specs[0]["device"])
            intake.lanes = tuple(s["lane"] for s in specs)
            if intake.input is None and not intake.open():
                intake.next_try = time.time() + audio.RETRY_EVERY_S

    @staticmethod
    def _senders(cfg: dict) -> dict[str, dict]:
        """The outputs Python drives (a module's network sender), by id."""
        return {o["id"]: o for o in cfg["outputs"] if o["output"] != audio.PORTAUDIO}

    def _stop_outputs(self, cfg: dict) -> None:
        wanted = self._senders(cfg)
        for oid in [k for k in self._outputs if k not in wanted or self._outputs[k].spec != wanted[k]]:
            self._outputs.pop(oid).stop()

    def _start_outputs(self, cfg: dict) -> None:
        for oid, spec in self._senders(cfg).items():
            if oid in self._outputs:
                continue
            out = self._outputs[oid] = _PyOutput(self, spec)
            if not out.open():
                out.next_try = time.time() + audio.RETRY_EVERY_S
            out.start()

    def _stop_all(self) -> None:
        self._stop.set()
        for out in self._outputs.values():
            out.stop()
        self._outputs.clear()
        for intake in self._intakes.values():
            intake.close()
        self._intakes.clear()
        if self._engine is not None:
            self._engine.apply({"enabled": False, "lanes": [], "outputs": [], "matrix": []})
        self.running = False

    def stop(self) -> None:
        with self._lock:
            self.config = {**self.config, "enabled": False}
            self._stop_all()
        if self._super is not None and self._super is not threading.current_thread():
            self._super.join(timeout=2.0)
        self._super = None
        for lane in self.lanes:
            lane.stop_tail()

    def close(self) -> None:
        """Stop, then stop the engine's own threads (tests do; Relay lets the process end do it)."""
        self.stop()
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    # -- unplug / replug: the engine reopens its own devices; this reopens the sources and senders Python drives --
    def _supervise(self, stop: threading.Event) -> None:
        while not stop.wait(0.5):
            now = time.time()
            with self._lock:
                for intake in list(self._intakes.values()):
                    self._check_intake(intake, now)
                for out in list(self._outputs.values()):
                    self._check_output(out, now)

    def _check_intake(self, intake: _PyIntake, now: float) -> None:
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

    def _check_output(self, out: _PyOutput, now: float) -> None:
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

    def output(self, output_id: str) -> _OutputRef | None:
        for spec in self.config["outputs"]:
            if spec["id"] == output_id and self.running:
                return _OutputRef(self, spec)
        return None

    def status(self) -> dict:
        with self._lock:
            patch = {s["lane"]: s for s in self.config["lanes"]}
            eng = self._engine.status() if self._engine is not None and self.running else None
            e_lanes = {l["lane"]: l for l in eng["lanes"]} if eng else {}
            e_inputs = {i["id"]: i for i in eng["inputs"]} if eng else {}
            lanes = []
            for lane in self.lanes:
                spec = patch.get(lane.index)
                e = e_lanes.get(lane.index)
                py = self._intakes.get(input_id(spec["source"], spec["device"])) if spec else None
                state, error = (py.state, py.error) if py is not None else (
                    ((e["state"] if e else None) or "closed", e["error"] if e else None) if spec else ("unpatched", None))
                m = _meter(e["meter"]) if e else _meter({"rms": 0.0, "peak": 0.0, "clipping": False, "clipped_samples": 0})
                lanes.append({
                    "lane": lane.index, "name": lane.name, "patched": spec is not None,
                    "source": spec["source"] if spec else None, "device": spec["device"] if spec else None,
                    "channel": spec["channel"] if spec else None,
                    "state": state, "error": error, "subscribers": lane.subscribers, **m,
                })
            inputs = []
            for iid, i in e_inputs.items():
                py = self._intakes.get(iid)
                if py is not None:
                    inputs.append({"source": py.source, "device": py.device, "label": py.label(), "state": py.state,
                                   "error": py.error, "rate": py.rate or None,
                                   "channels": int(py.info.device.get("channels", 0)) if py.info else None,
                                   "lanes": list(py.lanes), "dropped": py.dropped})
                else:
                    inputs.append({"source": i["source"], "device": i["device"], "label": str(i["device"] or "default input"),
                                   "state": i["state"], "error": i["error"], "rate": i["rate"], "channels": i["channels"],
                                   "lanes": i["lanes"], "dropped": i["dropped"]})
            outputs = []
            for spec in self.config["outputs"] if self.running else ():
                e = next((o for o in (eng["outputs"] if eng else []) if o["id"] == spec["id"]), None)
                py = self._outputs.get(spec["id"])
                state, error = (("open" if py.sink is not None else ("waiting" if py.error else "closed")), py.error) if py is not None \
                    else ((e["state"], e["error"]) if e else ("closed", None))
                outputs.append({
                    "id": spec["id"], "name": spec["name"], "output": spec["output"], "device": spec["device"],
                    "channels": spec["channels"], "latency_ms": spec["latency_ms"], "state": state, "error": error,
                    "underruns": (e["underruns"] if e else 0) + (py.underruns + (py.sink.underruns if py and py.sink else 0) if py else 0),
                    "resyncs": e["resyncs"] if e else 0, "queued_ms": e["queued_ms"] if e else 0.0,
                    "meters": [_meter(m) for m in e["meters"]] if e else [],
                    "ratio": e["ratio"] if e else 1.0, "drift": e["drift"] if e else 0.0,
                })
            return {"running": self.running, "lane_rate": RATE, "lanes": lanes, "inputs": inputs, "outputs": outputs,
                    "matrix": self.config["matrix"]}


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
            out.inject(mono, tuple(c for c in wanted if 0 <= c < out.channels), RATE)
        except Exception:
            log.exception("Could not play into the audio bus")

    def stop(self) -> None:
        for spec in list(self.bus.config["outputs"]):
            out = self.bus.output(spec["id"])
            if out is not None:
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


routing.provide_core(PROVIDER, {"exclusive": False, "gain": True, "apply": _core_apply, "read": _core_read,
                               "permission": "audio.manage"})
