"""Prove audio-only NDI output and the picture-plus-sound combiner against the real NDI runtime (issue 301). Run from the
repo root with relay_ndi installed and the NDI runtime present:

    python native/relay-ndi/python/proof_audio.py
"""
from __future__ import annotations

import sys
import threading
import time

import numpy as np

import relay_ndi

TONE_DB = -20.0


def tone(n: int, start: int, hz: float = 1000.0) -> np.ndarray:
    x = (10 ** (TONE_DB / 20) * np.sin(2 * np.pi * hz * (np.arange(n) + start) / 48000)).astype(np.float32)
    return np.stack([x, x], axis=1)           # (frames, channels)


def find(name: str, tries: int = 12) -> str | None:
    for _ in range(tries):
        for s in relay_ndi.find(1.0):
            if name in s["name"]:
                return s["name"]
    return None


def listen(name: str, seconds: float = 4.0) -> dict:
    rx = relay_ndi.Connection(name, False, 5.0)
    out = {"audio_chunks": 0, "video_frames": 0, "rate": 0, "channels": 0, "peak": -120.0}
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        f = rx.receive(200)
        if not f:
            continue
        if "data" in f:
            out["video_frames"] += 1
        elif "audio" in f:
            out["audio_chunks"] += 1
            out["rate"], out["channels"] = f["rate"], f["audio"].shape[0]
            out["peak"] = max(out["peak"], f["audio_db"])
    rx.close()
    return out


def feed(sink: "relay_ndi.AudioSink", stop: threading.Event) -> None:
    pos = 0
    while not stop.is_set():
        n = min(sink.free(), 480)
        if n:
            sink.write(tone(n, pos))
            pos += n
        time.sleep(0.005)


def main() -> int:
    ok = True
    sink = relay_ndi.AudioSink("RELAY AUDIO PROOF", 2, 48000)
    stop = threading.Event()
    t = threading.Thread(target=feed, args=(sink, stop), daemon=True)
    t.start()
    name = find("RELAY AUDIO PROOF")
    got = listen(name) if name else {}
    print("audio-only source:", got, "underruns", sink.underruns)
    ok &= bool(got) and got["audio_chunks"] > 20 and got["video_frames"] == 0 and got["rate"] == 48000 and got["channels"] == 2
    ok &= bool(got) and abs(got["peak"] - TONE_DB) < 1.0

    video = relay_ndi.Sender("RELAY COMBINE PICTURE", 320, 180, 30.0, "BGRA")
    vstop = threading.Event()

    def pictures() -> None:
        frame = bytes([30, 120, 220, 255]) * (320 * 180)
        while not vstop.is_set():
            video.send(frame)

    threading.Thread(target=pictures, daemon=True).start()
    time.sleep(1.0)
    picture, sound = find("RELAY COMBINE PICTURE"), find("RELAY AUDIO PROOF")
    combo = relay_ndi.Combiner("RELAY COMBINED", picture, sound, 5.0)
    cname = find("RELAY COMBINED")
    got = listen(cname, 5.0) if cname else {}
    print("picture of one, sound of another:", got, "pictures sent", combo.frames)
    ok &= bool(got) and got["video_frames"] > 30 and got["audio_chunks"] > 20 and abs(got["peak"] - TONE_DB) < 1.5
    combo.close()
    vstop.set()
    stop.set()
    t.join()
    sink.close()
    video.close()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
