"""Prove the A/V sync reading against the real NDI runtime (issue 561): one process sends a picture and a tone with the
audio labelled a known number of milliseconds later than the picture, this one receives it and prints what
`relay_ndi.SyncMeter` reads. Run from the repo root with relay_ndi installed and the NDI runtime present:

    python native/relay-ndi/python/proof_sync.py [offset_ms]
"""
from __future__ import annotations

import subprocess
import sys
import time

import numpy as np

import relay_ndi

W, H, FPS, NAME = 640, 360, 30.0, "RELAY SYNC PROOF"
SECONDS = 6


def send(offset: float) -> None:
    tx = relay_ndi.Sender(NAME, W, H, FPS, "BGRA", audio_offset_ms=offset)
    frame = bytes([40, 90, 200, 255]) * (W * H)
    tone = (0.1 * np.sin(2 * np.pi * 1000 * np.arange(1600) / 48000)).astype(np.float32)
    t0 = time.monotonic()
    while time.monotonic() - t0 < SECONDS + 8:
        tx.send(frame, np.stack([tone, tone]))


def receive() -> dict | None:
    for _ in range(10):
        found = [x["name"] for x in relay_ndi.find(1.0) if NAME in x["name"]]
        if found:
            break
    else:
        return None
    rx, sync = relay_ndi.Connection(found[0], False, 5.0), relay_ndi.SyncMeter()
    end = time.monotonic() + SECONDS
    while time.monotonic() < end:
        f = rx.receive(200)
        if f and "timecode" in f:
            (sync.video if "data" in f else sync.audio)(f["timecode"], f["timestamp"])
    rx.close()
    return sync.snapshot()


def reading(offset: float) -> dict | None:
    child = subprocess.Popen([sys.executable, __file__, "--send", str(offset)])
    time.sleep(1.5)
    try:
        return receive()
    finally:
        child.terminate()
        child.wait()
        time.sleep(1.0)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--send"]:
        send(float(sys.argv[2]))
        sys.exit(0)
    want = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
    base, got = reading(0.0), reading(want)
    print("audio labelled 0 ms from the picture:", base)
    print(f"audio labelled {want:+.1f} ms from the picture:", got)
    if not base or not got or not (base["valid"] and got["valid"]):
        print("no reading")
        sys.exit(1)
    moved = got["offset_ms"] - base["offset_ms"]
    print(f"the reading moved {moved:+.1f} ms for a {want:+.1f} ms change")
    sys.exit(0 if abs(moved - want) < 2.0 else 1)
