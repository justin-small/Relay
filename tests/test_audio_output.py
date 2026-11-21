"""app/audio.py's device list and the capture fan-out, with PortAudio replaced by a fake: which devices can
play, and a stalled consumer losing its oldest frame instead of holding up the rest. (Playing a sound goes through
the audio bus's destinations: tests/test_audiobus.py.) Offline, no sound device needed. Run: python tests/test_audio_output.py
"""
import asyncio
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section, wait_for  # noqa: E402

from app import audio  # noqa: E402

APIS = [{"name": "Core Audio"}, {"name": "JACK"}]
DEVICES = [
    {"name": "Built-in Microphone", "hostapi": 0, "max_output_channels": 0, "max_input_channels": 2},
    {"name": "Speakers", "hostapi": 0, "max_output_channels": 2, "max_input_channels": 0},
    {"name": "USB Interface", "hostapi": 0, "max_output_channels": 8, "max_input_channels": 8},
    {"name": "USB Interface", "hostapi": 0, "max_output_channels": 8, "max_input_channels": 8},
    {"name": "system", "hostapi": 7, "max_output_channels": 2, "max_input_channels": 0},
]


class FakeStream:
    made = []
    fail = None
    block = None            # an Event a write waits on, to hold a clip mid-play

    def __init__(self, **kw):
        if FakeStream.fail:
            raise FakeStream.fail
        self.kw = kw
        self.writes = []
        FakeStream.made.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write(self, data):
        if FakeStream.block is not None:
            FakeStream.block.wait(2)
        self.writes.append(len(data))


class FakeSD:
    def query_hostapis(self):
        return APIS

    def query_devices(self):
        return DEVICES

    OutputStream = FakeStream


real_sd = audio.sd
audio.sd = FakeSD()
try:
    with section("output devices"):
        outs = audio.list_output_devices()
        eq("inputs-only devices are left out", [d["index"] for d in outs], [1, 2, 3, 4])
        eq("each is labelled with its host API", outs[0]["label"], "Core Audio: Speakers")
        eq("an unknown host API is shown as ?", outs[-1]["hostapi"], "?")
        check("two devices with the same name are told apart", outs[1]["label"] != outs[2]["label"],
              [o["label"] for o in outs])
finally:
    audio.sd = real_sd


async def fan_out():
    cap = audio.AudioCapture(asyncio.get_running_loop())
    fast, slow = asyncio.Queue(maxsize=10), asyncio.Queue(maxsize=2)
    cap._consumers = {fast, slow}
    for i in range(5):
        cap._dispatch(bytes([i]))
    eq("a consumer with room gets every frame", [fast.get_nowait() for _ in range(fast.qsize())],
       [bytes([i]) for i in range(5)])
    eq("a stalled consumer keeps only the newest frames", [slow.get_nowait() for _ in range(slow.qsize())],
       [bytes([3]), bytes([4])])
    eq("…and counts what it lost", slow.dropped, 3)


with section("the capture fan-out"):
    asyncio.run(fan_out())

done()
