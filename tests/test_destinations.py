"""Named destinations (#599): audio Relay makes itself (alert tones, a test tone, spoken translation, a module's own) goes to a
destination the operator patches to output channels in the route editor, and playing to it mixes the clip into those channels.
Real routes, the real engine and a fake output that records what it is given. Run: .venv/bin/python tests/test_destinations.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import numpy as np  # noqa: E402

from app import audio, audiobus, channels, config, routing, tones  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

CHANNELS = 4


class Sink(audio.LaneSink):
    """A sender that takes everything at once and keeps it."""
    rate = audio.LANE_RATE

    def __init__(self, channels):
        self.channels = channels
        self.blocks = []

    def free(self):
        return 4096

    def queued(self):
        return 0

    def write(self, block):
        self.blocks.append(np.array(block, dtype=np.float32))
        return len(block)

    def audio(self):
        return np.concatenate(self.blocks) if self.blocks else np.zeros((0, self.channels), dtype=np.float32)


class Out(audio.AudioOutput):
    id = "fake"
    label = "Fake out"

    def __init__(self):
        self.sinks = []

    def lane_sink(self, selector, channels):
        self.sinks.append(Sink(channels))
        return self.sinks[-1]


OUT = Out()
audio.register_output(OUT)
config.ensure_file()
bus = audiobus.bus
CFG = audiobus.normalize({"lanes": [], "outputs": [{"id": "mon", "output": "fake", "channels": CHANNELS}]})


def peak(a):
    return float(np.max(np.abs(a))) if len(a) else 0.0


def patch(dest: str, label: str, out_channel: int, gain_db: float) -> None:
    """The operator's route: the destination into one output channel, with a gain."""
    refs = audiobus.report_channels(CFG)
    src = channels.ensure(audiobus.PROVIDER, audiobus.DEST_PREFIX + dest, "audio", "source", label)
    routing.replace(refs[f"out:mon:{out_channel}"], [(src, gain_db)], apply=False)
    bus.apply(CFG)


with section("destinations are sources in the route editor"):
    bus.apply(CFG)
    keys = {d["key"] for d in audiobus.destinations()}
    check("core offers alert tones and a test tone", {"alerts", "test"} <= keys, str(keys))
    check("and a spoken translation for each target language", any(k.startswith("speech:") for k in keys), str(keys))
    listed = {e["key"] for c in channels.listing("audio") for e in c["ends"] if e["provider"] == audiobus.PROVIDER}
    check("each is a source beside the lanes", {"dest:alerts", "dest:test"} <= listed, str(sorted(listed)))
    audiobus.register_destination("talkback", "Talkback")
    check("a module can offer its own", "talkback" in {d["key"] for d in audiobus.destinations()})
    audiobus.unregister_destination("talkback")
    check("and take it away", "talkback" not in {d["key"] for d in audiobus.destinations()})

with section("nothing is patched, so nothing plays"):
    eq("a destination with no patch reaches no channel", bus.play_to("alerts", tones.alert_tone("info"), tones.RATE), 0)
    eq("so does one that does not exist", bus.play_to("nowhere", tones.alert_tone("info"), tones.RATE), 0)

with section("an alert tone plays on the channel it is patched to"):
    patch("alerts", "Alert tones", 3, -6.0)
    eq("the patch is where the route says", bus.patches.get("alerts"), [("mon", 3, -6.0)])
    sink = OUT.sinks[-1]
    before = len(sink.audio())
    t0 = time.perf_counter()
    eq("the tone reaches one channel", tones.play_alert("urgent"), 1)
    check("it sounds", wait_for(lambda: peak(sink.audio()[before:, 2]) > 0.05))
    heard = time.perf_counter() - t0
    a = sink.audio()[before:]
    check("within half a second", heard < 0.5, f"{heard:.3f} s")
    check("on channel 3 at the patch's gain (-12 dB tone, -6 dB patch)", abs(peak(a[:, 2]) - 0.25 * 0.5) < 0.02, str(peak(a[:, 2])))
    check("and on no other channel", peak(a[:, 0]) == 0 and peak(a[:, 1]) == 0 and peak(a[:, 3]) == 0)
    n_before = len(sink.audio())
    tones.on_alert({"event": "alert.sent", "level": "no-such-level"})
    check("core's alert listener plays an alert sent (an unknown level is the info tone)",
          wait_for(lambda: len(sink.audio()) > n_before and peak(sink.audio()[n_before:, 2]) > 0.05))
    tones.on_alert({"event": "alert.cleared"})
    tones.on_alert("junk")                                   # never raises

with section("a test tone and a spoken clip use their own patches"):
    patch("test", "Test tone", 1, 0.0)
    sink = OUT.sinks[-1]
    before = len(sink.audio())
    eq("the test tone reaches channel 1", tones.play_test(0.3), 1)
    check("it sounds there", wait_for(lambda: peak(sink.audio()[before:, 0]) > 0.2))
    speech = (np.sin(np.arange(4800) * 0.3) * 0.5).astype(np.float32)          # 0.2 s at 24 kHz
    eq("a spoken language with no patch reaches nothing", bus.play_to("speech:spanish", speech, 24000), 0)
    patch("speech:spanish", "Spoken translation: Spanish", 4, 0.0)
    before = len(sink.audio())
    eq("patched, it reaches channel 4", bus.play_to("speech:spanish", speech, 24000), 1)
    check("resampled from 24 kHz to 48 kHz: 0.2 s of speech is about 9600 samples", wait_for(lambda: int(np.count_nonzero(sink.audio()[before:, 3])) > 9000),
          str(int(np.count_nonzero(sink.audio()[before:, 3]))))

audiobus.bus.stop()
done()
