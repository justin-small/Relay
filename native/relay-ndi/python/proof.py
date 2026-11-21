"""Prove the engine against the real NDI runtime: one process sends a moving 1080p30 UYVY picture with a -23 dBFS tone,
this one receives it and prints the rate, the late frames, the loudness and the scope time. Run it from the repo root
with relay_ndi installed and the NDI runtime present (NDI_RUNTIME_DIR_V6 if it is somewhere unusual):

    python native/relay-ndi/python/proof.py
"""
from __future__ import annotations

import subprocess
import sys
import time

import numpy as np

import relay_ndi

W, H, NAME = 1920, 1080, "RELAY PROOF 1080p30"
SECONDS = 10


def send() -> None:
    tx = relay_ndi.Sender(NAME, W, H, 30.0, "UYVY")
    x = np.arange(W // 2)
    frame = np.zeros((H, W // 2, 4), np.uint8)
    frame[:, :, 0] = (x * 255 // (W // 2))[None, :]
    frame[:, :, 2] = (255 - x * 255 // (W // 2))[None, :]
    frame[:, :, 1] = frame[:, :, 3] = (16 + np.arange(H) * 219 // H)[:, None]
    tone = (10 ** (-23 / 20) * np.sin(2 * np.pi * 1000 * np.arange(48000 * 60) / 48000)).astype(np.float32)
    n, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < SECONDS + 8:
        a = tone[n * 1600 % len(tone):][:1600]
        tx.send(np.roll(frame, n * 8, axis=1).tobytes(), np.stack([a, a]))
        n += 1


def receive() -> None:
    found = []
    for _ in range(10):
        found = [s["name"] for s in relay_ndi.find(1.0) if NAME in s["name"]]
        if found:
            break
    if not found:
        sys.exit("the proof source did not appear on the network")
    conn = relay_ndi.Connection(found[0])
    meter, grid, frames, gaps, late, last, scope_ms = relay_ndi.AudioMeter(), None, 0, 0, 0, None, []
    t0 = time.monotonic()
    while time.monotonic() - t0 < SECONDS:
        f = conn.receive(500)
        if not f:
            continue
        now = time.monotonic()
        if "audio" in f:
            meter.feed(f["audio"], f["rate"], now)
        elif "data" in f:
            frames += 1
            if last is not None:
                gaps += now - last > 0.150
                late += now - last > 2 / 30
            last = now
            if frames % 10 == 0:
                s = time.perf_counter()
                _, grid = relay_ndi.scopes(f["data"], f["width"], f["height"], f["fourcc"], grid)
                scope_ms.append(1000 * (time.perf_counter() - s))
    snap = meter.snapshot(time.monotonic()) or {}
    print(f"video {frames / SECONDS:.1f} fps (30 sent), gaps over 150 ms {gaps}, late frames {late}")
    print(f"audio integrated {snap.get('integrated')} LUFS (-23 sent), scopes {np.mean(scope_ms):.1f} ms a frame")
    print("counts", conn.perf())
    conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "send":
        send()
    else:
        child = subprocess.Popen([sys.executable, __file__, "send"])
        try:
            time.sleep(2)
            receive()
        finally:
            child.kill()
