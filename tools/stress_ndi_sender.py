#!/usr/bin/env python3
"""One synthetic NDI camera for tools/stress.py: a moving picture at a given size and six channels of tone.

    .venv/bin/python tools/stress_ndi_sender.py STRESS-1080-1 1920x1080 --fps 30 --channels 6

It is a process of its own so the senders do not share Relay's interpreter or its GIL, the way a real camera does not.
Prints "ready" once the source is up, then sends until it is stopped.
"""
from __future__ import annotations

import argparse
import math
import signal
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RATE = 48000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("size", help="WxH, e.g. 1920x1080")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--channels", type=int, default=6)
    ap.add_argument("--level-db", type=float, default=-30.0)
    args = ap.parse_args()
    w, h = (int(x) for x in args.size.lower().split("x"))

    import relay_ndi
    from app.modules.ndi import runtime
    if runtime.load() is None:        # finds and loads the NDI runtime the way Relay does
        print("no NDI runtime: " + runtime.reason(), file=sys.stderr)
        return 2
    sender = relay_ndi.Sender(args.name, w, h, args.fps, "BGRA")

    # A few distinct pictures to cycle, so every frame is new to the encoder: a bar sweeps across.
    frames = []
    for k in range(8):
        img = np.zeros((h, w, 4), dtype=np.uint8)
        img[..., 3] = 255
        img[:, :, 0] = np.linspace(40, 200, w, dtype=np.uint8)[None, :]
        img[:, :, 1] = np.linspace(200, 40, h, dtype=np.uint8)[:, None]
        x = (k * w) // 8
        img[:, x:x + max(8, w // 40), :3] = 255
        frames.append(img.tobytes())

    amp = 10 ** (args.level_db / 20)
    per = int(round(RATE / args.fps))
    # Channel n carries its own tone (220 Hz steps), so a lane that is mis-patched is audible in the meters.
    freqs = [220.0 * (n + 1) for n in range(args.channels)]
    phase = np.zeros(args.channels)
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    print("ready", flush=True)
    period, n, t0 = 1.0 / args.fps, 0, time.monotonic()
    t = np.arange(per) / RATE
    while not stop:
        audio = np.stack([amp * np.sin(2 * math.pi * f * t + p) for f, p in zip(freqs, phase)]).astype(np.float32)
        phase = (phase + 2 * math.pi * np.array(freqs) * per / RATE) % (2 * math.pi)
        sender.send({"width": w, "height": h, "fourcc": "BGRA", "data": frames[n % len(frames)], "audio": audio, "rate": RATE})
        n += 1
        wait = t0 + n * period - time.monotonic()
        if wait > 0:
            time.sleep(wait)
    sender.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
