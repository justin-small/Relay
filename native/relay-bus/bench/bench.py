"""Head-to-head benchmark: the Rust audio bus (relay_bus) against Relay's Python bus (app/audiobus.py).  Issue 599, phase 3.

    .venv/bin/python native/relay-bus/bench/bench.py --engine rust   --scenario B
    .venv/bin/python native/relay-bus/bench/bench.py --engine python --scenario C --seconds 30
    .venv/bin/python native/relay-bus/bench/bench.py --all                    # everything, 3 repeats, results/*.json
    .venv/bin/python native/relay-bus/bench/bench.py --report                 # markdown tables from results/summary.json

Engines: `rust` (relay_bus, cpal), `python` (app.audiobus with rtmixer, what Relay runs), `python-nortmixer` (the same
bus with rtmixer switched off, so the PortAudio callbacks are Python and hold the GIL).

Scenarios (each runs `--seconds`, default 60, after the bus is up and a 5 s settle that is not counted):
  A  light: mic -> lane 1 with a 24 kHz tap read continuously; 2 ch speaker fed by one synthetic lane at -60 dB
  B  heavy: 32 lanes (mic + the 31 channels of four 8 ch synthetic sources, two of the four at 44.1 kHz), the speaker plus
     three 8 ch outputs that are not devices, a full matrix at -40 dB (no mic -> speaker cell), four 24 kHz taps
  C  B plus 20 alert tones (1 kHz, -20 dBFS, 300 ms) at random moments: time to the output meter and to the fake device
  D  B plus an asyncio loop that schedules a 5 ms timer over and over and records how late it fires
  E  B plus a CPU-bound pure-Python thread (a tight loop: the worst case); underruns, how much input still arrived
  F  Rust only: the engine on its own fake backend (no Python in its audio path), first half idle, second half with the
     busy thread: does Python load glitch the engine itself?
  G  B plus a bursty Python thread (2 ms of work, 8 ms asleep: 20% of a core, like a request handler)

Every run is its own process (clean RSS, clean CPU counters). The synthetic sources are fed from Python threads, and the
"devices" that are not real are drained by Python threads on the same wall clock for both engines; see RESULTS.md.

SAFETY. The microphone has no matrix cell to the real speaker, ever (asserted below). Everything the speaker receives is
at most -40 dBFS (asserted: sum of the cells' peak amplitudes), except the alert tone of scenario C at -20 dBFS.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import math
import os
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import warnings

import numpy as np

# numpy 2.2 on macOS (Accelerate) raises spurious floating point flags from threaded matmul calls (checked: the products are
# right). The Python bus' mixer would print them thousands of times a minute.
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*encountered in matmul")

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RESULTS = HERE / "results"
RATE = 48000
AMP = 0.03                 # peak of every synthetic sine: 31 of them at -40 dB sum to at most -40.6 dBFS
LATENCY_MS = 60            # both engines' default
TAP_RATE = 24000
TONE_HZ, TONE_DB, TONE_S = 1000.0, -20.0, 0.3
TONE_THRESHOLD = 0.05      # a peak above this in an output means the tone has arrived (background is below 0.01)
RISE_RMS = 10 ** (-30 / 20)  # the speaker meter has "risen" when a channel's RMS passes -30 dBFS (tone: -23, background: -59)
SCENARIOS = "ABCDEFG"
ENGINES = ("rust", "python", "python-nortmixer")


# -- configuration (the same dict for both engines: audiobus.normalize() output) ---------------------------------------
def lane_freq(n: int) -> int:
    return 200 + 30 * n


# Lanes 2..32 are the 31 channels of four 8 channel synthetic devices (a 32 channel interface seen as four 8 channel
# sources): lane n is channel (n-2) % 8 of device d{(n-2)//8}. Devices d1 and d3 run at 44.1 kHz, so the resampler runs.
DEVICE_RATES = {"d0": 48000, "d1": 44100, "d2": 48000, "d3": 44100}


def lane_device(n: int) -> tuple[str, int]:
    return f"d{(n - 2) // 8}", (n - 2) % 8


def build_config(scn: str) -> dict:
    synth = [2] if scn == "A" else list(range(2, 33))
    lanes = [{"lane": 1, "name": "Mic", "source": "portaudio", "device": None, "channel": 0}]
    lanes += [{"lane": n, "name": f"Synth {n}", "source": "synth", "device": lane_device(n)[0], "channel": lane_device(n)[1]}
              for n in synth]
    outputs = [{"id": "spk", "name": "Speaker", "output": "portaudio", "device": None, "channels": 2, "latency_ms": LATENCY_MS}]
    matrix = [{"in": n, "out": "spk", "ch": ch, "gain_db": -60.0 if scn == "A" else -40.0} for ch in (1, 2) for n in synth]
    if scn != "A":
        for i in (1, 2, 3):
            outputs.append({"id": f"f{i}", "name": f"Fake {i}", "output": "fakesink", "device": None, "channels": 8,
                            "latency_ms": LATENCY_MS})
            matrix += [{"in": n, "out": f"f{i}", "ch": ch, "gain_db": -40.0} for ch in range(1, 9) for n in range(1, 33)]
    matrix.sort(key=lambda x: (x["out"], x["ch"], x["in"]))
    cfg = {"enabled": True, "lanes": lanes, "outputs": outputs, "matrix": matrix}
    # Safety: no cell from the microphone (lane 1) to the speaker, and the speaker's worst-case peak is <= -40 dBFS.
    to_spk = [c for c in matrix if c["out"] == "spk"]
    assert all(c["in"] != 1 for c in to_spk), "the microphone must never reach the speaker"
    worst = max(sum(AMP * 10 ** (c["gain_db"] / 20) for c in to_spk if c["ch"] == ch) for ch in (1, 2))
    assert 20 * math.log10(worst) <= -39.99, f"speaker worst-case peak {20 * math.log10(worst):.2f} dBFS is above -40"
    return cfg


def synth_devices(scn: str) -> dict[str, dict]:
    """device name -> {rate, channels, buf}: a 0.1 s loop of 8 channels, every frequency a multiple of 10 Hz so it repeats
    without a seam. Channel c of device dK carries lane_freq(2 + 8K + c), the frequency of the lane that takes it."""
    out = {}
    for name in (["d0"] if scn == "A" else DEVICE_RATES):
        rate, k = DEVICE_RATES[name], int(name[1:])
        frames = rate // 10
        t = np.arange(frames) / rate
        buf = np.empty((frames, 8), dtype=np.float32)
        for c in range(8):
            buf[:, c] = AMP * np.sin(2 * np.pi * lane_freq(2 + 8 * k + c) * t)
        out[name] = {"rate": rate, "channels": 8, "buf": np.ascontiguousarray(buf), "deliver": None}
    return out


# -- helpers -----------------------------------------------------------------------------------------------------------
def pct(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def lin(db: float) -> float:
    return 0.0 if db <= -60.0 else 10 ** (db / 20)


def db(x: float) -> float:
    return 20 * math.log10(x) if x > 1e-12 else -240.0


_libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)


class _ThreadInfo(ctypes.Structure):
    _fields_ = [("user", ctypes.c_uint64), ("sys", ctypes.c_uint64), ("cpu", ctypes.c_int32), ("policy", ctypes.c_int32),
                ("run", ctypes.c_int32), ("flags", ctypes.c_int32), ("sleep", ctypes.c_int32), ("curpri", ctypes.c_int32),
                ("pri", ctypes.c_int32), ("maxpri", ctypes.c_int32), ("name", ctypes.c_char * 64)]


class _TaskInfo(ctypes.Structure):
    _fields_ = [("virt", ctypes.c_uint64), ("resident", ctypes.c_uint64), ("rest", ctypes.c_uint64 * 10)]


def thread_cpu() -> dict[int, tuple[float, str]]:
    """{thread id: (cpu seconds so far, OS thread name)} for every live thread of this process (macOS libproc)."""
    pid = os.getpid()
    ids = (ctypes.c_uint64 * 1024)()
    got = _libproc.proc_pidinfo(pid, 28, 0, ids, ctypes.sizeof(ids))   # PROC_PIDLISTTHREADIDS
    out = {}
    for tid in ids[: max(0, got) // 8]:
        ti = _ThreadInfo()
        if _libproc.proc_pidinfo(pid, 15, tid, ctypes.byref(ti), ctypes.sizeof(ti)) > 0:   # PROC_PIDTHREADID64INFO
            out[tid] = ((ti.user + ti.sys) / 1e9, ti.name.decode(errors="replace"))
    return out


def rss_mb() -> float:
    ti = _TaskInfo()
    _libproc.proc_pidinfo(os.getpid(), 4, 0, ctypes.byref(ti), ctypes.sizeof(ti))   # PROC_PIDTASKINFO
    return ti.resident / 1048576


def cpu_s() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def thread_groups() -> dict[int, str]:
    """native thread id -> group, for the Python threads (named by the code that starts them)."""
    groups = {}
    for t in threading.enumerate():
        n = t.name
        g = ("main" if t is threading.main_thread() else "py-bus" if n.startswith("lane-") else
             "bench-gen" if n.startswith("bench-gen") else "bench-drain" if n.startswith("bench-drain") else
             "bench-tap" if n.startswith("bench-tap") else "bench-poll" if n.startswith("bench-poll") else
             "bench-busy" if n.startswith("bench-busy") else "py-other")
        if t.native_id:
            groups[t.native_id] = g
    return groups


# -- paced threads: absolute schedules, so a late wake-up is caught up, not accumulated ----------------------------------
class Paced(threading.Thread):
    def __init__(self, name: str, period: float):
        super().__init__(name=name, daemon=True)
        self.period, self.stop_ev = period, threading.Event()
        self.ticks, self.resets, self.late = 0, 0, []
        self.measuring = False

    def tick(self, k: int, t0: float) -> None:
        raise NotImplementedError

    def run(self) -> None:
        t0, k = time.perf_counter() + 0.02, 0
        while not self.stop_ev.is_set():
            due = t0 + k * self.period
            now = time.perf_counter()
            if now < due:
                time.sleep(max(0.0, due - now - 0.0003))
                continue
            late = now - due
            if self.measuring:
                self.late.append(late)
            if late > 0.5:
                self.resets += 1
                t0 = now - k * self.period       # give up catching up: the schedule restarts from now
            self.tick(k, t0)
            k += 1
            self.ticks = k


class Driver(Paced):
    """Plays the synthetic inputs: every 20 ms each device's next 10 ms block goes to its deliver function (Python bus:
    the intake's deliver, Rust: bus.push), the way a device callback would."""

    def __init__(self, devices: dict):
        super().__init__("bench-gen", 0.02)
        self.devices, self.errors = devices, 0

    def tick(self, k: int, t0: float) -> None:
        for d in self.devices.values():
            fn = d["deliver"]
            if fn is None:
                continue
            n = d["rate"] // 50
            i = (k * n) % len(d["buf"])
            try:
                fn(d["buf"][i:i + n])
            except Exception:
                self.errors += 1


class Analyzer:
    """Looks at every block an output hands to its (not real) device: silent blocks and the arrival of the tone."""

    def __init__(self) -> None:
        self.measure, self.blocks, self.frames, self.silent = False, 0, 0, 0
        self.armed_at: float | None = None
        self.tone_dt: float | None = None

    def arm(self, t: float) -> None:
        self.tone_dt, self.armed_at = None, t

    def feed(self, blk: np.ndarray) -> None:
        t = time.perf_counter()
        pk = float(np.abs(blk).max()) if blk.size else 0.0
        if self.armed_at is not None and self.tone_dt is None and pk > TONE_THRESHOLD:
            self.tone_dt = t - self.armed_at
        if self.measure:
            self.blocks += 1
            self.frames += len(blk)
            self.silent += pk == 0.0


class PullDriver(Paced):
    """The Rust side's stand-in for a device that is not there: pulls the output's frames at 48 kHz of wall time."""

    def __init__(self, bus, oid: str):
        super().__init__(f"bench-drain-{oid}", 0.005)
        self.bus, self.oid, self.done, self.an = bus, oid, 0, Analyzer()
        self.t_start = None

    def tick(self, k: int, t0: float) -> None:
        want = int((time.perf_counter() - t0) * RATE) - self.done
        if want < 1:
            return
        blk = self.bus.pull(self.oid, want)
        self.done += want
        self.an.feed(blk)


# -- the Python bus --------------------------------------------------------------------------------------------------------
class PyEngine:
    def __init__(self, name: str):
        self.name = name
        tmp = tempfile.mkdtemp(prefix="relay-bench-")
        os.environ["RELAY_CONFIG"] = os.path.join(tmp, "config.json")     # keep config and database writes out of the real data dir
        os.environ["RELAY_ANNOUNCE"] = "0"
        sys.path.insert(0, str(REPO))
        from app import audio, audiobus
        self.audio, self.audiobus = audio, audiobus
        if name == "python-nortmixer":
            audio.rtmixer = None
        self.rtmixer_in_use = audio.rtmixer is not None
        self.bus = audiobus.Bus()
        self.sinks: list = []
        self.devices: dict = {}
        self.tmp = tmp
        eng = self

        class Sink(audio.LaneSink):
            """An output that is not a device: a ring a thread drains at exactly 48000 frames per wall second."""
            rate = RATE

            def __init__(self, channels: int) -> None:
                self.channels = channels
                self.size = 8192
                self.buf = np.zeros((self.size, channels), dtype=np.float32)
                self.r = self.w = 0
                self.lock = threading.Lock()
                self._under = 0
                self.up = True
                self.an = Analyzer()
                self.drain = _Drain(f"bench-drain-{len(eng.sinks) + 1}", 0.005)
                self.drain.sink, self.drain.done = self, 0
                eng.sinks.append(self)
                self.drain.start()

            def free(self) -> int:
                return self.size - (self.w - self.r)

            def queued(self) -> int:
                return self.w - self.r

            def write(self, block: np.ndarray) -> int:
                with self.lock:
                    n = min(len(block), self.size - (self.w - self.r))
                    if n:
                        idx = (self.w + np.arange(n)) % self.size
                        self.buf[idx] = block[:n]
                        self.w += n
                    return n

            def take(self, n: int) -> np.ndarray:
                with self.lock:
                    have = self.w - self.r
                    m = min(n, have)
                    out = np.zeros((n, self.channels), dtype=np.float32)
                    if m:
                        out[:m] = self.buf[(self.r + np.arange(m)) % self.size]
                        self.r += m
                    if m < n:
                        self._under += 1
                    return out

            def alive(self) -> bool:
                return self.up

            @property
            def underruns(self) -> int:
                return self._under

            def close(self) -> None:
                self.up = False
                self.drain.stop_ev.set()

        class _Drain(Paced):
            def tick(self, k: int, t0: float) -> None:
                want = int((time.perf_counter() - t0) * RATE) - self.done
                if want < 1:
                    return
                self.done += want
                self.sink.an.feed(self.sink.take(want))

        class FakeOut(audio.AudioOutput):
            id = "fakesink"
            label = "Bench pace sink"

            def lane_sink(self, selector, channels):
                return Sink(channels)

            def play(self, pcm, rate=24000, selector=None):
                pass

        class Synth(audio.AudioSource):
            id = "synth"
            label = "Bench synthetic"

            def open(self, selector, channel, deliver):
                raise audio.SourceError("lane inputs only")

            def lane_input(self):
                return SynthIn()

        class SynthIn(audio.LaneInput):
            def open(self, selector, deliver):
                d = eng.devices[str(selector)]
                d["deliver"] = deliver
                self.dev = d
                return audio.SourceInfo(rate=d["rate"], device={"label": f"synthetic {selector}", "channels": d["channels"]},
                                        channel="all")

            def close(self):
                self.dev["deliver"] = None

        audio.register_source(Synth())
        audio.register_output(FakeOut())

    def start(self, cfg: dict, devices: dict) -> None:
        self.devices = devices
        assert self.audiobus.normalize(cfg) == cfg, "bench config is not what normalize() returns"
        self.bus.apply(cfg)
        if self.rtmixer_in_use:
            self.deaths = {"speaker": [], "mic": []}
            self._watch_stop = threading.Event()
            threading.Thread(target=self._watcher, name="bench-poll-rearm", daemon=True).start()

    def _watcher(self) -> None:
        """rtmixer 0.1.7: a play_ringbuffer / record_ringbuffer action ENDS the first time its ring is empty (output) or full
        (input) at a device callback, and the ring is never touched again; the stream still reports active, so audiobus'
        supervisor (sink.alive() is stream.active) never notices. This thread notes each death and re-arms the action (what a
        restart would do), so the run can go on and the number of deaths is a result. See RESULTS.md."""
        while not self._watch_stop.wait(0.01):
            try:
                o = self.bus._outputs.get("spk")
                sink = o.sink if o else None
                st = getattr(sink, "_stream", None)
                if st is not None and sink._ring is not None and not st.actions:
                    self.deaths["speaker"].append(time.perf_counter())
                    sink._action = st.play_ringbuffer(sink._ring)
                for i in list(self.bus._intakes.values()):
                    li = i.input
                    rec = getattr(li, "_stream", None) if i.source == "portaudio" else None
                    if rec is not None and li._ring is not None and not rec.actions:
                        self.deaths["mic"].append(time.perf_counter())
                        li._action = rec.record_ringbuffer(li._ring)
            except Exception:
                pass

    # -- what the harness needs from an engine
    def status(self) -> dict:
        s = self.bus.status()
        outs = {}
        for o in s["outputs"]:
            outs[o["id"]] = {"state": o["state"], "underruns": o["underruns"], "resyncs": o["resyncs"],
                             "queued_ms": o["queued_ms"], "rms": [lin(m["rms_dbfs"]) for m in o["meters"]],
                             "peak": [lin(m["peak_dbfs"]) for m in o["meters"]]}
        lanes = {l["lane"]: {"state": l["state"], "rms": lin(l["rms_dbfs"])} for l in s["lanes"] if l["patched"]}
        return {"outputs": outs, "lanes": lanes, "input_dropped": sum(i["dropped"] for i in s["inputs"]),
                "all_open": all(l["state"] == "open" for l in lanes.values()) and all(o["state"] == "open" for o in outs.values())}

    def detail(self) -> dict:
        """What the Python bus' status() hides: per-output lane underruns versus the sink's own, and rtmixer's counters."""
        d = {"bus_lane_underruns": {k: o.underruns for k, o in self.bus._outputs.items()},
             "sink_underruns": {k: (o.sink.underruns if o.sink else None) for k, o in self.bus._outputs.items()},
             "rtmixer_in_use": self.rtmixer_in_use}
        if self.rtmixer_in_use:
            d["ring_action_deaths"] = {k: list(v) for k, v in self.deaths.items()}
            for label, stream in (("speaker_output_underflows", getattr(self.bus._outputs.get("spk") and self.bus._outputs["spk"].sink, "_stream", None)),
                                  ("mic_input_overflows", next((getattr(i.input, "_stream", None) for i in self.bus._intakes.values() if i.source == "portaudio"), None))):
                try:
                    act = stream.fetch_and_reset_stats()
                    stream.wait(act)
                    st = act.stats
                    d[label] = int(st.output_underflows if "output" in label else st.input_overflows)
                except Exception as exc:  # pragma: no cover
                    d[label] = f"unavailable: {exc!r}"
        return d

    def analyzers(self) -> dict[str, Analyzer]:
        return {oid: o.sink.an for oid, o in self.bus._outputs.items() if oid != "spk" and o.sink}

    def drains(self) -> list:
        return [o.sink.drain for o in self.bus._outputs.values() if o.sink and hasattr(o.sink, "drain")]

    def reset_device_stats(self) -> None:
        """rtmixer's counters since the stream opened are not wanted: read-and-discard once at the baseline."""
        self.detail()

    def open_tap(self, lane: int):
        return _PyTap(self, lane)

    def inject(self, oid: str, pcm: bytes) -> None:
        self.audiobus.BusOutput(self.bus).play(pcm, TAP_RATE, oid)

    def stop(self) -> None:
        if self.rtmixer_in_use and hasattr(self, "_watch_stop"):
            self._watch_stop.set()
        self.bus.stop()


class _PyTap:
    """A 24 kHz tap the way the speech-to-text path takes one: the lane as an audio source (audiobus.LaneSource), blocks
    queued to a reader that resamples them with soxr to 24 kHz (as AudioCapture's pump does)."""

    def __init__(self, eng: PyEngine, lane: int):
        import queue
        import soxr
        self.q = queue.SimpleQueue()
        self.src = eng.audiobus.LaneSource(eng.bus)
        self.src.open(str(lane), 0, self.q.put)
        self.rs = soxr.ResampleStream(RATE, TAP_RATE, 1, dtype="float32", quality="HQ")
        self.queue = queue

    def read(self, timeout: float):
        try:
            parts = [self.q.get(timeout=timeout)]
        except self.queue.Empty:
            return np.zeros(0, np.float32), 0
        while True:
            try:
                parts.append(self.q.get_nowait())
            except self.queue.Empty:
                break
        return self.rs.resample_chunk(np.concatenate(parts)), 0

    def close(self):
        self.src.close()


# -- the Rust bus ---------------------------------------------------------------------------------------------------------
class RustEngine:
    name = "rust"

    def __init__(self, backend: str = "cpal"):
        import relay_bus
        self.bus = relay_bus.Bus(backend)
        self.pulls: dict[str, PullDriver] = {}

    def start(self, cfg: dict, devices: dict) -> None:
        for key, d in devices.items():
            d["deliver"] = (lambda blk, k=key, r=d["rate"]: self.bus.push(f"synth:{k}", blk, r))
        self.bus.apply(cfg)
        for o in cfg["outputs"]:
            if o["output"] != "portaudio":
                p = PullDriver(self.bus, o["id"])
                self.pulls[o["id"]] = p
                p.start()

    def status(self) -> dict:
        s = self.bus.status()
        outs = {o["id"]: {"state": o["state"], "underruns": o["underruns"], "resyncs": o["resyncs"], "queued_ms": o["queued_ms"],
                          "rms": [m["rms"] for m in o["meters"]], "peak": [m["peak"] for m in o["meters"]]}
                for o in s["outputs"]}
        lanes = {l["lane"]: {"state": l["state"], "rms": l["meter"]["rms"]} for l in s["lanes"] if l["patched"]}
        return {"outputs": outs, "lanes": lanes, "input_dropped": sum(i["dropped"] for i in s["inputs"]),
                "all_open": all(l["state"] == "open" for l in lanes.values()) and all(o["state"] == "open" for o in outs.values()),
                "glitches": s["glitches"], "advisories": s["advisories"], "taps": s["taps"]}

    def detail(self) -> dict:
        s = self.bus.status()
        return {"glitches": s["glitches"], "advisories": s["advisories"], "tap_dropped": {t["id"]: t["dropped"] for t in s["taps"]}}

    def analyzers(self) -> dict[str, Analyzer]:
        return {oid: p.an for oid, p in self.pulls.items()}

    def drains(self) -> list:
        return list(self.pulls.values())

    def reset_device_stats(self) -> None:
        pass

    def open_tap(self, lane: int):
        return _RustTap(self.bus, lane)

    def inject(self, oid: str, pcm: bytes) -> None:
        clip = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        self.bus.inject(oid, [0, 1], clip, TAP_RATE)

    def stop(self) -> None:
        for p in self.pulls.values():
            p.stop_ev.set()
        self.bus.close()


class _RustTap:
    def __init__(self, bus, lane: int):
        self.bus, self.id = bus, bus.open_tap(lane, TAP_RATE, 4000)

    def read(self, timeout: float):
        return self.bus.read_tap(self.id, TAP_RATE, timeout)

    def close(self):
        self.bus.close_tap(self.id)


# -- tap readers ---------------------------------------------------------------------------------------------------------
class TapReader(threading.Thread):
    def __init__(self, engine, lane: int, keep: bool):
        super().__init__(name=f"bench-tap-{lane}", daemon=True)
        self.lane, self.keep, self.stop_ev = lane, keep, threading.Event()
        self.tap = engine.open_tap(lane)
        self.measure = False
        self.samples = self.dropped = 0
        self.sumsq = 0.0
        self.parts: list[np.ndarray] = []
        self.stamps: list[float] = []
        self.t_first = self.t_last = None

    def run(self) -> None:
        while not self.stop_ev.is_set():
            a, d = self.tap.read(0.1)
            now = time.perf_counter()
            if not self.measure:
                continue
            if self.t_first is None:
                self.t_first = now
            self.dropped += d
            if a.size:
                self.samples += a.size
                self.sumsq += float(np.sum(np.square(a, dtype=np.float64)))
                self.stamps.append(now)
                self.t_last = now
                if self.keep:
                    self.parts.append(np.asarray(a, dtype=np.float32))

    def result(self, wall: float) -> dict:
        gaps = [b - a for a, b in zip(self.stamps, self.stamps[1:])]
        res = {"lane": self.lane, "samples": self.samples, "expected": int(wall * TAP_RATE),
               "ratio": self.samples / (wall * TAP_RATE) if wall else None, "dropped": self.dropped,
               "rms_dbfs": db(math.sqrt(self.sumsq / self.samples)) if self.samples else None,
               "max_arrival_gap_ms": 1000 * max(gaps) if gaps else None, "discontinuities": None}
        if self.keep and self.parts and self.lane >= 2:
            x = np.concatenate(self.parts).astype(np.float64)
            w = 2 * math.pi * lane_freq(self.lane) / TAP_RATE
            r = np.abs(x[2:] - 2 * math.cos(w) * x[1:-1] + x[:-2])    # a pure sine obeys x[n] = 2cos(w)x[n-1] - x[n-2]
            bad = np.flatnonzero(r > 0.05 * AMP)
            events = int(1 + np.count_nonzero(np.diff(bad) > 8)) if bad.size else 0
            res["discontinuities"] = events
            res["worst_residual_over_amp"] = float(r.max() / AMP) if r.size else None
        return res


# -- the run --------------------------------------------------------------------------------------------------------------
class Poller(threading.Thread):
    """Once a second: the engine's status (the way the panel polls it), RSS, and the meter readings used for the sanity check."""

    def __init__(self, engine):
        super().__init__(name="bench-poll", daemon=True)
        self.engine, self.stop_ev, self.measure = engine, threading.Event(), False
        self.lane_rms: dict[int, list[float]] = {}
        self.out_rms: dict[str, list[list[float]]] = {}
        self.rss: list[float] = []
        self.status_ms: list[float] = []

    def run(self) -> None:
        while not self.stop_ev.wait(1.0):
            t = time.perf_counter()
            s = self.engine.status()
            self.status_ms.append(1000 * (time.perf_counter() - t))
            if not self.measure:
                continue
            self.rss.append(rss_mb())
            for n, l in s["lanes"].items():
                self.lane_rms.setdefault(n, []).append(l["rms"])
            for oid, o in s["outputs"].items():
                self.out_rms.setdefault(oid, []).append(o["rms"])


class Busy(threading.Thread):
    """A CPU-bound pure-Python thread doing small units of work (a multiply and a modulo) and counting them. Tight by default
    (scenario E, the worst case); with `sleep_s` it works for `work_s` and then sleeps, like a web handler (scenario G)."""

    def __init__(self, work_s: float = 0.0, sleep_s: float = 0.0):
        super().__init__(name="bench-busy", daemon=True)
        self.stop_ev, self.iters, self.work_s, self.sleep_s = threading.Event(), 0, work_s, sleep_s

    def run(self) -> None:
        x = 1
        while not self.stop_ev.is_set():
            until = time.perf_counter() + self.work_s if self.sleep_s else None
            while True:
                for _ in range(500):
                    x = (x * 31 + 7) % 1000003
                self.iters += 500
                if until is None or time.perf_counter() >= until or self.stop_ev.is_set():
                    break
            if self.sleep_s:
                time.sleep(self.sleep_s)


def busy_for(scn: str) -> Busy:
    return Busy(0.002, 0.008) if scn == "G" else Busy()


def busy_rate(seconds: float, scn: str = "E") -> float:
    b = busy_for(scn)
    b.start()
    time.sleep(seconds)
    b.stop_ev.set()
    b.join()
    return b.iters / seconds


def tone_pcm() -> bytes:
    n = int(TAP_RATE * TONE_S)
    amp = 10 ** (TONE_DB / 20)
    x = amp * np.sin(2 * np.pi * TONE_HZ * np.arange(n) / TAP_RATE)
    return np.round(x * 32768.0).astype("<i2").tobytes()


async def timer_loop(seconds: float) -> list[float]:
    late, end = [], time.perf_counter() + seconds
    while time.perf_counter() < end:
        t = time.perf_counter()
        await asyncio.sleep(0.005)
        late.append(time.perf_counter() - t - 0.005)
    return late


def stats(xs, scale=1000.0):
    if not xs:
        return None
    return {"n": len(xs), "p50": scale * pct(xs, 50), "p90": scale * pct(xs, 90), "p99": scale * pct(xs, 99),
            "p999": scale * pct(xs, 99.9), "max": scale * max(xs)}


def run_one(engine_name: str, scn: str, seconds: float, warmup: float, seed: int) -> dict:
    out: dict = {"engine": engine_name, "scenario": scn, "seconds": seconds, "seed": seed}
    cfg = build_config(scn)
    devices = synth_devices(scn)
    # Baselines that need no engine: what a 5 ms timer does in an idle process, and how fast the busy thread runs alone.
    idle_timer = solo_busy = None
    if scn == "D":
        idle_timer = stats(asyncio.run(timer_loop(5.0)))
    if scn in ("E", "G"):
        solo_busy = busy_rate(3.0, scn)
    engine = RustEngine() if engine_name == "rust" else PyEngine(engine_name)
    out["rtmixer_in_use"] = getattr(engine, "rtmixer_in_use", None)
    rss_base = rss_mb()
    out["rss_after_imports_mb"] = rss_base
    driver = Driver(devices)
    t_apply = time.perf_counter()
    engine.start(cfg, devices)
    out["apply_ms"] = 1000 * (time.perf_counter() - t_apply)
    driver.start()
    taps_lanes = [1] if scn == "A" else [1, 2, 10, 18]
    taps = [TapReader(engine, n, keep=(n >= 2)) for n in taps_lanes]
    for t in taps:
        t.start()
    poller = Poller(engine)
    poller.start()
    # wait until every lane and output is open and carrying audio
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 15 and not engine.status()["all_open"]:
        time.sleep(0.1)
    out["ready_s"] = time.perf_counter() - t0
    out["all_open"] = engine.status()["all_open"]
    time.sleep(warmup)
    # -- baseline
    analyzers = engine.analyzers()
    base = engine.status()
    engine.reset_device_stats()
    drains = engine.drains()
    for p in [driver, *drains]:
        p.measuring = True
        p.late.clear()
    for a in analyzers.values():
        a.measure = True
    for t in taps:
        t.measure = True
    poller.measure = True
    groups0 = thread_groups()
    th0 = thread_cpu()
    cpu0, w0, rss0 = cpu_s(), time.perf_counter(), rss_mb()
    ru_peak0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576

    extra: dict = {}
    busy = None
    if scn in ("E", "G"):
        busy = busy_for(scn)
        busy.start()
        b0, bt0 = busy.iters, time.perf_counter()
    if scn == "D":
        late = asyncio.run(timer_loop(seconds))
        extra["timer_late_ms"] = stats(late)
        extra["timer_idle_baseline_ms"] = idle_timer
    elif scn == "C":
        extra.update(run_alerts(engine, analyzers, seconds, seed))
    else:
        time.sleep(seconds)
    # -- end of the measured window
    wall = time.perf_counter() - w0
    cpu1, rss1 = cpu_s(), rss_mb()
    th1 = thread_cpu()
    groups1 = thread_groups()
    if busy is not None:
        busy.stop_ev.set()
        busy_iters = busy.iters - b0
        extra["busy_iters_per_s"] = busy_iters / (time.perf_counter() - bt0)
        extra["busy_solo_iters_per_s"] = solo_busy
        extra["busy_share_of_solo"] = extra["busy_iters_per_s"] / solo_busy
    end = engine.status()
    det = engine.detail()
    groups = {**groups0, **groups1}
    by_group: dict[str, float] = {}
    for tid, (c1, name) in th1.items():
        d = c1 - th0.get(tid, (0.0, name))[0]
        g = groups.get(tid) or ("rust-threads" if name.startswith("relay") else "native-other")
        by_group[g] = by_group.get(g, 0.0) + d
    out["threads_cpu_s"] = {k: round(v, 3) for k, v in sorted(by_group.items(), key=lambda kv: -kv[1])}
    out["threads_cpu_pct"] = {k: round(100 * v / wall, 2) for k, v in by_group.items()}
    out["threads_unaccounted_s"] = round((cpu1 - cpu0) - sum(by_group.values()), 3)   # threads that exited during the window
    out["wall_s"] = wall
    out["cpu_pct"] = 100 * (cpu1 - cpu0) / wall
    out["rss_series_mb"] = [round(rss0, 1)] + [round(x, 1) for x in poller.rss]   # once a second from the window start
    out["rss_mb"] = {"after_imports": rss_base, "window_start": rss0, "window_end": rss1,
                     "window_max_sampled": max(poller.rss) if poller.rss else None,
                     "process_peak": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1048576,
                     "growth_over_window": rss1 - rss0, "bus_over_imports": rss1 - rss_base}
    # -- counters (deltas from the baseline, so the start-up transient is not counted)
    def dsum(key, ids):
        return sum(end["outputs"][i][key] - base["outputs"][i][key] for i in ids if i in end["outputs"])
    fakes = [i for i in end["outputs"] if i != "spk"]
    out["speaker"] = {"underruns": dsum("underruns", ["spk"]), "resyncs": dsum("resyncs", ["spk"]),
                      "queued_ms_end": end["outputs"]["spk"]["queued_ms"]}
    out["fake_outputs"] = {"underruns": dsum("underruns", fakes), "resyncs": dsum("resyncs", fakes),
                           "silent_blocks": sum(a.silent for a in analyzers.values()),
                           "blocks": sum(a.blocks for a in analyzers.values())}
    out["input_dropped"] = end["input_dropped"] - base["input_dropped"]
    deaths = det.pop("ring_action_deaths", None)
    if deaths is not None:
        out["rtmixer_ring_action_deaths"] = {k: {"before_window": sum(t < w0 for t in v), "in_window": sum(w0 <= t <= w0 + wall for t in v)}
                                             for k, v in deaths.items()}
    out["detail_end"] = det
    if engine_name == "rust":
        out["glitches_delta"] = {k: end["glitches"][k] - base["glitches"][k] for k in end["glitches"]}
        out["advisories_delta"] = end["advisories"] - base["advisories"]
    out["totals_since_start"] = {i: {"underruns": end["outputs"][i]["underruns"], "resyncs": end["outputs"][i]["resyncs"]} for i in end["outputs"]}
    out["driver"] = {"late_ms": stats(driver.late), "resets": driver.resets, "errors": driver.errors}
    if drains:
        allf = [x for p in drains for x in p.late]
        out["drain"] = {"late_ms": stats(allf), "resets": sum(p.resets for p in drains)}
    out["status_call_ms"] = {"median": statistics.median(poller.status_ms), "max": max(poller.status_ms)} if poller.status_ms else None
    for t in taps:
        t.stop_ev.set()
    out["taps"] = [t.result(wall) for t in taps]
    # -- meter sanity
    exp_lane = AMP / math.sqrt(2)
    lane_err = []
    for n, xs in poller.lane_rms.items():
        if n >= 2 and xs:
            lane_err.append(db(statistics.fmean(xs)) - db(exp_lane))
    meters = {"lane_expected_dbfs": db(exp_lane), "synthetic_lane_error_db": {"median_abs": statistics.median(abs(e) for e in lane_err),
              "max_abs": max(abs(e) for e in lane_err), "n": len(lane_err)} if lane_err else None}
    mic = taps[0].result(wall)["rms_dbfs"]
    mic_lane = poller.lane_rms.get(1)
    meters["mic_tap_rms_dbfs"] = mic
    meters["mic_lane_meter_rms_dbfs"] = db(math.sqrt(statistics.fmean(x * x for x in mic_lane))) if mic_lane else None
    if scn != "A" and "spk" in poller.out_rms:
        # The Python bus' meters are floored at -60 dBFS (audio.dbfs). The expected level of these outputs is -58.6 dBFS, so
        # the block RMS dips below the floor now and then; those readings are censored, and a mean over them is biased low.
        # The readings are averaged as power (sqrt of the mean of rms squared): the 31 in-phase sines make 10 ms block RMS swing
        # from -17 to +5 dB around the level, so a plain mean of the readings sits about 2.9 dB low (tests/meter_level.rs).
        exp = math.sqrt(31) * 10 ** (-40 / 20) * AMP / math.sqrt(2)
        meters["output_expected_dbfs"] = db(exp)
        meters["output_at_floor_fraction"] = {oid: statistics.fmean(1.0 if r[0] == 0.0 else 0.0 for r in rs) for oid, rs in poller.out_rms.items()}
        meters["output_error_db"] = {oid: (db(math.sqrt(statistics.fmean(r[0] * r[0] for r in rs))) - db(exp)) for oid, rs in poller.out_rms.items()
                                     if meters["output_at_floor_fraction"][oid] < 0.05}
    out["meters"] = meters
    out["extra"] = extra
    out["ru_peak_before_window_mb"] = ru_peak0
    # -- shut down
    poller.stop_ev.set()
    driver.stop_ev.set()
    for t in taps:
        try:
            t.tap.close()
        except Exception:
            pass
    engine.stop()
    return out


def run_isolated(seconds: float, warmup: float) -> dict:
    """Scenario F (Rust only): the engine with nothing of Python's in its audio path. Rust's own fake backend plays a 2 channel
    sine into 32 lanes on the wall clock and drains a 2 channel output on the wall clock, all in Rust threads; Python only reads
    four 24 kHz taps. The first half of the time the process is otherwise idle; in the second half a CPU-bound Python thread
    runs. If Python load could glitch the engine itself, the second half shows it. There is no Python bus counterpart: its
    mixing is Python, so scenario E is its measurement."""
    out: dict = {"engine": "rust", "scenario": "F", "seconds": seconds}
    lanes = [{"lane": n, "name": "", "source": "portaudio", "device": "Fake Mic", "channel": n % 2} for n in range(1, 33)]
    cfg = {"enabled": True, "lanes": lanes,
           "outputs": [{"id": "spk", "name": "Fake speaker", "output": "portaudio", "device": "Fake Speaker", "channels": 2, "latency_ms": LATENCY_MS}],
           "matrix": [{"in": n, "out": "spk", "ch": ch, "gain_db": -40.0} for ch in (1, 2) for n in range(1, 33)]}
    solo = busy_rate(3.0)
    eng = RustEngine("fake")
    eng.start(cfg, {})
    taps = [TapReader(eng, n, keep=False) for n in (1, 2, 3, 4)]
    for t in taps:
        t.start()
    time.sleep(1.0)
    while not eng.status()["all_open"]:
        time.sleep(0.1)
    time.sleep(warmup)
    for t in taps:
        t.measure = True
    halves, busy = [], None
    base = eng.status()
    for half in (0, 1):
        if half == 1:
            busy = Busy()
            busy.start()
        c0, w0 = cpu_s(), time.perf_counter()
        n0 = [t.samples for t in taps]
        g0 = [len(t.stamps) for t in taps]
        b0 = busy.iters if busy else 0
        time.sleep(seconds / 2)
        wall = time.perf_counter() - w0
        st = eng.status()
        gaps = []
        for t, g in zip(taps, g0):
            xs = t.stamps[g:]
            gaps.append(1000 * max((b - a for a, b in zip(xs, xs[1:])), default=0))
        halves.append({"busy_thread": half == 1, "wall_s": wall, "cpu_pct": 100 * (cpu_s() - c0) / wall,
                       "speaker_underruns": st["outputs"]["spk"]["underruns"] - base["outputs"]["spk"]["underruns"],
                       "speaker_resyncs": st["outputs"]["spk"]["resyncs"] - base["outputs"]["spk"]["resyncs"],
                       "input_dropped": st["input_dropped"] - base["input_dropped"],
                       "glitches": {k: st["glitches"][k] - base["glitches"][k] for k in st["glitches"]},
                       "tap_samples_vs_expected": [(t.samples - n) / (wall * TAP_RATE) for t, n in zip(taps, n0)],
                       "tap_dropped": sum(t.dropped for t in taps), "tap_max_arrival_gap_ms": max(gaps),
                       "busy_share_of_solo": ((busy.iters - b0) / wall / solo) if busy else None})
        base = st
    busy.stop_ev.set()
    for t in taps:
        t.stop_ev.set()
    out["halves"] = halves
    out["advisories"] = eng.status()["advisories"]
    eng.stop()
    return out


def run_alerts(engine, analyzers, seconds: float, seed: int) -> dict:
    """20 alert tones at random moments: timed from the inject call to the speaker meter rising, to the speaker meter plus
    the ring it still had to play (Python only), and to the samples reaching the first not-real device."""
    rng = random.Random(seed)
    n = 20
    # random moments with at least 2 s between them, inside [3 s, seconds - 3 s]
    span = seconds - 6 - 2 * (n - 1)
    gaps = sorted(rng.uniform(0, span) for _ in range(n))
    moments = [3 + g + 2 * i for i, g in enumerate(gaps)]
    pcm = tone_pcm()
    an = analyzers.get("f1")
    t_start = time.perf_counter()
    rows = []
    for at in moments:
        delay = t_start + at - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        row = {"at_s": at}
        t0 = time.perf_counter()
        if an is not None:
            an.arm(t0)
        engine.inject("spk", pcm)
        t_call = time.perf_counter()
        engine.inject("f1", pcm)
        row["inject_call_ms"] = 1000 * (t_call - t0)
        rise = qms = peak = None
        while time.perf_counter() - t0 < 1.0:
            s = engine.status()["outputs"]["spk"]
            peak = max([peak or 0.0] + s["peak"])
            if rise is None and max(s["rms"]) > RISE_RMS:
                rise = time.perf_counter() - t0
                qms = s["queued_ms"]
            if rise is not None and an is not None and an.tone_dt is not None and time.perf_counter() - t0 > 0.5:
                break
            time.sleep(0.001)
        row["meter_ms"] = None if rise is None else 1000 * rise
        row["ring_queued_ms_at_rise"] = qms
        # Python's output thread mixes the clip into the ring up to `queued_ms` ahead of the device, so the sound reaches the device
        # about that much after the meter moved. Rust adds the clip to the block the device is filling, after its lane buffers,
        # so there is nothing to add (its queued_ms is the lane buffering, which the clip does not wait behind).
        row["est_device_ms"] = None if rise is None or qms is None or engine.name == "rust" else 1000 * rise + qms
        row["fake_device_ms"] = None if an is None or an.tone_dt is None else 1000 * an.tone_dt
        row["speaker_peak_dbfs"] = db(peak) if peak else None
        rows.append(row)
    return {"alerts": rows}


def summarize_alerts(rows: list[dict]) -> dict:
    out = {}
    for key in ("meter_ms", "est_device_ms", "fake_device_ms", "inject_call_ms"):
        xs = [r[key] for r in rows if r.get(key) is not None]
        out[key] = {"n": len(xs), "median": statistics.median(xs), "p95": pct(xs, 95), "max": max(xs)} if xs else None
        out[key + "_missing"] = len(rows) - len(xs)
    return out


# -- orchestration --------------------------------------------------------------------------------------------------------
def spread(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"median": statistics.median(xs), "min": min(xs), "max": max(xs), "runs": xs}


def get(d, path):
    for p in path.split("."):
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else None
    return d


FLAT = ["cpu_pct", "rss_mb.window_end", "rss_mb.process_peak", "rss_mb.bus_over_imports", "rss_mb.growth_over_window",
        "speaker.underruns", "speaker.resyncs", "rtmixer_ring_action_deaths.speaker.in_window", "rtmixer_ring_action_deaths.speaker.before_window", "rtmixer_ring_action_deaths.mic.in_window", "fake_outputs.underruns", "fake_outputs.resyncs", "fake_outputs.silent_blocks",
        "input_dropped", "driver.late_ms.p99", "driver.late_ms.max", "driver.resets", "drain.late_ms.p99", "drain.late_ms.max",
        "drain.resets", "apply_ms", "status_call_ms.median", "advisories_delta",
        "meters.synthetic_lane_error_db.max_abs", "extra.timer_late_ms.p50", "extra.timer_late_ms.p99", "extra.timer_late_ms.max",
        "extra.timer_late_ms.p999", "extra.busy_share_of_solo", "extra.busy_iters_per_s"]


def summarise(runs: list[dict]) -> dict:
    s = {"runs": len(runs), "metrics": {k: spread([get(r, k) for r in runs]) for k in FLAT}}
    s["metrics"] = {k: v for k, v in s["metrics"].items() if v is not None}
    for g in ("main", "py-bus", "bench-gen", "bench-drain", "bench-tap", "bench-poll", "bench-busy", "rust-threads", "native-other", "py-other"):
        v = spread([get(r, f"threads_cpu_pct.{g}") for r in runs])
        if v:
            s["metrics"][f"threads_cpu_pct.{g}"] = v
    tap = [t for r in runs for t in r["taps"]]
    s["metrics"]["tap.ratio_min"] = spread([min(t["ratio"] for t in r["taps"] if t["ratio"] is not None) for r in runs])
    s["metrics"]["tap.dropped"] = spread([sum(t["dropped"] for t in r["taps"]) for r in runs])
    s["metrics"]["tap.discontinuities"] = spread([sum(t["discontinuities"] or 0 for t in r["taps"]) for r in runs])
    s["metrics"]["tap.max_arrival_gap_ms"] = spread([max(t["max_arrival_gap_ms"] or 0 for t in r["taps"]) for r in runs])
    s["metrics"]["mic_tap_vs_meter_db"] = spread([(r["meters"]["mic_tap_rms_dbfs"] - r["meters"]["mic_lane_meter_rms_dbfs"])
                                                  if r["meters"]["mic_tap_rms_dbfs"] is not None and r["meters"]["mic_lane_meter_rms_dbfs"] is not None else None for r in runs])
    s["metrics"]["output_meter_at_floor_fraction"] = spread([max(r["meters"]["output_at_floor_fraction"].values()) if r["meters"].get("output_at_floor_fraction") else None for r in runs])
    s["metrics"]["output_meter_error_db_max_abs"] = spread([max((abs(e) for e in r["meters"]["output_error_db"].values()), default=None) if r["meters"].get("output_error_db") else None for r in runs])
    s["detail_end"] = [r["detail_end"] for r in runs]
    if runs[0]["extra"].get("alerts"):
        pooled = [a for r in runs for a in r["extra"]["alerts"]]
        s["alerts_pooled"] = summarize_alerts(pooled)
        s["alerts_per_run"] = [summarize_alerts(r["extra"]["alerts"]) for r in runs]
        s["tone_peak_error_db"] = spread([statistics.median(a["speaker_peak_dbfs"] + 20.0 for a in r["extra"]["alerts"] if a["speaker_peak_dbfs"] is not None)
                                          for r in runs])
    return s


def summarise_isolated(runs: list[dict]) -> dict:
    out = {"runs": len(runs), "halves": {}}
    for i, name in enumerate(("idle", "busy")):
        hs = [r["halves"][i] for r in runs]
        out["halves"][name] = {
            "cpu_pct": spread([h["cpu_pct"] for h in hs]), "speaker_underruns": spread([h["speaker_underruns"] for h in hs]),
            "speaker_resyncs": spread([h["speaker_resyncs"] for h in hs]), "input_dropped": spread([h["input_dropped"] for h in hs]),
            "tap_dropped": spread([h["tap_dropped"] for h in hs]),
            "tap_ratio_min": spread([min(h["tap_samples_vs_expected"]) for h in hs]),
            "tap_ratio_max": spread([max(h["tap_samples_vs_expected"]) for h in hs]),
            "tap_max_arrival_gap_ms": spread([h["tap_max_arrival_gap_ms"] for h in hs]),
            "busy_share_of_solo": spread([h["busy_share_of_solo"] for h in hs]),
            "glitches": [h["glitches"] for h in hs]}
    return out


def run_all(args) -> None:
    (RESULTS / "raw").mkdir(parents=True, exist_ok=True)
    engines = args.engines.split(",") if args.engines else list(ENGINES)
    scns = args.scenarios or SCENARIOS
    for scn in scns:
        for eng in engines:
            if scn == "F" and eng != "rust":
                continue
            for rep in range(args.repeats):
                path = RESULTS / "raw" / f"{eng}-{scn}-{rep + 1}.json"
                if path.exists() and not args.force:
                    print(f"skip {path.name} (exists)")
                    continue
                print(f"== {eng} scenario {scn} run {rep + 1}/{args.repeats}", flush=True)
                cmd = [sys.executable, str(Path(__file__)), "--engine", eng, "--scenario", scn, "--seconds", str(args.seconds),
                       "--seed", str(rep + 1), "--json", str(path)]
                try:
                    subprocess.run(cmd, check=True, timeout=args.seconds + 120)
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                    print(f"   FAILED: {exc!r}", flush=True)
                time.sleep(3)
    aggregate()


def aggregate() -> dict:
    summary: dict = {"machine": machine_info()}
    groups: dict = {}
    for f in sorted((RESULTS / "raw").glob("*.json")):
        r = json.loads(f.read_text())
        groups.setdefault((r["engine"], r["scenario"]), []).append(r)
    for (eng, scn), runs in groups.items():
        summary.setdefault(scn, {})[eng] = summarise_isolated(runs) if scn == "F" else summarise(runs)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    print(f"wrote {RESULTS / 'summary.json'}")
    return summary


def sh(*cmd) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception as exc:  # pragma: no cover
        return f"? {exc!r}"


def machine_info() -> dict:
    return {"model": sh("sysctl", "-n", "hw.model"), "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "cores": sh("sysctl", "-n", "hw.ncpu"), "perf_cores": sh("sysctl", "-n", "hw.perflevel0.logicalcpu"),
            "efficiency_cores": sh("sysctl", "-n", "hw.perflevel1.logicalcpu"),
            "memory_gb": int(sh("sysctl", "-n", "hw.memsize") or 0) / 2 ** 30, "macos": sh("sw_vers", "-productVersion"),
            "build": sh("sw_vers", "-buildVersion"), "python": sys.version.split()[0],
            "numpy": np.__version__, "date": time.strftime("%Y-%m-%d %H:%M"),
            "power": sh("pmset", "-g", "batt").splitlines()[0] if sh("pmset", "-g", "batt") else "?",
            "low_power_mode": sh("sh", "-c", "pmset -g | grep -i lowpowermode || echo 'lowpowermode (not listed)'"),
            "load_avg_at_start": os.getloadavg()}


def fmt(v, nd=1):
    if v is None:
        return "n/a"
    m = v["median"]
    if v["min"] == v["max"]:
        return f"{m:.{nd}f}"
    return f"{m:.{nd}f} ({v['min']:.{nd}f}-{v['max']:.{nd}f})"


def report() -> None:
    s = json.loads((RESULTS / "summary.json").read_text())
    rows = [("CPU, % of one core", "cpu_pct", 1), ("RSS at end, MB", "rss_mb.window_end", 0), ("RSS growth over the window, MB", "rss_mb.growth_over_window", 1),
            ("Speaker underruns", "speaker.underruns", 0), ("Speaker resyncs", "speaker.resyncs", 0),
            ("Fake outputs underruns", "fake_outputs.underruns", 0), ("Fake outputs resyncs", "fake_outputs.resyncs", 0),
            ("Fake outputs silent blocks", "fake_outputs.silent_blocks", 0), ("Input frames dropped", "input_dropped", 0),
            ("Taps: fewest samples vs expected", "tap.ratio_min", 4), ("Taps: dropped", "tap.dropped", 0),
            ("Taps: discontinuities (synthetic taps)", "tap.discontinuities", 0), ("Tap max arrival gap, ms", "tap.max_arrival_gap_ms", 0),
            ("Input driver lateness p99, ms", "driver.late_ms.p99", 1), ("Input driver lateness max, ms", "driver.late_ms.max", 1)]
    for scn in SCENARIOS:
        if scn not in s:
            continue
        engs = [e for e in ENGINES if e in s[scn]]
        print(f"\n### Scenario {scn}\n\n| Metric | " + " | ".join(engs) + " |\n|---|" + "---|" * len(engs))
        for label, key, nd in rows:
            vals = [fmt(s[scn][e]["metrics"].get(key), nd) for e in engs]
            if all(v == "n/a" for v in vals):
                continue
            print(f"| {label} | " + " | ".join(vals) + " |")
        for e in engs:
            if "alerts_pooled" in s[scn][e]:
                print(f"\n{e} alerts (pooled over runs): " + json.dumps(s[scn][e]["alerts_pooled"], default=float))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=ENGINES)
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--warmup", type=float, default=5.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--json", help="write this run's result here")
    ap.add_argument("--all", action="store_true", help="every engine x scenario x --repeats, into results/")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--engines", help="with --all: comma list (default: all three)")
    ap.add_argument("--scenarios", help="with --all: letters (default ABCDE)")
    ap.add_argument("--force", action="store_true", help="with --all: rerun runs that already have a result file")
    ap.add_argument("--aggregate", action="store_true", help="rebuild results/summary.json from results/raw")
    ap.add_argument("--report", action="store_true", help="print markdown tables from results/summary.json")
    args = ap.parse_args()
    if args.all:
        run_all(args)
        return 0
    if args.aggregate:
        aggregate()
        return 0
    if args.report:
        report()
        return 0
    if not (args.engine and args.scenario):
        ap.error("--engine and --scenario are required (or --all / --aggregate / --report)")
    if args.scenario == "F":
        if args.engine != "rust":
            ap.error("scenario F is Rust only")
        res = run_isolated(args.seconds, args.warmup)
        res["machine"] = machine_info()
        if args.json:
            Path(args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.json).write_text(json.dumps(res, indent=1, default=float))
        print(json.dumps(res["halves"], indent=1, default=float))
        return 0
    res = run_one(args.engine, args.scenario, args.seconds, args.warmup, args.seed)
    res["machine"] = machine_info()
    if args.scenario == "C":
        res["extra"]["alerts_summary"] = summarize_alerts(res["extra"]["alerts"])
    text = json.dumps(res, indent=1, default=float)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text)
    print(json.dumps({k: res[k] for k in ("engine", "scenario", "cpu_pct", "speaker", "fake_outputs", "input_dropped", "taps", "threads_cpu_pct", "rss_mb", "meters")}, indent=1, default=float))
    if res["extra"]:
        print(json.dumps({k: v for k, v in res["extra"].items() if k != "alerts"}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
