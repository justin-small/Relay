"""Unplug and replug a real audio device while the Rust bus runs (issue 601: "a real device unplugged and plugged back in
reconnects on its own, on each OS").

    python replug.py --list                         # the devices the bus sees
    python replug.py --input "USB Audio Device"     # watch an input; unplug it, wait, plug it back in
    python replug.py --output "USB Audio Device"    # the same for an output (nothing audible is sent to it)
    python replug.py --input A --output A --cycles 2

Each cycle: when the device state leaves "open" the script prints how long the bus took to notice, and when it is
"open" again how long the reconnect took and whether sound flows (an input's meter moves, an output's frame count
rises). The script stops after `--cycles` complete cycles, or `--seconds` (default 300), or Ctrl-C, and exits 0 only
when every unplug it saw was recovered with sound flowing.

The microphone is never patched to a speaker: an output only gets a muted crosspoint (-90 dB) from a silent
synthetic source, so the output stream opens and runs but plays nothing.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

import relay_bus

POLL_S = 0.05


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list the devices and exit")
    ap.add_argument("--input", help="input device name to watch (lane 1, channel 0)")
    ap.add_argument("--output", help="output device name to watch")
    ap.add_argument("--cycles", type=int, default=2, help="unplug/replug cycles to see (default 2)")
    ap.add_argument("--seconds", type=float, default=300.0, help="give up after this long (default 300)")
    ap.add_argument("--backend", default="cpal", choices=("cpal", "fake"), help="fake: exercise the script with no hardware")
    args = ap.parse_args()

    bus = relay_bus.Bus(args.backend)
    if args.list:
        for d in bus.list_devices():
            print(f"{d['name']!r}: {d['inputs']} in, {d['outputs']} out"
                  f"{' (default input)' if d['is_default_input'] else ''}{' (default output)' if d['is_default_output'] else ''}")
        return 0
    if not args.input and not args.output:
        ap.error("give --input and/or --output (see --list)")

    lanes = [{"lane": 1, "name": "replug input", "source": "portaudio", "device": args.input, "channel": 0}] if args.input else []
    outputs, matrix = [], []
    if args.output:
        outputs = [{"id": "o", "name": "replug output", "output": "portaudio", "device": args.output, "channels": 2, "latency_ms": 60}]
        # A silent synthetic lane (pushed from here) with a muted crosspoint: the output runs and plays nothing.
        lanes.append({"lane": 2, "name": "silence", "source": "replug", "device": "silence", "channel": 0})
        matrix = [{"in": 2, "out": "o", "ch": 1, "gain_db": -90.0}]
    bus.apply({"enabled": True, "lanes": lanes, "outputs": outputs, "matrix": matrix})
    print(f"watching {', '.join(x for x in (args.input and f'input {args.input!r}', args.output and f'output {args.output!r}') if x)}"
          f" on the {args.backend} backend (watcher: {bus.watch_method}).")
    print("Unplug the device, wait a few seconds, plug it back in. Ctrl-C to stop.\n")

    watched = {}
    if args.input:
        watched["input"] = {"state": None, "lost_at": None, "back_at": None, "mark": None, "n": 0}
    if args.output:
        watched["output"] = {"state": None, "lost_at": None, "back_at": None, "mark": None, "n": 0}

    if args.backend == "fake":      # no hardware to pull out: the fake backend unplugs and replugs its own devices
        import threading

        def pull_cables() -> None:
            for _ in range(args.cycles):
                time.sleep(1.0)
                for dev in (args.input, args.output):
                    if dev:
                        bus.fake_unplug(dev, True)
                time.sleep(1.5)
                for dev in (args.input, args.output):
                    if dev:
                        bus.fake_unplug(dev, False)
                time.sleep(2.5)

        threading.Thread(target=pull_cables, daemon=True).start()

    t0 = time.monotonic()
    silence = np.zeros((480, 1), dtype=np.float32)
    next_push = 0.0
    cycles, bad = 0, 0

    def reading(kind: str, st: dict):
        """(state, error, activity) for the watched input or output."""
        if kind == "input":
            lane = next(l for l in st["lanes"] if l["lane"] == 1)
            return lane["state"], lane["error"], 0, lane["last_input_age_s"]
        out = next(o for o in st["outputs"] if o["id"] == "o")
        return out["state"], out["error"], out["frames_out"], None

    try:
        while time.monotonic() - t0 < args.seconds and min(w["n"] for w in watched.values()) < args.cycles:
            now = time.monotonic() - t0
            if args.output and now >= next_push:                      # keep the synthetic source's clock going
                try:
                    bus.push("replug:silence", silence, 48000)
                except (ValueError, RuntimeError):
                    pass
                next_push = now + 0.01
            st = bus.status()
            for kind, w in watched.items():
                state, error, activity, age = reading(kind, st)
                if w["state"] is not None and state != w["state"]:
                    print(f"[{now:7.2f}s] {kind}: {w['state']} -> {state}" + (f"  ({error})" if error else ""))
                    if w["state"] == "open" and state != "open":
                        w["lost_at"], w["back_at"] = now, None
                        print(f"           lost; first seen {now - (w['mark'] or now):.2f} s after its last good reading")
                    elif state == "open" and w["lost_at"] is not None:
                        w["back_at"] = now
                        w["flow_from"] = activity
                        print(f"           back {now - w['lost_at']:.2f} s after it was noticed gone")
                if state == "open":
                    w["mark"] = now
                if w["back_at"] is not None and now - w["back_at"] >= 1.5:   # judge the sound after a second and a half
                    flowing = (activity > w["flow_from"]) if kind == "output" else (age is not None and age < 0.5)
                    print(f"           {'sound flows' if flowing else 'NO SOUND after reconnecting'} ({kind})")
                    bad += 0 if flowing else 1
                    cycles += 1
                    w["n"] += 1
                    w["lost_at"] = w["back_at"] = None
                w["state"] = state
            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        bus.close()

    unrecovered = [k for k, w in watched.items() if w["lost_at"] is not None]
    print(f"\n{cycles} unplug(s) seen across the watched devices; {bad} with no sound afterwards; still gone: {', '.join(unrecovered) or 'none'}.")
    if cycles == 0:
        print("No unplug was seen.")
        return 2
    return 0 if not bad and not unrecovered else 1


if __name__ == "__main__":
    sys.exit(main())
