"""app/audio.py's sound output and the capture fan-out, with PortAudio replaced by a fake: which devices can
play, how a saved selector finds one, a clip written in short blocks on its own thread, Stop cutting it off, a
device that fails only logging, and a stalled consumer losing its oldest frame instead of holding up the rest.
Offline, no sound device needed. Run: python tests/test_audio_output.py
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

    out = audio.PortAudioOutput()
    with section("finding the saved device"):
        eq("none or empty is the default device", (out._device(None), out._device("")), (None, None))
        eq("by index", out._device("2"), 2)
        eq("by label, ignoring case", out._device("core audio: speakers"), 1)
        eq("by name", out._device("Speakers"), 1)
        eq("an index of a device that cannot play is not used", out._device("0"), None)
        eq("a device that is gone falls back to the default", out._device("HDMI"), None)

    with section("playing a clip"):
        pcm = b"\x01\x00" * 4800                         # 0.2 s at 24 kHz
        out.play(pcm, rate=24000, selector="Speakers")
        check("it plays on its own thread and finishes", wait_for(lambda: not out._playing, 2))
        s = FakeStream.made[-1]
        eq("on the chosen device, mono 16-bit at the clip's rate",
           (s.kw["device"], s.kw["channels"], s.kw["dtype"], s.kw["samplerate"]), (1, 1, "int16", 24000))
        eq("written in 50 ms blocks, all of it", s.writes, [1200, 1200, 1200, 1200])

    with section("stopping a clip"):
        FakeStream.block = threading.Event()
        out.play(b"\x01\x00" * 24000, rate=24000)
        check("the clip is playing", wait_for(lambda: FakeStream.made[-1].writes == [] and out._playing, 1))
        stopper = threading.Thread(target=out.stop)
        stopper.start()
        FakeStream.block.set()
        stopper.join(3)
        FakeStream.block = None
        check("Stop waits for it and it ends early", not out._playing and len(FakeStream.made[-1].writes) < 20,
              len(FakeStream.made[-1].writes))
        out.play(b"\x01\x00" * 2400, rate=24000)
        check("the next clip plays in full after a Stop",
              wait_for(lambda: not out._playing, 2) and FakeStream.made[-1].writes == [1200, 1200])

    with section("a device that fails"):
        records = []
        handler = logging.Handler()
        handler.emit = lambda r: records.append(r.getMessage())
        audio.log.addHandler(handler)
        FakeStream.fail = OSError("device unavailable")
        try:
            out.play(b"\x01\x00" * 100)
            check("the failure ends the clip", wait_for(lambda: not out._playing, 2))
        finally:
            FakeStream.fail = None
            audio.log.removeHandler(handler)
        check("…and is logged, not raised", any("Could not play audio" in r for r in records), records)
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
