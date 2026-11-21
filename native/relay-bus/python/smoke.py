"""Smoke test of the Rust audio bus on this machine's real devices (issue 599, phase 2).

    python smoke.py                  # real devices (cpal): the default input and the default output
    python smoke.py --backend fake   # no sound card needed; runs the same steps on the fake backend
    python smoke.py --seconds 10     # shorter soak

What it does, in order:
  1. lists the audio devices;
  2. opens the default input as lane 1 (channel 0) and the default output as a 2 channel output, with NO matrix cell
     from lane 1 to the speaker (so the microphone can never feed the speaker), opens a 24 kHz tap on lane 1 and reads
     it for 5 s (RMS, dropped frames, how much of it is digital silence);
  3. injects a 1 kHz tone at -20 dBFS for 1 s on the output and reports when the output's meters saw it and how long
     after inject() that was;
  4. keeps running until `--seconds` (30) have passed since the streams opened and reports underruns, resyncs, the
     frames each stream actually moved against the wall clock (the two clocks' rates) and this process's CPU use;
  5. changes the configured output (and input) device to a name that does not exist and back, and reports how long
     the bus takes to notice and to recover;
  6. on the fake backend only, unplugs a device and times recovery by backoff and by a device-change event.

The drift loop only runs for a lane that has a non-zero cell to the output. So that it runs against the real speaker
clock without the microphone anywhere near it, the script also pushes a quiet synthetic source from Python (lane 2,
paced by this computer's clock) into the speaker at -80 dB (about -110 dBFS, inaudible); `--no-synthetic` turns
that off. `--drift-cell` additionally adds lane 1 (the microphone) -> speaker at -80 dB so status() reports the
learned offset between the microphone clock and the speaker clock. It is off by default because it is a matrix cell
from the microphone to the speaker (-80 dB cannot feed back, but the rule is no cell at all); the script refuses to
go on if the output is not silent before the tone.
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import sys
import threading
import time

import numpy as np

import relay_bus

NO_SUCH_DEVICE = "relay-bus test: no such device"
LANE_RATE = 48000


def cpu_seconds() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


SYNTH = {"on": True}


def config(out_device, in_device, drift_cell: bool) -> dict:
    cfg = {
        "enabled": True,
        "lanes": [{"lane": 1, "name": "Smoke mic", "source": "portaudio", "device": in_device, "channel": 0}],
        "outputs": [{"id": "spk", "name": "Smoke speaker", "output": "portaudio", "device": out_device, "channels": 2,
                     "latency_ms": 60}],
        # No cell from lane 1 to the speaker: the microphone must never reach it.
        "matrix": [],
    }
    if drift_cell:
        cfg["matrix"].append({"in": 1, "out": "spk", "ch": 1, "gain_db": -80.0})
    if SYNTH["on"]:
        # A pushed (network-style) source, paced by Python's clock, into the speaker at -80 dB: a 0.03 amplitude
        # tone comes out at -110 dBFS, far below anything audible. (A cell at -90 dB or less is exactly zero, and
        # the output then does not read the lane at all, so the drift loop would not run.) It makes the drift loop
        # run against a real device clock without the microphone being anywhere near the speaker.
        cfg["lanes"].append({"lane": 2, "name": "Synthetic", "source": "synth", "device": None, "channel": 0})
        cfg["matrix"].append({"in": 2, "out": "spk", "ch": 1, "gain_db": -80.0})
    return cfg


def out_of(status: dict) -> dict:
    return next(o for o in status["outputs"] if o["id"] == "spk")


def wait_for(pred, timeout: float, step: float = 0.002):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        v = pred()
        if v:
            return time.perf_counter() - t0
        time.sleep(step)
    return None


class Pusher(threading.Thread):
    """Pushes 10 ms blocks of a quiet sine into the external input "synth:" on an absolute schedule."""

    def __init__(self, bus):
        super().__init__(daemon=True)
        self.bus, self.stop, self.pushed = bus, threading.Event(), 0
        n = np.arange(480)
        self.block = (0.03 * np.sin(2 * math.pi * 440 * n / LANE_RATE)).astype(np.float32)

    def run(self):
        t = time.perf_counter()
        while not self.stop.is_set():
            try:
                self.bus.push("synth:", self.block, LANE_RATE)
                self.pushed += len(self.block)
            except (ValueError, RuntimeError):
                pass  # the input is not configured at this moment (during the recovery test)
            t += 0.01
            delay = t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:
                t = time.perf_counter()


def rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(a, dtype=np.float64)))) if a.size else 0.0


def dbfs(x: float) -> str:
    return f"{20 * math.log10(x):.1f} dBFS" if x > 1e-9 else "-inf dBFS"


def section(title: str) -> None:
    print(f"\n== {title}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="cpal", choices=["cpal", "fake"])
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to run in total after the streams open")
    ap.add_argument("--tap-seconds", type=float, default=5.0)
    ap.add_argument("--input", default=None, help="input device name (default: the system default)")
    ap.add_argument("--output", default=None, help="output device name (default: the system default)")
    ap.add_argument("--no-synthetic", action="store_true", help="do not run the quiet pushed source (lane 2) into the speaker")
    ap.add_argument("--no-inject", action="store_true")
    ap.add_argument("--drift-cell", action="store_true", help="add lane 1 (the microphone) -> speaker at -80 dB to read the mic/speaker clock offset")
    args = ap.parse_args()
    SYNTH["on"] = not args.no_synthetic
    summary: dict = {"backend": args.backend}

    section("devices")
    bus = relay_bus.Bus(args.backend)
    print(f"device change notification: {bus.watch_method}")
    devices = bus.list_devices()
    for d in devices:
        flags = ("IN " if d["is_default_input"] else "") + ("OUT" if d["is_default_output"] else "")
        print(f"  {d['name']!r:40} in={d['inputs']:<3} out={d['outputs']:<3} rate={d['default_rate']:<6} {flags}")
    summary["devices"] = len(devices)

    section("open")
    cfg = config(args.output, args.input, args.drift_cell)
    t_apply = time.perf_counter()
    bus.apply(cfg)
    opened = wait_for(lambda: out_of(bus.status())["state"] == "open"
                      and bus.status()["inputs"] and bus.status()["inputs"][0]["state"] == "open", 10)
    st = bus.status()
    inp = st["inputs"][0] if st["inputs"] else {}
    o = out_of(st)
    print(f"apply() returned after {1000 * (time.perf_counter() - t_apply):.0f} ms; both streams open after "
          f"{'%.0f ms' % (1000 * opened) if opened is not None else 'NEVER'}")
    print(f"input : state={inp.get('state')} rate={inp.get('rate')} channels={inp.get('channels')} error={inp.get('error')}")
    print(f"output: state={o['state']} channels={o['channels']} target={o['target_ms']:.0f} ms error={o['error']}")
    if inp.get("state") != "open" or o["state"] != "open":
        print("a stream did not open (microphone permission? no device?); continuing with what works")
    pusher = None
    if SYNTH["on"]:
        pusher = Pusher(bus)
        pusher.start()
    t_open = time.perf_counter()
    cpu_open = cpu_seconds()
    frames_out_open = o["frames_out"]

    section(f"tap: 24 kHz on lane 1 for {args.tap_seconds:.0f} s")
    tap = bus.open_tap(1, rate=24000, capacity_ms=4000)
    got: list[np.ndarray] = []
    dropped = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < args.tap_seconds:
        a, d = bus.read_tap(tap, 24000, 0.2)
        dropped += d
        if a.size:
            got.append(a)
    samples = np.concatenate(got) if got else np.zeros(0, dtype=np.float32)
    wall = time.perf_counter() - t0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    zeros = float(np.mean(samples == 0.0)) if samples.size else 1.0
    print(f"{samples.size} samples in {wall:.2f} s = {samples.size / wall:.0f} Hz (want 24000)")
    print(f"RMS {rms(samples):.6f} ({dbfs(rms(samples))}), peak {peak:.6f}, exactly-zero samples {100 * zeros:.1f}%, dropped {dropped}")
    if samples.size == 0 or peak == 0.0:
        print("the microphone delivered only silence: permission denied for this terminal/Python, or the input is muted")
    summary["tap"] = {"samples": int(samples.size), "rms": rms(samples), "peak": peak, "zero_fraction": zeros,
                      "dropped": dropped, "rate_hz": samples.size / wall}

    if not args.no_inject:
        section("inject: 1 kHz, -20 dBFS, 1 s, both channels of the speaker output")
        before = out_of(bus.status())["meters"]
        if args.drift_cell and max(m["rms"] for m in before) > 1e-6:
            print("output is not silent before the tone; refusing to go on with a cell from the microphone")
            bus.close()
            return 2
        amp = 10 ** (-20 / 20)
        n = LANE_RATE
        tone = (amp * np.sin(2 * math.pi * 1000 * np.arange(n) / LANE_RATE)).astype(np.float32)
        t_inj = time.perf_counter()
        bus.inject("spk", [0, 1], tone, LANE_RATE)
        t_called = time.perf_counter()
        seen = None
        peak_seen = 0.0
        while time.perf_counter() - t_inj < 1.5:
            m = out_of(bus.status())["meters"]
            level = max(x["rms"] for x in m)
            peak_seen = max(peak_seen, max(x["peak"] for x in m))
            if seen is None and level > 0.01:
                seen = time.perf_counter() - t_inj
            if seen is not None and time.perf_counter() - t_inj > 1.2:
                break
            time.sleep(0.001)
        print(f"inject() call took {1000 * (t_called - t_inj):.2f} ms")
        print(f"output meter rose {'%.1f ms' % (1000 * seen) if seen is not None else 'NEVER'} after inject() started "
              f"(peak seen {peak_seen:.4f}, expected {amp:.4f})")
        summary["inject"] = {"call_ms": 1000 * (t_called - t_inj), "meter_rise_ms": None if seen is None else 1000 * seen,
                             "peak": peak_seen}
        time.sleep(0.3)
        print(f"meter after the clip ended: rms {max(x['rms'] for x in out_of(bus.status())['meters']):.5f}")

    section(f"soak until {args.seconds:.0f} s")
    drift_log = []
    next_log = 5.0
    while time.perf_counter() - t_open < args.seconds:
        a, d = bus.read_tap(tap, 24000, 0.2)
        dropped += d
        if SYNTH["on"] and time.perf_counter() - t_open >= next_log:
            od = out_of(bus.status())
            drift_log.append((round(time.perf_counter() - t_open, 1), od["drift"], od["queued_ms"], od["underruns"], od["resyncs"]))
            next_log += 5.0
    wall_total = time.perf_counter() - t_open
    cpu_total = cpu_seconds() - cpu_open
    st = bus.status()
    o = out_of(st)
    inp = st["inputs"][0] if st["inputs"] else {}
    out_rate = inp_rate = None
    if o["frames_out"] and wall_total:
        out_rate = (o["frames_out"] - frames_out_open) / wall_total
    print(f"wall {wall_total:.2f} s, process CPU {cpu_total:.2f} s = {100 * cpu_total / wall_total:.2f}% of one core")
    print(f"output: underruns={o['underruns']} resyncs={o['resyncs']} drift={o['drift']:.6f} ratio={o['ratio']:.6f} "
          f"queued={o['queued_ms']:.1f} ms (fill {o['fill_min_ms']:.1f}..{o['fill_max_ms']:.1f}) frames_out={o['frames_out']}")
    if out_rate and o["rate"]:
        print(f"        output device runs at {o['rate']} Hz; it moved {out_rate:.1f} frames/s of wall time "
              f"({(out_rate / o['rate'] - 1) * 1e6:+.0f} ppm against this computer's clock; includes timer noise)")
    else:
        print("        no output frames counted")
    print(f"input : state={inp.get('state')} dropped={inp.get('dropped')} lane age={st['lanes'][0]['last_input_age_s']}")
    print(f"global glitches: {st['glitches']}   advisory cpal errors (xruns, reroutes): {st['advisories']}")
    print(f"tap   : dropped total {dropped}")
    if SYNTH["on"]:
        print("speaker clock against this computer's clock, learned by the drift loop on the quiet pushed source (lane 2):")
        for t_, dr, q, u, r in drift_log:
            print(f"        t={t_:5.1f}s drift={dr:.6f} ({(dr - 1) * 1e6:+7.1f} ppm) queued={q:5.1f} ms underruns={u} resyncs={r}")
        print(f"        lane 2 subscribers={st['lanes'][1]['subscribers']} (1 means the speaker is reading it), pushed {pusher.pushed} frames")
    if args.drift_cell:
        print(f"learned drift mic -> speaker: {o['drift']:.6f} ({(o['drift'] - 1) * 1e6:+.1f} ppm)")
    else:
        print("learned drift mic -> speaker: not measured (no cell from the microphone to the speaker; use --drift-cell)")
    summary["soak"] = {"wall_s": wall_total, "cpu_s": cpu_total, "cpu_percent": 100 * cpu_total / wall_total,
                       "underruns": o["underruns"], "resyncs": o["resyncs"], "drift": o["drift"],
                       "glitches": st["glitches"], "advisories": st["advisories"], "output_device_rate": o["rate"], "output_hz_vs_wall": out_rate,
                       "input_dropped": inp.get("dropped"), "tap_dropped": dropped}
    bus.close_tap(tap)
    summary["soak"]["drift_log"] = drift_log

    section("recovery: device name that does not exist, then back")
    rec = {}
    for what, make in (("output", lambda dev: config(dev, args.input, args.drift_cell)),
                       ("input", lambda dev: config(args.output, dev, args.drift_cell))):
        def state_of():
            s = bus.status()
            return out_of(s)["state"] if what == "output" else (s["inputs"][0]["state"] if s["inputs"] else "none")
        bus.apply(make(NO_SUCH_DEVICE))
        went = wait_for(lambda: state_of() == "waiting", 5)
        s = bus.status()
        err = out_of(s)["error"] if what == "output" else (s["inputs"][0]["error"] if s["inputs"] else None)
        print(f"{what}: bad name -> state 'waiting' after {1000 * (went or 0):.0f} ms; error: {err}")
        time.sleep(1.5)  # let the supervisor retry (and fail) a couple of times
        bus.apply(make(args.output if what == "output" else args.input))
        back = wait_for(lambda: state_of() == "open", 10)
        flowing = wait_for(lambda: out_of(bus.status())["frames_out"] > 0 if what == "output" else bus.status()["lanes"][0]["last_input_age_s"] is not None and bus.status()["lanes"][0]["last_input_age_s"] < 0.2, 10)
        print(f"{what}: right name again -> 'open' {'%.0f ms' % (1000 * back) if back is not None else 'NEVER'}, "
              f"audio flowing {'%.0f ms' % (1000 * flowing) if flowing is not None else 'NEVER'} after apply() was called")
        rec[what] = {"open_ms": None if back is None else 1000 * back, "flowing_ms": None if flowing is None else 1000 * flowing}
    summary["recovery"] = rec
    print("(apply() reopens a changed device itself, so this times the reopen, not the background retry. A real unplug "
          "test needs a USB or Bluetooth device: unplug it, watch status(), plug it back in.)")

    if pusher:
        pusher.stop.set()
        pusher.join()
    bus.close()

    if args.backend == "fake":
        section("recovery by the supervise path (fake backend: unplug a device, plug it back in)")
        fb = relay_bus.Bus("fake")
        fb.apply(config("Fake Speaker", "Fake Mic", False))
        wait_for(lambda: out_of(fb.status())["state"] == "open", 5)
        for label, notify in (("by backoff (no event)", False), ("by a device-change event", True)):
            fb.fake_unplug("Fake Speaker", True)
            went = wait_for(lambda: out_of(fb.status())["state"] == "waiting", 5)
            time.sleep(0.6)
            fb.fake_unplug("Fake Speaker", False)
            if notify:
                fb.notify_device_change()
            back = wait_for(lambda: out_of(fb.status())["state"] == "open", 15)
            print(f"{label}: waiting after {1000 * (went or 0):.0f} ms; open again {'%.0f ms' % (1000 * back) if back is not None else 'NEVER'} after the device returned")
            summary.setdefault("supervise", {})[label] = None if back is None else 1000 * back
        fb.close()

    print("\nsummary json:")
    print(json.dumps(summary, indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
