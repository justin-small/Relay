"""The sound meters (#149, #604): settings, the Rust probes behind them, calibration, a meter that keeps reading while a
busy Python thread runs, the admin API, the status feed's sound block and the board widgets. The weighting, time
constants, Leq and bands are tested against known tones in Rust (native/relay-bus/crates/relay-bus/tests/probes.rs).
No devices. Run: .venv/bin/python tests/test_metering.py"""
import math
import os
import sys
import tempfile
import threading
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

from _buslane import feed, lane_bus  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

RATE = audiobus.RATE


def tone(freq, amp, seconds):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * math.pi * freq * t)).astype(np.float32)


def meter_on(bus, *meters):
    m = metering.Metering(bus)
    m.apply({"meters": list(meters)})
    return m


with section("the maths lives in the Rust core, not here"):
    for name in ("metering.py", "mixreport.py"):
        text = (Path(__file__).resolve().parent.parent / "app" / name).read_text(encoding="utf-8")
        check(f"app/{name} has no FFT and no numpy", "numpy" not in text and "fft" not in text.lower().replace("probe", ""),
              "the weighting, loudness and spectrum belong in native/relay-bus (#604)")

with section("the setting is cleaned"):
    n = metering.normalize({"meters": [{"id": "A B", "name": "  Room   mic ", "lane": 99, "kind": "x", "calibration_db": "abc",
                                         "warn_db": 120, "limit_db": 90, "zzz": 1}, {"id": "m1"}, "junk"]})
    first = n["meters"][0]
    eq("a bad id is replaced", first["id"], "m1")
    eq("the next one gets a different id", n["meters"][1]["id"] != first["id"], True)
    eq("a junk entry becomes a default meter", len(n["meters"]), 3)
    eq("name tidied", first["name"], "Room mic")
    eq("lane clamped", first["lane"], audiobus.LANES)
    eq("kind falls back", first["kind"], "room")
    eq("bad number takes the default", first["calibration_db"], 110.0)
    check("limit is not under warn", first["limit_db"] >= first["warn_db"])
    check("unknown keys dropped", "zzz" not in first)
    eq("junk is the default", metering.normalize("x"), metering.default_config())
    eq("at most eight", len(metering.normalize({"meters": [{}] * 20})["meters"]), metering.MAX_METERS)

with section("a meter reads its lane through a Rust probe, and two meters may share one"):
    bus = lane_bus(3)
    m = meter_on(bus, {"id": "a", "lane": 3, "calibration_db": 110.0}, {"id": "b", "lane": 3, "calibration_db": 100.0})
    check("running", m.running)
    feed(bus, 3, tone(1000, 0.5, 3.5))      # under the tap's four-second ring, which a real input never fills at once
    want = 110.0 + 20 * math.log10(0.5 / math.sqrt(2))
    wait_for(lambda: m.snapshot("a")["state"] == "ok" and m.snapshot("a")["a_slow"] > want - 0.3, timeout=5)
    wait_for(lambda: m.snapshot("a")["bands"][17] > want - 1.0, timeout=5)
    a, b = m.snapshot("a"), m.snapshot("b")
    check("the tone's level", abs(a["a_slow"] - want) < 0.6, f"{a['a_slow']} vs {want:.1f}")
    check("the second meter has its own calibration", abs(b["a_slow"] - (want - 10.0)) < 0.5, str(b["a_slow"]))
    eq("31 bands", len(a["bands"]), 31)
    eq("the peak is in the 1 kHz band", metering.BAND_LABELS[int(np.argmax(a["bands"]))], "1k")
    eq("nothing was dropped", a["dropped"], 0)
    # disabling one closes its probe and leaves the other running with its averages
    m.apply({"meters": [{"id": "a", "lane": 3, "enabled": False}, {"id": "b", "lane": 3, "calibration_db": 100.0}]})
    eq("a is off", m.snapshot("a")["state"], "off")
    eq("b still reads", m.snapshot("b")["state"], "ok")
    m.stop()
    eq("stopped", m.running, False)
    eq("no taps left behind", bus.engine.status()["taps"], [])

with section("a meter keeps reading while a busy Python thread runs"):
    bus = lane_bus(1)
    m = meter_on(bus, {"id": "a", "lane": 1})
    busy = threading.Event()

    def burn():
        while not busy.is_set():
            sum(i * i for i in range(20000))

    burners = [threading.Thread(target=burn, daemon=True) for _ in range(4)]
    for t in burners:
        t.start()
    try:
        for _ in range(3):
            feed(bus, 1, tone(1000, 0.5, 1.0))
        ok = wait_for(lambda: m.snapshot("a")["state"] == "ok" and m.snapshot("a")["a_slow"] > 90.0, timeout=5)
    finally:
        busy.set()
    check("it read the tone", ok, str(m.snapshot("a")))
    eq("and lost nothing", m.snapshot("a")["dropped"], 0)
    m.stop()

with section("calibration against a reference tone"):
    bus = lane_bus(1)
    m = meter_on(bus, {"id": "a", "lane": 1})
    try:
        m.calibrate("a", 94)
        check("no signal refuses", False)
    except metering.CalibrateError as exc:
        eq("no signal refuses with its own reason", exc.reason, "no_signal")
        check("and a sentence", "No sound" in str(exc), str(exc))
    for _ in range(4):
        feed(bus, 1, tone(1000, 0.1, 1.0))
    wait_for(lambda: m.snapshot("a")["a_slow"] > 86.6, timeout=5)
    cal = m.calibrate("a", 94)
    expect = 94 - 20 * math.log10(0.1 / math.sqrt(2))
    check("offset puts the tone at 94 dB", abs(cal - expect) < 0.8, f"{cal} vs {expect:.1f}")
    check("the meter was marked calibrated", m.config["meters"][0]["calibrated"])
    try:
        m.calibrate("a", "x")
        check("a junk reference is refused", False)
    except metering.CalibrateError as exc:
        eq("a junk reference is refused", exc.reason, "reference")
    try:
        m.calibrate("nope", 94)
        check("an unknown meter refused", False)
    except metering.CalibrateError as exc:
        eq("an unknown meter is off", exc.reason, "off")
    m.stop()

with section("the status feed carries sound only when asked, and only when a meter is on"):
    metering.meter.stop()
    metering.meter.apply({"meters": []})
    check("no sound block when off", "sound" not in streamstatus.payload(sound=True))
    metering.meter.apply({"meters": [{"id": "desk", "name": "Desk", "lane": 2, "kind": "mix"},
                                     {"id": "room", "name": "Room", "lane": 3}]})
    check("not for the default feed", "sound" not in streamstatus.payload())
    got = streamstatus.payload(sound=True)
    check("with it for a board", "sound" in got)
    check("it names the honest label", got["sound"]["kind"] == "mix" and got["sound"]["calibrated"] is False)
    check("no device details", "lane" not in got["sound"] and "dropped" not in got["sound"])
    eq("every meter is a source a widget can pick", sorted(got["sound"]["sources"]), ["desk", "room"])
    eq("a source is labelled by name", got["sound"]["sources"]["room"]["label"], "Room")
    metering.meter.stop()

with section("other tools' sources sit beside the meters"):
    metering.register_source("osm", "Open Sound Meter")
    metering.publish("osm", {"a_slow": 71.234, "bands": [60.0] * 31, "junk": 1})
    got = metering.meter.public()
    eq("a source alone is enough", got["state"], "off")
    eq("its reading is rounded", got["sources"]["osm"]["a_slow"], 71.2)
    check("a short bands list is dropped", "bands" in got["sources"]["osm"])
    metering.publish("osm", {"bands": [1, 2, 3]})
    ids = [k for k, _ in metering.choices()]
    check("a widget can pick it", "osm" in ids)
    metering.unregister_source("osm")
    eq("gone", metering.meter.public(), None)

with section("the admin API"):
    metering.meter.apply({"meters": []})
    config.ensure_file()
    admin = _db.client(app, role="admin", username="adminuser")
    viewer = _db.client(app, role="viewer", username="viewuser")
    r = admin.get("/api/admin/metering")
    eq("read", r.status_code, 200)
    eq("no meters by default", (r.json()["config"]["meters"], r.json()["status"]), ([], []))
    eq("a viewer may read", viewer.get("/api/admin/metering").status_code, 200)
    eq("a viewer may not save", viewer.post("/api/admin/metering", json={"meters": []}).status_code, 403)
    r = admin.post("/api/admin/metering", json={"meters": [{"id": "room", "name": "Room", "lane": 5, "calibration_db": 105}]})
    eq("save", r.status_code, 200)
    eq("saved in config", config.get()["metering"]["meters"][0]["lane"], 5)
    eq("it is measuring", r.json()["status"][0]["state"] in ("ok", "no_input"), True)
    check("a new meter's calibration is not marked calibrated until it is set", not config.get()["metering"]["meters"][0]["calibrated"])
    r = admin.post("/api/admin/metering", json={"meters": [{"id": "room", "name": "Room", "lane": 5, "calibration_db": 99}]})
    check("a hand-set calibration counts as calibrated", config.get()["metering"]["meters"][0]["calibrated"])
    r = admin.post("/api/admin/metering/room/calibrate", json={"reference_db": 94})
    eq("no signal is a 409 with a sentence", r.status_code, 409)
    check("the sentence is shown", "error" in r.json())
    eq("an unknown meter is a 404", admin.post("/api/admin/metering/zzz/calibrate", json={"reference_db": 94}).status_code, 404)
    eq("a viewer may not calibrate", viewer.post("/api/admin/metering/room/calibrate", json={"reference_db": 94}).status_code, 403)
    admin.post("/api/admin/metering", json={"meters": []})
    eq("removed", admin.get("/api/admin/metering").json()["status"], [])
    metering.meter.stop()

with section("the board widgets"):
    cat = boards.catalogue()
    check("sound level and spectrum are placeable", "spl" in cat and "rta" in cat)
    eq("kinds", (cat["spl"]["kind"], cat["rta"]["kind"]), ("spl", "rta"))
    props = boards.clean_props({"weighting": "Z", "response": "fast"}, cat["spl"])
    eq("a choice outside the offered ones falls back", props["weighting"], "A")
    eq("an offered one is kept", props["response"], "fast")
    metering.meter.apply({"meters": [{"id": "room", "name": "Room"}]})
    field = next(f for f in boards.catalogue()["spl"]["fields"] if f["key"] == "source")
    check("a widget can pick each meter by name", ["room", "Room"] in [list(o) for o in field["options"]], str(field["options"]))
    metering.meter.stop()
    board = boards.create([], {"name": "Sound", "items": [{"type": "rta", "x": 0, "y": 0, "w": 6, "h": 3,
                                                         "props": {"view": "waterfall"}}]}, cat)
    eq("a board holds it", board["pages"][0]["items"][0]["props"]["view"], "waterfall")

done()
