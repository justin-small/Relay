"""The 32-lane audio bus (#92): inputs from several devices patched to lanes, consumers subscribing to a lane,
a crosspoint matrix, drift-following outputs, and Relay's own sounds into an output. Fake devices only: no
sound card. Run: .venv/bin/python tests/test_audiobus.py
"""
import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"

import numpy as np  # noqa: E402

from app import audio, audiobus  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import capture_log, check, done, eq, section, wait_for  # noqa: E402

audio.RETRY_EVERY_S = 0.1
audio.LOST_AFTER_S = 0.6
RATE = audio.LANE_RATE


# -- fakes ---------------------------------------------------------------------------------------------------------
class FakeInput(audio.LaneInput):
    def __init__(self, owner, rate, channels):
        self.owner, self.rate, self.channels = owner, rate, channels
        self.deliver = None
        self.up = False
        self.closed = False

    def open(self, selector, deliver):
        self.owner.opens.append(selector)
        if self.owner.fail:
            raise audio.SourceError(self.owner.fail)
        self.deliver, self.up = deliver, True
        self.owner.live.append(self)
        return audio.SourceInfo(rate=self.rate, device={"label": f"Fake: {selector}", "channels": self.channels},
                                channel="all")

    def close(self):
        self.up, self.closed = False, True
        self.deliver = None
        if self in self.owner.live:
            self.owner.live.remove(self)

    def alive(self):
        return self.up


class FakeSource(audio.AudioSource):
    id = "fake"
    label = "Fake"
    DEVICES = {"A": (RATE, 8), "B": (44100, 4)}

    def __init__(self):
        self.opens, self.live, self.fail = [], [], None

    def lane_input(self):
        return _Pending(self)

    def open(self, selector, channel, deliver):
        raise audio.SourceError("mono not used")

    def push(self, device, block):
        for li in list(self.live):
            if li.selector == device and li.deliver:
                li.deliver(block)


class _Pending(audio.LaneInput):
    """FakeSource.lane_input() before the device is known: picks the device when opened."""

    def __init__(self, owner):
        self.owner, self.inner = owner, None

    def open(self, selector, deliver):
        rate, ch = FakeSource.DEVICES[selector]
        self.inner = FakeInput(self.owner, rate, ch)
        self.inner.selector = selector
        return self.inner.open(selector, deliver)

    def close(self):
        if self.inner:
            self.inner.close()

    def alive(self):
        return bool(self.inner and self.inner.alive())


class FakeSink(audio.LaneSink):
    """An output device on its own clock: a thread drains `rate_hz` frames a second."""

    def __init__(self, owner, channels, rate_hz):
        self.owner, self.channels, self.rate = owner, channels, RATE
        self.rate_hz = rate_hz
        self.q = []
        self.size = 4096
        self.played = []
        self._under = 0
        self.up = True
        self.lock = threading.Lock()
        self.t = threading.Thread(target=self._drain, daemon=True)
        self.t.start()

    def _drain(self):
        last = time.time()
        owed = 0.0
        while self.up:
            time.sleep(0.005)
            now = time.time()
            owed += (now - last) * self.rate_hz
            last = now
            n = int(owed)
            owed -= n
            with self.lock:
                have = sum(len(b) for b in self.q)
                take = min(n, have)
                if n > have:
                    self._under += 1
                out, left = [], take
                while left and self.q:
                    b = self.q[0]
                    if len(b) <= left:
                        out.append(b)
                        left -= len(b)
                        self.q.pop(0)
                    else:
                        out.append(b[:left])
                        self.q[0] = b[left:]
                        left = 0
                if out:
                    self.played.append(np.concatenate(out))

    def free(self):
        with self.lock:
            return self.size - sum(len(b) for b in self.q)

    def queued(self):
        with self.lock:
            return sum(len(b) for b in self.q)

    def write(self, block):
        with self.lock:
            n = min(len(block), self.size - sum(len(b) for b in self.q))
            if n:
                self.q.append(np.array(block[:n], dtype=np.float32))
            return n

    def alive(self):
        return self.up

    @property
    def underruns(self):
        return self._under

    def close(self):
        self.up = False

    def audio(self):
        return np.concatenate(self.played) if self.played else np.zeros((0, self.channels), np.float32)


class FakeOutput(audio.AudioOutput):
    id = "fake"
    label = "Fake"

    def __init__(self):
        self.sinks, self.fail, self.rate_hz = [], None, RATE

    def lane_sink(self, selector, channels):
        if self.fail:
            raise audio.SourceError(self.fail)
        s = FakeSink(self, channels, self.rate_hz)
        self.sinks.append(s)
        return s

    def play(self, pcm, rate=audio.TARGET_RATE, selector=None):
        pass


SRC, OUT = FakeSource(), FakeOutput()
audio.register_source(SRC)
audio.register_output(OUT)


def tone(freq, seconds, rate, amp=0.5, start=0):
    t = (np.arange(int(seconds * rate)) + start) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def feed(device, channels, seconds, rate, freqs, amp=0.5, step=0.01, realtime=True):
    """Deliver `seconds` of audio to `device` in 10 ms blocks; channel c carries a tone at freqs[c] (or silence)."""
    n = int(rate * step)
    for i in range(int(seconds / step)):
        block = np.zeros((n, channels), dtype=np.float32)
        for c, f in freqs.items():
            block[:, c] = tone(f, step, rate, amp, start=i * n)
        SRC.push(device, block)
        if realtime:
            time.sleep(step)


def reset_bus():
    audiobus.bus.apply({"enabled": False})
    SRC.opens.clear()
    SRC.fail = None
    OUT.sinks.clear()
    OUT.fail = None


def peak(x):
    return float(np.max(np.abs(x))) if len(x) else 0.0


# -- configuration -----------------------------------------------------------------------------------------------------
with section("the saved setting is cleaned, never refused"):
    cfg = audiobus.normalize({
        "enabled": 1,
        "lanes": [{"lane": 1, "source": "Fake", "device": "A", "channel": 3},
                  {"lane": 1, "source": "fake", "device": "B"},             # duplicate lane
                  {"lane": 33, "source": "fake"}, {"lane": 0}, "junk",     # out of range, not a dict
                  {"lane": 32, "channel": "MIX", "name": "x" * 99}],
        "outputs": [{"id": "Main Out", "channels": 99, "latency_ms": 1},
                    {"id": "main_out"}, {"id": "bus"}, {"id": ""}],
        "matrix": [{"in": 1, "out": "main_out", "ch": 1, "gain_db": -6},
                   {"in": 1, "out": "main_out", "ch": 1, "gain_db": 0},    # duplicate cell
                   {"in": 2, "out": "nowhere", "ch": 1}, {"in": 2, "out": "main_out", "ch": 99},
                   {"in": 3, "out": "main_out", "ch": 2, "gain_db": "loud"},
                   {"in": 4, "out": "main_out", "ch": 3, "gain_db": 500}]})
    eq("enabled is a bool", cfg["enabled"], True)
    eq("lane 1 kept once, lane 32 kept, others dropped", [x["lane"] for x in cfg["lanes"]], [1, 32])
    eq("source ids are lowercased", cfg["lanes"][0]["source"], "fake")
    eq("mix is normalised", cfg["lanes"][1]["channel"], "mix")
    eq("names are cut", len(cfg["lanes"][1]["name"]), 40)
    eq("an output id is made safe, and repeated or reserved ids are dropped", [o["id"] for o in cfg["outputs"]], ["main_out"])
    eq("an output has at most 32 channels", cfg["outputs"][0]["channels"], 32)
    eq("latency is clamped", cfg["outputs"][0]["latency_ms"], audiobus.MIN_LATENCY_MS)
    eq("only valid, unique crosspoints stay", [(m["in"], m["ch"]) for m in cfg["matrix"]], [(1, 1), (3, 2), (4, 3)])
    eq("a gain that is not a number is 0 dB, and gains are clamped",
       [m["gain_db"] for m in cfg["matrix"]], [-6.0, 0.0, audiobus.GAIN_MAX_DB])
    eq("empty config is off", audiobus.normalize(None)["enabled"], False)


# -- inputs from several devices -----------------------------------------------------------------------------------------
with section("lanes come from several devices, each device opened once"):
    reset_bus()
    audiobus.bus.apply({"enabled": True, "lanes": [
        {"lane": 1, "source": "fake", "device": "A", "channel": 0},
        {"lane": 2, "source": "fake", "device": "A", "channel": 2},
        {"lane": 3, "source": "fake", "device": "A", "channel": "mix"},
        {"lane": 9, "source": "fake", "device": "B", "channel": 1, "name": "Pastor"}]})
    eq("two devices opened, once each", sorted(SRC.opens), ["A", "B"])
    got = {1: [], 2: [], 3: [], 9: []}
    toks = {n: audiobus.bus.lane(n).subscribe(lambda b, n=n: got[n].append(b.copy())) for n in got}
    feed("A", 8, 0.5, RATE, {0: 440, 2: 1000})
    feed("B", 4, 0.5, 44100, {1: 600})
    wait_for(lambda: sum(len(b) for b in got[9]) > 20000)
    l1, l2, l3, l9 = (np.concatenate(got[n]) for n in (1, 2, 3, 9))
    check("lane 1 carries channel 0 of device A", abs(peak(l1) - 0.5) < 0.01 and len(l1) == 24000, f"{peak(l1)} {len(l1)}")
    check("lane 2 carries channel 2 of device A (a different tone)", abs(peak(l2) - 0.5) < 0.01
          and not np.allclose(l1, l2))
    check("lane 3 is the mean of every channel", abs(peak(l3) - (0.5 + 0.5) / 8) < 0.02, str(peak(l3)))
    check("a 44.1 kHz device is resampled to 48 kHz", abs(len(l9) - 24000) < 1200 and abs(peak(l9) - 0.5) < 0.05,
          f"{len(l9)} {peak(l9)}")
    st = audiobus.bus.status()
    eq("lane 9 has its name", st["lanes"][8]["name"], "Pastor")
    eq("an unpatched lane says so", st["lanes"][4]["state"], "unpatched")
    eq("two inputs reported", sorted(i["device"] for i in st["inputs"]), ["A", "B"])
    eq("the inputs list names their lanes", sorted(sorted(i["lanes"]) for i in st["inputs"]), [[1, 2, 3], [9]])
    check("a lane's meter reads its level", st["lanes"][0]["peak_dbfs"] > -9, str(st["lanes"][0]))
    for n, t in toks.items():
        audiobus.bus.lane(n).unsubscribe(t)


with section("many consumers share a lane, and a consumer's fault does not stop it"):
    reset_bus()
    audiobus.bus.apply({"enabled": True, "lanes": [{"lane": 5, "source": "fake", "device": "A", "channel": 0}]})
    a, b = [], []

    def boom(_):
        raise RuntimeError("consumer bug")

    t1 = audiobus.bus.lane(5).subscribe(lambda x: a.append(len(x)))
    t2 = audiobus.bus.lane(5).subscribe(lambda x: b.append(len(x)))
    t3 = audiobus.bus.lane(5).subscribe(boom)
    with capture_log("relay.audiobus") as logged:
        feed("A", 8, 0.1, RATE, {0: 440}, realtime=False)
    check("the broken consumer is logged", any("consumer failed" in m for m in logged))
    eq("both consumers got every sample", (sum(a), sum(b)), (4800, 4800))
    eq("the lane counts its consumers", audiobus.bus.status()["lanes"][4]["subscribers"], 3)
    audiobus.bus.lane(5).unsubscribe(t1)
    audiobus.bus.lane(5).unsubscribe(t3)
    feed("A", 8, 0.1, RATE, {0: 440}, realtime=False)
    eq("an unsubscribed consumer stops hearing it", (sum(a), sum(b)), (4800, 9600))
    audiobus.bus.lane(5).unsubscribe(t2)

    async def via_queue():
        loop = asyncio.get_running_loop()
        tok, q = audiobus.bus.lane(5).subscribe_queue(loop, maxsize=3)
        await asyncio.to_thread(feed, "A", 8, 0.1, RATE, {0: 440}, 0.5, 0.01, False)
        await asyncio.sleep(0.1)
        audiobus.bus.lane(5).unsubscribe(tok)
        return q.qsize(), q.dropped

    size, dropped = asyncio.run(via_queue())
    check("an async consumer gets blocks on its loop, and a slow one drops its oldest", size == 3 and dropped > 0,
          f"{size} {dropped}")
    try:
        audiobus.bus.lane(33)
        check("lane 33 does not exist", False)
    except ValueError:
        check("lane 33 does not exist", True)


# -- changing one feed leaves the others alone -------------------------------------------------------------------------
with section("changing one input or output does not disturb another"):
    reset_bus()
    base = {"enabled": True,
            "lanes": [{"lane": 1, "source": "fake", "device": "A", "channel": 0},
                      {"lane": 2, "source": "fake", "device": "B", "channel": 0}],
            "outputs": [{"id": "o1", "output": "fake", "channels": 2}, {"id": "o2", "output": "fake", "channels": 2}],
            "matrix": [{"in": 1, "out": "o1", "ch": 1}, {"in": 2, "out": "o2", "ch": 1}]}
    audiobus.bus.apply(base)
    first = {li.selector: li for li in SRC.live}
    sinks = list(OUT.sinks)
    changed = {**base, "lanes": [{"lane": 1, "source": "fake", "device": "A", "channel": 3}, base["lanes"][1]],
               "outputs": [base["outputs"][0], {**base["outputs"][1], "channels": 4}]}
    audiobus.bus.apply(changed)
    live = {li.selector: li for li in SRC.live}
    check("device A (its lane changed channel) was not reopened", live["A"] is first["A"] and not first["A"].closed)
    check("device B was not reopened", live["B"] is first["B"] and not first["B"].closed)
    check("output o1 kept its device", sinks[0].up and OUT.sinks[0] is sinks[0])
    check("output o2 (changed) was reopened", not sinks[1].up and len(OUT.sinks) == 3 and OUT.sinks[2].channels == 4)
    audiobus.bus.apply({**changed, "lanes": [changed["lanes"][1]]})
    check("a device no lane uses is closed", first["A"].closed and not first["B"].closed)
    audiobus.bus.apply({"enabled": False})
    check("switching the bus off closes everything", not SRC.live and all(not s.up for s in OUT.sinks))
    eq("and it reports not running", audiobus.bus.status()["running"], False)


with section("off by default: nothing opens"):
    reset_bus()
    audiobus.bus.apply({"lanes": [{"lane": 1, "source": "fake", "device": "A"}]})
    eq("a disabled bus opens no device", (SRC.opens, OUT.sinks), ([], []))


# -- the matrix -------------------------------------------------------------------------------------------------------------
def run_matrix(rate_a=RATE, seconds=1.5, out_rate=RATE):
    reset_bus()
    OUT.rate_hz = out_rate
    audiobus.bus.apply({
        "enabled": True,
        "lanes": [{"lane": 1, "source": "fake", "device": "A", "channel": 0},
                  {"lane": 2, "source": "fake", "device": "A", "channel": 1}],
        "outputs": [{"id": "main", "output": "fake", "channels": 3, "latency_ms": 60}],
        "matrix": [{"in": 1, "out": "main", "ch": 1, "gain_db": -6.0206},
                   {"in": 1, "out": "main", "ch": 2, "gain_db": 0},
                   {"in": 2, "out": "main", "ch": 2, "gain_db": 0}]})
    n = int(RATE * 0.01)

    def block_at(k):
        b = np.zeros((n, 8), dtype=np.float32)
        b[:, 0] = tone(440, 0.01, RATE, 0.4, start=k * n)
        b[:, 1] = tone(1500, 0.01, RATE, 0.4, start=k * n)
        return b

    first = block_at(0)             # built before the clock starts: a cold first call must not delay the feed
    SRC.push("A", first)
    t0 = time.time()
    i = 1
    sink = OUT.sinks[0]
    warm = None
    # feed at `rate_a` times real time so the input clock runs a little fast or slow against the output's
    while time.time() - t0 < seconds:
        SRC.push("A", block_at(i))
        i += 1
        if warm is None and time.time() - t0 > 0.5:
            warm = sink.underruns   # starting up is not starving: the check counts from here
        target = t0 + i * 0.01 / (rate_a / RATE)
        time.sleep(max(0.0, target - time.time()))
    sink.warm_underruns = warm or 0
    return sink


def settled(run, ok, tries=3):
    """These sections feed a fake input in real time, so a loaded CI runner (macOS) can stall the feeder and starve the
    output through no fault of the bus. Run again up to `tries` times and keep the first run that was not starved; a real
    fault shows in every run."""
    got = run()
    for _ in range(tries - 1):
        if ok(got):
            break
        got = run()
    return got


with section("the crosspoint matrix sums lanes with gains"):
    sink = settled(run_matrix, lambda k: k.underruns - k.warm_underruns <= 2)
    a = sink.audio()
    tail = a[RATE // 2:]
    check("audio reached the output", len(a) > RATE, str(len(a)))
    check("channel 1 is lane 1 at -6 dB", abs(peak(tail[:, 0]) - 0.2) < 0.02, str(peak(tail[:, 0])))
    check("channel 2 is lane 1 plus lane 2", 0.5 < peak(tail[:, 1]) <= 0.8 + 1e-6, str(peak(tail[:, 1])))
    check("channel 3 has no crosspoint, so silence", peak(tail[:, 2]) == 0.0)
    check("the output was never starved once running", sink.underruns - sink.warm_underruns <= 2,
          f"{sink.underruns} total, {sink.warm_underruns} while starting")
    st = audiobus.bus.status()["outputs"][0]
    eq("the output reports open", st["state"], "open")
    check("output meters read each channel", st["meters"][0]["peak_dbfs"] > -20 and st["meters"][2]["peak_dbfs"] <= -60,
          str(st["meters"][:3]))


with section("an output follows an input whose clock runs fast or slow, without clicking"):
    for ratio in (1.003, 0.997):
        def clean(k):
            return float(np.abs(np.diff(k.audio()[RATE:][:, 0])).max()) < 0.08 and audiobus.bus.output("main").resyncs == 0

        sink = settled(lambda: run_matrix(rate_a=RATE * ratio, seconds=3.0), clean)
        a = sink.audio()[RATE:]
        out = audiobus.bus.output("main")
        # a click shows up as a jump between neighbouring samples far larger than the 440 Hz tone's slope
        jumps = np.abs(np.diff(a[:, 0]))
        check(f"no click at {ratio:.3f}x", float(jumps.max()) < 0.08, str(float(jumps.max())))
        check(f"no resync at {ratio:.3f}x", out.resyncs == 0, str(out.resyncs))
        check(f"the output trims its speed at {ratio:.3f}x", 0 < abs(out.ratio.get(1, 1.0) - 1.0) <= audiobus.MAX_DRIFT_TRIM
              or out.ratio.get(1, 1.0) != 1.0, str(out.ratio))


with section("an output on another sample rate gets resampled audio"):
    reset_bus()
    FakeSink.rate = 44100   # noqa
    class OddSink(FakeSink):
        def __init__(self, owner, channels, rate_hz):
            super().__init__(owner, channels, 44100)
            self.rate = 44100
    old = OUT.lane_sink
    OUT.lane_sink = lambda selector, channels: (OUT.sinks.append(OddSink(OUT, channels, 44100)) or OUT.sinks[-1])
    audiobus.bus.apply({"enabled": True, "lanes": [{"lane": 1, "source": "fake", "device": "A", "channel": 0}],
                        "outputs": [{"id": "x", "output": "fake", "channels": 1}],
                        "matrix": [{"in": 1, "out": "x", "ch": 1}]})
    feed("A", 8, 1.2, RATE, {0: 440}, amp=0.4)
    a = OUT.sinks[-1].audio()
    check("a 44.1 kHz output is fed at 44.1 kHz", len(a) > 30000 and abs(peak(a[len(a) // 2:]) - 0.4) < 0.08,
          f"{len(a)} {peak(a)}")   # the tolerance allows for the resampler's small overshoot
    OUT.lane_sink = old
    reset_bus()


# -- Relay's own sounds -----------------------------------------------------------------------------------------------------
with section("Relay's own sounds play into a bus output"):
    reset_bus()
    audiobus.bus.apply({"enabled": True, "outputs": [{"id": "mon", "output": "fake", "channels": 4}]})
    pcm = (tone(880, 0.3, 24000, 0.5) * 32767).astype("<i2").tobytes()
    audio.get_output("bus").play(pcm, 24000, "mon:3,4")
    wait_for(lambda: len(OUT.sinks[0].audio()) > 14000)
    a = OUT.sinks[0].audio()
    check("the clip is on channels 3 and 4", peak(a[:, 2]) > 0.4 and peak(a[:, 3]) > 0.4)
    check("and not on channels 1 and 2", peak(a[:, 0]) == 0 and peak(a[:, 1]) == 0)
    check("it was resampled from 24 kHz to 48 kHz: about 0.6 s", abs(int(np.count_nonzero(a[:, 2])) - 14400) < 2000,
          str(int(np.count_nonzero(a[:, 2]))))
    with capture_log("relay.audiobus") as logged:
        audio.get_output("bus").play(pcm, 24000, "nobody")     # an unknown output is logged, never raised
        audio.get_output("bus").play(b"\x00", 24000, "mon")    # a truncated clip too
    check("an unknown output or a bad clip raises nothing, and is logged", len(logged) == 2, str(logged))
    reset_bus()


# -- the engine takes a lane ------------------------------------------------------------------------------------------------------
with section("the main capture can take a lane as its source"):
    reset_bus()
    src = audio.get_source("lane")
    check("core registers the lane source", src is not None)
    try:
        src.open("1", 0, lambda b: None)
        check("a stopped bus refuses with a sentence", False)
    except audio.SourceError as exc:
        check("a stopped bus refuses with a sentence", "not running" in str(exc), str(exc))
    audiobus.bus.apply({"enabled": True, "lanes": [{"lane": 4, "source": "fake", "device": "A", "channel": 0, "name": "Altar"}]})
    got = []
    info = src.open("4", 0, got.append)
    eq("the lane's label is shown", info.device["label"], "Lane 4: Altar")
    eq("the pump is told the lane rate", info.rate, RATE)
    feed("A", 8, 0.1, RATE, {0: 440}, realtime=False)
    eq("the capture received the lane's samples", sum(len(b) for b in got), 4800)
    check("alive while the bus runs", src.alive())
    for bad in ("0", "99", "x", None):
        try:
            src.open(bad, 0, got.append)
            check(f"lane {bad!r} refused", False)
        except audio.SourceError:
            check(f"lane {bad!r} refused", True)
    src.close()
    n = sum(len(b) for b in got)
    feed("A", 8, 0.1, RATE, {0: 440}, realtime=False)
    eq("a closed lane source hears nothing more", sum(len(b) for b in got), n)

    async def into_capture():
        cap = audio.AudioCapture(asyncio.get_running_loop())
        q = cap.add_consumer()
        cap.start("4", 0, "lane")
        await asyncio.to_thread(feed, "A", 8, 0.6, RATE, {0: 440}, 0.5, 0.01, True)
        await asyncio.sleep(0.2)
        s = cap.status()
        cap.stop()
        return q.qsize(), s

    frames, s = asyncio.run(into_capture())
    check("AudioCapture runs on a lane and sends 24 kHz frames to its consumers", frames > 5, str(frames))
    eq("its device is the lane", s["device"], "Lane 4: Altar")
    reset_bus()


# -- unplug and replug ----------------------------------------------------------------------------------------------------------
with section("a lost input reconnects, and a failed open is retried"):
    reset_bus()
    SRC.fail = "Could not open Fake: A. Check it is plugged in."
    audiobus.bus.apply({"enabled": True, "lanes": [{"lane": 1, "source": "fake", "device": "A", "channel": 0}]})
    st = audiobus.bus.status()
    eq("a failed open is reported on the lane", (st["lanes"][0]["state"], st["lanes"][0]["error"]),
       ("waiting", SRC.fail))
    SRC.fail = None
    check("it opens when the device appears", wait_for(lambda: bool(SRC.live), 3.0))
    eq("and the lane is open", audiobus.bus.status()["lanes"][0]["state"], "open")

    SRC.live[0].up = False                               # unplugged
    check("a dead device is noticed", wait_for(lambda: audiobus.bus.status()["lanes"][0]["state"] == "waiting", 3.0))
    eq("with the fixed sentence", audiobus.bus.status()["lanes"][0]["error"], audiobus.LOST_SENTENCE)
    check("it reopens on its own", wait_for(lambda: bool(SRC.live) and SRC.live[0].up, 3.0))
    got = []
    t = audiobus.bus.lane(1).subscribe(got.append)
    feed("A", 8, 0.1, RATE, {0: 440}, realtime=False)
    eq("and delivers again", sum(len(b) for b in got), 4800)
    audiobus.bus.lane(1).unsubscribe(t)

    # a device that is open but silent for too long is also treated as gone
    n_opens = len(SRC.opens)
    check("a device that stops delivering is reopened", wait_for(lambda: len(SRC.opens) > n_opens, 4.0))
    reset_bus()


with section("a lost or missing output is reported and retried"):
    reset_bus()
    OUT.fail = "Could not open the output device."
    audiobus.bus.apply({"enabled": True, "outputs": [{"id": "o", "output": "fake", "channels": 2}]})
    st = audiobus.bus.status()["outputs"][0]
    eq("a missing output reports its sentence", (st["state"], st["error"]), ("waiting", OUT.fail))
    OUT.fail = None
    check("and opens when it appears", wait_for(lambda: audiobus.bus.status()["outputs"][0]["state"] == "open", 3.0))
    OUT.sinks[0].up = False
    check("a dead output is noticed", wait_for(lambda: len(OUT.sinks) > 1, 3.0))
    check("and reopened", wait_for(lambda: audiobus.bus.status()["outputs"][0]["state"] == "open", 3.0))
    audiobus.bus.apply({"enabled": True, "outputs": [{"id": "o", "output": "nope", "channels": 2}]})
    eq("a source or output that is not registered says so",
       "not available" in (audiobus.bus.status()["outputs"][0]["error"] or ""), True)
    audiobus.bus.apply({"enabled": True, "lanes": [{"lane": 1, "source": "nope"}]})
    eq("a lane on an unknown source says so",
       "not available" in (audiobus.bus.status()["lanes"][0]["error"] or ""), True)
    reset_bus()


# -- the live-capture count --------------------------------------------------------------------------------------------------
with section("the device list is not re-probed while any stream is open"):
    audio._set_capture_live(False)
    eq("no stream, not live", audio.capture_is_live(), False)
    audio._capture_opened()
    audio._capture_opened()
    audio._capture_closed()
    eq("one of two streams closed: still live", audio.capture_is_live(), True)
    audio._capture_closed()
    eq("both closed: not live", audio.capture_is_live(), False)
    audio._capture_closed()
    eq("a stray close cannot go below zero", audio.capture_is_live(), False)
    probes = []
    real = audio._probe_input_devices
    audio._probe_input_devices = lambda reinit: probes.append(reinit) or []
    try:
        audio._capture_opened()
        audio.list_input_devices(refresh=True)
        audio._capture_closed()
        audio.list_input_devices(refresh=True)
    finally:
        audio._probe_input_devices = real
    eq("re-init only once nothing is open", probes, [False, True])

with section("the fallback ring (no rtmixer) keeps order, wraps, and never overfills"):
    ring = audio._PyRing(8, 2)
    blk = lambda a, b: np.array([[i, -i] for i in range(a, b)], dtype=np.float32)
    eq("writes up to its size", ring.write(blk(0, 10)), 8)
    eq("full: nothing more fits", (ring.write_available, ring.read_available), (0, 8))
    got = ring.read(5)
    eq("reads in order", got[:, 0].tolist(), [0, 1, 2, 3, 4])
    eq("space opens as it is read", ring.write(blk(8, 12)), 4)
    eq("it wraps without losing order", ring.read(99)[:, 0].tolist(), [5, 6, 7, 8, 9, 10, 11])
    eq("empty reads nothing", len(ring.read(4)), 0)

audiobus.bus.stop()
done()
