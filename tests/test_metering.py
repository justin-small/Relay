"""The sound meter (#149): weighted levels and bands against known tones, the tap that never waits, calibration, the
admin API, the status feed's sound block and the board widgets. No devices. Run: .venv/bin/python tests/test_metering.py"""
import math
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import numpy as np  # noqa: E402

from app import audiobus, boards, config, metering, streamstatus  # noqa: E402
from app.main import app  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

RATE = metering.RATE


def tone(freq, amp, seconds):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def run(an, sig):
    for i in range(0, len(sig), 960):
        an.feed(sig[i:i + 960])


with section("weighting curves match the standard"):
    f = np.array([100.0, 1000.0, 10000.0])
    a, c = metering.a_weight_db(f), metering.c_weight_db(f)
    check("A at 1 kHz is 0 dB", abs(a[1]) < 0.05, str(a[1]))
    check("A at 100 Hz is -19.1 dB", abs(a[0] + 19.1) < 0.1, str(a[0]))
    check("A at 10 kHz is -2.5 dB", abs(a[2] + 2.5) < 0.1, str(a[2]))
    check("C at 100 Hz is -0.3 dB", abs(c[0] + 0.3) < 0.1, str(c[0]))

with section("a 1 kHz tone reads its calibrated level"):
    an = metering.Analyzer(calibration_db=110.0)
    run(an, tone(1000, 0.5, 6.0))
    snap = an.snapshot()
    want = 110.0 + 20 * math.log10(0.5 / math.sqrt(2))
    check("A slow is the tone's SPL", abs(snap["a_slow"] - want) < 0.3, f"{snap['a_slow']} vs {want:.1f}")
    check("A fast agrees on a steady tone", abs(snap["a_fast"] - want) < 0.3, str(snap["a_fast"]))
    check("C agrees at 1 kHz", abs(snap["c_slow"] - want) < 0.3, str(snap["c_slow"]))
    check("Leq(A) agrees", abs(snap["leq_a"] - want) < 0.5, str(snap["leq_a"]))
    eq("31 bands", len(snap["bands"]), 31)
    peak = int(np.argmax(snap["bands"]))
    eq("the peak is in the 1 kHz band", metering.BAND_LABELS[peak], "1k")
    check("the band holds the tone's level", abs(snap["bands"][peak] - want) < 1.5, str(snap["bands"][peak]))

with section("A-weighting turns a low tone down; C barely does"):
    an = metering.Analyzer(calibration_db=110.0)
    run(an, tone(100, 0.5, 6.0))
    s = an.snapshot()
    check("A is about 19 dB under C at 100 Hz", 17.5 < s["c_slow"] - s["a_slow"] < 19.5, f"{s['c_slow']} {s['a_slow']}")

with section("Fast follows a step quicker than Slow"):
    an = metering.Analyzer(calibration_db=110.0)
    run(an, tone(1000, 0.5, 3.0))
    run(an, tone(1000, 0.005, 0.3))
    s = an.snapshot()
    check("fast has fallen further than slow", s["a_fast"] < s["a_slow"] - 3, f"{s['a_fast']} {s['a_slow']}")

with section("silence reads the floor, not an error"):
    an = metering.Analyzer()
    run(an, np.zeros(RATE, dtype=np.float32))
    eq("A slow", an.snapshot()["a_slow"], metering.FLOOR_DB)

with section("the setting is cleaned"):
    n = metering.normalize({"enabled": 1, "lane": 99, "kind": "x", "calibration_db": "abc", "warn_db": 120, "limit_db": 90, "zzz": 1})
    eq("lane clamped", n["lane"], audiobus.LANES)
    eq("kind falls back", n["kind"], "room")
    eq("bad number takes the default", n["calibration_db"], 110.0)
    check("limit is not under warn", n["limit_db"] >= n["warn_db"])
    check("unknown keys dropped", "zzz" not in n)
    eq("junk is the default", metering.normalize("x"), metering.default_config())

with section("the tap copies and returns; a full queue drops, never waits"):
    bus = audiobus.Bus()
    m = metering.Metering(bus)
    m.apply({"enabled": True, "lane": 3, "calibration_db": 110.0})
    lane = bus.lane(3)
    eq("one subscriber on the lane", lane.subscribers, 1)
    lane.push(tone(1000, 0.5, 0.5))
    wait_for(lambda: m.analyzer.hops >= 4)
    eq("state is ok with sound", m.snapshot()["state"], "ok")
    m.stop()
    eq("stopped, unsubscribed", lane.subscribers, 0)
    m._q = metering.queue.Queue(maxsize=2)
    t0 = time.time()
    for _ in range(50):
        m._on_block(np.zeros(480, dtype=np.float32))
    check("50 blocks into a full queue took no time", time.time() - t0 < 0.5)
    eq("the rest were counted as dropped", m.dropped, 48)

with section("calibration against a reference tone"):
    bus = audiobus.Bus()
    m = metering.Metering(bus)
    m.apply({"enabled": True, "lane": 1})
    try:
        m.calibrate(94)
        check("no signal refuses", False)
    except ValueError as exc:
        check("no signal refuses with a sentence", "No sound" in str(exc), str(exc))
    for _ in range(10):
        bus.lane(1).push(tone(1000, 0.1, 0.6))
    wait_for(lambda: m.analyzer.hops >= 120, timeout=10)
    cal = m.calibrate(94)
    expect = 94 - 20 * math.log10(0.1 / math.sqrt(2))
    check("offset puts the tone at 94 dB", abs(cal - expect) < 0.6, f"{cal} vs {expect:.1f}")
    check("marked calibrated", m.snapshot()["calibrated"])
    try:
        m.calibrate("x")
        check("junk reference refused", False)
    except ValueError:
        check("junk reference refused", True)
    m.stop()

with section("the status feed carries sound only when asked, and only when the meter is on"):
    metering.meter.stop()
    check("no sound block when off", "sound" not in streamstatus.payload(sound=True))
    metering.meter.apply({"enabled": True, "lane": 2, "kind": "mix"})
    check("not for the default feed", "sound" not in streamstatus.payload())
    got = streamstatus.payload(sound=True)
    check("with it for a board", "sound" in got)
    check("it names the honest label", got["sound"]["kind"] == "mix" and got["sound"]["calibrated"] is False)
    check("no device details", "lane" not in got["sound"] and "dropped" not in got["sound"])
    metering.meter.stop()

with section("the admin API"):
    config.ensure_file()
    admin = _db.client(app, role="admin", username="adminuser")
    viewer = _db.client(app, role="viewer", username="viewuser")
    r = admin.get("/api/admin/metering")
    eq("read", r.status_code, 200)
    eq("off by default", r.json()["status"]["state"], "off")
    eq("a viewer may read", viewer.get("/api/admin/metering").status_code, 200)
    eq("a viewer may not save", viewer.post("/api/admin/metering", json={"enabled": True}).status_code, 403)
    r = admin.post("/api/admin/metering", json={"enabled": True, "lane": 5, "calibration_db": 105})
    eq("save", r.status_code, 200)
    eq("saved in config", config.get()["metering"]["lane"], 5)
    check("a hand-set calibration counts as calibrated", config.get()["metering"]["calibrated"])
    r = admin.post("/api/admin/metering", json={"warn_db": 80})
    eq("what is left out is kept", config.get()["metering"]["calibration_db"], 105.0)
    r = admin.post("/api/admin/metering/calibrate", json={"reference_db": 94})
    eq("no signal is a 409 with a sentence", r.status_code, 409)
    check("the sentence is shown", "error" in r.json())
    eq("a viewer may not calibrate", viewer.post("/api/admin/metering/calibrate", json={"reference_db": 94}).status_code, 403)
    admin.post("/api/admin/metering", json={"enabled": False})
    eq("off again", admin.get("/api/admin/metering").json()["status"]["state"], "off")

with section("the board widgets"):
    cat = boards.catalogue()
    check("sound level and spectrum are placeable", "spl" in cat and "rta" in cat)
    eq("kinds", (cat["spl"]["kind"], cat["rta"]["kind"]), ("spl", "rta"))
    props = boards.clean_props({"weighting": "Z", "response": "fast"}, cat["spl"])
    eq("a choice outside the offered ones falls back", props["weighting"], "A")
    eq("an offered one is kept", props["response"], "fast")
    board = boards.create([], {"name": "Sound", "items": [{"type": "rta", "x": 0, "y": 0, "w": 6, "h": 3,
                                                         "props": {"view": "waterfall"}}]}, cat)
    eq("a board holds it", board["items"][0]["props"]["view"], "waterfall")

done()
