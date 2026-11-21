"""Pluggable audio sources and outputs (#265): core registers PortAudio only, a module adds its own through
the registry, and a fake source feeds capture end to end. Run: python tests/test_audio_sources.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"

import numpy as np  # noqa: E402

from app import audio, config, engine as engine_mod  # noqa: E402
from app.modules import Manifest, Module  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, fails  # noqa: E402


def until(cond, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeSource(audio.AudioSource):
    id = "fake_net"
    label = "Fake network stream"

    def __init__(self):
        self.deliver = None
        self.opened = []
        self.closed = 0
        self.up = True
        self.fail = None

    def open(self, selector, channel, deliver):
        if self.fail:
            raise audio.SourceError(self.fail)
        self.opened.append((selector, channel))
        self.deliver, self.up = deliver, True
        return audio.SourceInfo(rate=16000, device={"label": "Fake: stream 1", "channels": 1}, channel=0)

    def close(self):
        self.closed += 1
        self.deliver = None

    def alive(self):
        return self.up


class FakeOutput(audio.AudioOutput):
    id = "fake_net"
    label = "Fake network sender"

    def __init__(self):
        self.clips = []

    def play(self, pcm, rate=audio.TARGET_RATE, selector=None):
        self.clips.append((len(pcm), rate, selector))


SRC, OUT = FakeSource(), FakeOutput()


class FakeModule(Module):
    """What a real module does: add its source and output while on, take them away when off."""
    manifest = Manifest(id="fake_net", name="Fake network", version="1.0.0", summary="test", settings_key="fake_net")

    def attach(self):
        audio.register_source(SRC)
        audio.register_output(OUT)

    def detach(self):
        audio.unregister_source(SRC.id)
        audio.unregister_output(OUT.id)


print("registry")
check("core registers PortAudio for both", audio.get_source("portaudio") is not None
      and audio.get_output("portaudio") is not None)
check("no id means PortAudio", audio.get_source(None).id == "portaudio")
check("a module's source is absent until it attaches", audio.get_source("fake_net") is None)
mod = FakeModule()
mod.attach()
check("attach adds the source and output", audio.get_source("fake_net") is SRC and audio.get_output("fake_net") is OUT)
check("PortAudio is listed first", [s.id for s in audio.sources()] == ["portaudio", "fake_net"])
audio.unregister_source("portaudio")
audio.unregister_output("portaudio")
check("core's PortAudio cannot be removed", audio.get_source("portaudio") and audio.get_output("portaudio"))
try:
    audio.register_source(type("Imposter", (audio.AudioSource,), {"id": "portaudio"})())
    imposter = True
except ValueError:
    imposter = False
check("a module cannot take the id portaudio", not imposter)
try:
    audio.register_output(type("Bad", (audio.AudioOutput,), {"id": "Not Snake"})())
    bad = True
except ValueError:
    bad = False
check("an id must be lower_snake_case", not bad)

print("a fake source feeds capture end to end")
loop = asyncio.new_event_loop()
cap = audio.AudioCapture(loop)
q = cap.add_consumer()
cap.gate_silence = False
dev = cap.start("stream-1", 0, "fake_net")
check("start returns the source's device", dev["label"] == "Fake: stream 1")
check("the source got the saved selector and channel", SRC.opened[-1] == ("stream-1", 0))
check("status reports the source's rate and device", cap.status()["native_rate"] == 16000
      and cap.status()["device"] == "Fake: stream 1" and cap.status()["running"])
tone = (0.5 * np.sin(2 * np.pi * 440 * np.arange(1600) / 16000)).astype(np.float32)
for _ in range(10):
    SRC.deliver(tone)                      # 10 x 100 ms
frames = []


async def drain():
    while True:
        try:
            frames.append(q.get_nowait())
        except asyncio.QueueEmpty:
            return


def got_audio():
    loop.run_until_complete(asyncio.sleep(0.05))
    loop.run_until_complete(drain())
    return len(frames) >= 20


check("the pump resamples to 24 kHz and fans frames out", until(got_audio), len(frames))
check("each frame is 40 ms of 16-bit mono at 24 kHz", all(len(f) == 1920 for f in frames))
check("the meter moves", cap.level > 0.1 and cap.peak > 0.4, (cap.level, cap.peak))

print("unplugging a source")
SRC.up = False
check("a source that reports itself dead is reconnected", until(lambda: cap.reconnecting))
check("the admin page gets the fixed sentence", cap.error == audio.INPUT_LOST_SENTENCE)
SRC.up = True
check("the pump re-opens the same source", until(lambda: len(SRC.opened) >= 2, secs=8.0), SRC.opened)
SRC.deliver(tone)
check("and the error clears once audio flows", until(lambda: not cap.reconnecting and cap.error is None))
cap.stop()
check("stop closes the source", SRC.closed >= 1 and cap.source is None and not cap.running)

print("refusals")
SRC.fail = "The stream could not be reached."
try:
    cap.start("x", 0, "fake_net")
    refused = False
except RuntimeError:
    refused = True
check("a source's refusal is the capture's fixed error", refused and cap.error == SRC.fail, cap.error)
SRC.fail = None
try:
    cap.start(None, 0, "nope")
    refused = False
except RuntimeError:
    refused = True
check("an unknown source is refused with a clear sentence", refused and "not available" in cap.error, cap.error)
mod.detach()
try:
    cap.start(None, 0, "fake_net")
    refused = False
except RuntimeError:
    refused = True
check("switching the module off takes its source away", refused)
loop.close()

print("outputs")
mod.attach()
audio.get_output("fake_net").play(b"\0\0" * 10, 24000, "bus 1")
check("a module's output receives the clip", OUT.clips == [(20, 24000, "bus 1")], OUT.clips)
mod.detach()

print("config and the engine")
check("the captions listen to lane 1 by default", config.save({})["audio_lane"] == 1)
check("a lane is clamped to the bus's 32", config.save({"audio_lane": 99})["audio_lane"] == 32)
check("a lane that is not a number falls back to 1", config.save({"audio_lane": "x"})["audio_lane"] == 1)
config.save({"audio_lane": 6})
seen = {}


class StubCapture:
    error = None

    def start(self, selector, channel, source="portaudio"):
        seen["args"] = (selector, channel, source)
        raise RuntimeError("stop here")


eng = engine_mod.Engine()
eng.capture = StubCapture()
config.save({"openai_api_key": "sk-test"})
try:
    asyncio.run(eng.start())
except engine_mod.StartRefused:
    pass
check("the engine starts capture on the saved lane", seen.get("args") == ("6", 0, "lane"), seen)

print()
print("FAILED: " + ", ".join(fails) if fails else "all checks passed")
sys.exit(1 if fails else 0)
