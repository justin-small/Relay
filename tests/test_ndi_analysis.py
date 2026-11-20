"""NDI analysis (#300): frame timing and health, the picture scopes, the audio meters and R128 loudness, the sender's
own product info, the CSV recording, the analysis session on the shared pool (and its low-bandwidth receiver), and the
routes with their permissions. A fake NDI backend stands in for the SDK; the maths runs for real.
Run: python tests/test_ndi_analysis.py
"""
import asyncio
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

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.ndi import MODULE, analysis, runtime  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

SRC = "CAM (Stage)"


def sine(db, n=1600, start=0, ch=2):
    x = (10 ** (db / 20) * np.sin(2 * np.pi * 1000 * (np.arange(n) + start) / 48000)).astype(np.float32)
    return np.stack([x] * ch)


def frame(w=64, h=36, fourcc="BGRA", rgb=(128, 128, 128)):
    if fourcc == "UYVY":
        return {"width": w, "height": h, "fps": 30.0, "fourcc": fourcc, "data": bytes([128, 126, 128, 126]) * (w // 2 * h)}
    px = bytes([rgb[2], rgb[1], rgb[0], 255]) * (w * h)
    return {"width": w, "height": h, "fps": 30.0, "fourcc": fourcc, "data": px}


with section("frame timing and health"):
    t = analysis.Timing()
    now = 100.0
    for i in range(60):
        t.record(now, 1000, 30.0, (64, 36))
        now += 1 / 30
    st = t.stats(now)
    check("a steady 30 fps measures 30", abs(st["measured_fps"] - 30.0) < 0.1)
    eq("no late frames", (st["late"], st["long_gaps"]), (0, 0))
    check("interval spread is tiny", st["interval_ms"]["sd"] < 0.01 and abs(st["interval_ms"]["avg"] - 33.33) < 0.05)
    eq("a healthy stream is ok", analysis.health(st)[0], "ok")
    eq("data rate is the decoded bytes", st["frame_bytes"], {"min": 1000, "avg": 1000, "max": 1000})
    check("rate in Mbit/s", abs(st["rate_mbps"] - 0.24) < 0.01)
    eq("a frame after 80 ms is late", t.record(now + 0.08, 1000), "late")
    eq("a frame after 200 ms is a long gap", t.record(now + 0.28, 1000), "gap")
    eq("a normal frame is clean", t.record(now + 0.31, 1000), "")
    eq("counted", (t.late, t.long_gaps), (2, 1))
    check("problems lately are no longer ok", analysis.health(t.stats(now + 0.32))[0] in ("warn", "bad"))
    one = analysis.Timing()
    for i in range(60):
        one.record(i / 30 + (0.01 if i > 40 else 0), 1000, 30.0)
    eq("one slip is only a warning", analysis.health(one.stats(2.1))[0], "warn")
    t.record(now + 0.60, 1000)
    t.record(now + 0.90, 1000)
    eq("many gaps are bad", analysis.health(t.stats(now + 0.91))[0], "bad")
    q = analysis.Timing()
    for i in range(10):
        q.record(10.0 + i / 30, 1000, 30.0)
    eq("no frames for 5 s is quiet, not a fault", analysis.health(q.stats(15.0))[0], "quiet")
    eq("nothing received is unknown", analysis.health({})[0], "unknown")
    steady = analysis.Timing()
    for i in range(60):
        steady.record(i / 30, 1000, 30.0)
    eq("the SDK's own drops turn it bad", analysis.health(steady.stats(2.0), dropped=3)[0], "bad")
    slow = analysis.Timing()
    for i in range(60):
        slow.record(i / 25, 1000, 30.0)
    eq("a 25 fps stream that says 30 is bad", analysis.health(slow.stats(2.4))[0], "bad")

with section("picture scopes"):
    sc, grid = analysis.scopes(frame(rgb=(128, 128, 128)))
    r = sc["read"]
    eq("a grey frame has no illegal pixels", (r["sub_black"], r["super_white"], r["illegal_rgb"]), (0, 0, 0))
    check("luma is about the mid code", 120 <= r["luma_mean"] <= 130)
    eq("first frame has no difference yet", r["frame_diff"], None)
    sc2, _ = analysis.scopes(frame(rgb=(128, 128, 128)), grid)
    eq("the same frame is frozen", ["FROZEN", "bad"] in sc2["alarms"], True)
    sc3, _ = analysis.scopes(frame(rgb=(0, 0, 0)))
    eq("black is flagged", ["BLACK", "bad"] in sc3["alarms"], True)
    sc4, _ = analysis.scopes(frame(rgb=(255, 255, 255)))
    eq("white reaches the top rail", sc4["read"]["luma_max"], 235)
    uy, _ = analysis.scopes(frame(fourcc="UYVY"))
    eq("UYVY reads luma", uy["read"]["luma_min"], 126)
    eq("the matrix is guessed from the height", (sc["read"]["matrix"], analysis.scopes(frame(1920, 1080, "UYVY"))[0]["read"]["matrix"]),
       ("BT.601", "BT.709"))
    eq("an unhandled pixel format is refused", analysis.scopes({**frame(), "fourcc": "P216"})[0], None)
    eq("the histogram counts every pixel", sum(sc["histogram"]), 64 * 36)
    eq("scopes are packed grids", (sc["waveform"]["w"], sc["waveform"]["h"], len(sc["parade"]), sc["vectorscope"]["w"]), (256, 256, 3, 128))
    hot = np.zeros((36, 64, 4), np.uint8)
    hot[:, :, 3] = 255
    hot[:, :, 2] = 255            # BGRA: pure red is outside what a limited-range signal carries
    red, _ = analysis.scopes({"width": 64, "height": 36, "fps": 30.0, "fourcc": "BGRA", "data": hot.tobytes()})
    eq("pure red is not outside the limited range once mapped", red["read"]["illegal_rgb"], 0)
    bars = np.zeros((36, 64, 4), np.uint8)
    bars[:, :, 3] = 255
    bars[:, :32, :3] = 128
    lb, _ = analysis.scopes({"width": 64, "height": 36, "fps": 30.0, "fourcc": "BGRA", "data": bars.tobytes()})
    eq("black columns on the right are a pillarbox", lb["read"]["pillarbox"], [0, 32])

with section("audio meters and loudness"):
    m = analysis.AudioMeter()
    for k in range(150):                           # 5 s of -23 dBFS on L and R
        m.feed(sine(-23, start=k * 1600), 48000, now=k / 30)
    a = m.snapshot(now=149 / 30)
    check("sample peak reads -23", abs(a["channels"][0]["peak"] + 23) < 0.2)
    check("true peak is at least the sample peak and close to it", a["channels"][0]["peak"] - 0.1 <= a["channels"][0]["true_peak"] < -22)
    eq("integrated loudness of -23 dBFS stereo is -23.0 LUFS", a["integrated"], -23.0)
    eq("momentary", a["momentary"], -23.0)
    eq("a steady tone has no loudness range", a["lra"], 0.0)
    eq("identical channels correlate", a["correlation"], 1.0)
    eq("nothing is clipping or silent", a["alarms"], [])
    peak_bin = max(range(len(a["spectrum"])), key=lambda i: a["spectrum"][i])
    check("the spectrum peaks near 1 kHz", abs((peak_bin + 1) * 48000 / 1024 - 1000) < 100)
    check("a stale meter says nothing", m.snapshot(now=200.0) is None)
    m.reset_stats()
    check("reset clears it", m.snapshot(now=1.0) is None)
    loud = analysis.AudioMeter()
    x = sine(0, ch=2)
    x[:, 100] = 1.0
    loud.feed(x, 48000, now=0.0)
    s = loud.snapshot(now=0.1)
    check("a full-scale sample is a clip", s["clips"] >= 1 and s["clip_now"] and ["CLIP", "warn"] in s["alarms"])
    quiet = analysis.AudioMeter()
    for k in range(80):
        quiet.feed(np.zeros((2, 1600), np.float32), 48000, now=k / 30)
    qs = quiet.snapshot(now=79 / 30)
    check("silence for 2 s raises SILENCE", ["SILENCE", "warn"] in qs["alarms"] and qs["channels"][0]["mute"])
    out = analysis.AudioMeter()
    x = sine(-12)
    x[1] = -x[1]
    for k in range(10):
        out.feed(x, 48000, now=k / 30)
    ph = out.snapshot(now=9 / 30)
    check("opposite polarity is out of phase", ph["correlation"] < -0.5 and ["PHASE", "warn"] in ph["alarms"])
    held = analysis.AudioMeter()
    held.feed(sine(-6), 48000, now=0.0)
    held.feed(sine(-40), 48000, now=1.0)
    check("the peak is held for 1.5 s", abs(held.snapshot(now=1.0)["channels"][0]["hold"] + 6) < 0.3)
    held.feed(sine(-40), 48000, now=3.0)
    check("then it falls at 22 dB a second", -20 > held.snapshot(now=3.0)["channels"][0]["hold"] > -40)
    six = analysis.AudioMeter()
    six.feed(np.zeros((6, 1600), np.float32), 48000, now=0.0)
    eq("six channels are metered", len(six.snapshot(now=0.1)["channels"]), 6)

with section("what the sender says about itself"):
    xml = ('<ndi_product long_name="BirdDog P200" short_name="P200" model_name="P200" version="5.2" manufacturer="BirdDog" '
           'serial="123"/><ndi_capabilities ntk_ptz="true" ntk_record="false" ntk_kvm="true"/>')
    eq("product and capabilities", analysis.parse_meta(xml),
       {"name": "BirdDog P200", "short": "P200", "model": "P200", "version": "5.2", "maker": "BirdDog", "serial": "123",
        "ptz": True, "recordable": False, "kvm": True})
    eq("nothing to read", analysis.parse_meta("<hello/>"), {})
    eq("HX is read from the instance name", analysis.source_type({"machine": "PTZ-1", "source": "NDI HX2"}), "NDI|HX")
    eq("a plain source is NDI", analysis.source_type({"machine": "PC", "source": "OBS"}), "NDI")

with section("recording"):
    rec = analysis.Recording(Path(TMP) / "rec", "A (b/c)", 2)
    check("the file name is safe", "/" not in rec.path.name and rec.path.suffix == ".csv")
    rec.frame(rec.began + 0.0, 100, (64, 36), "")
    rec.frame(rec.began + 0.1, 100, (64, 36), "late")
    rec.frame(rec.began + 5.0, 100, (64, 36), "")        # past the end: closes it, writes nothing
    eq("it stopped at its time", (rec.done, rec.rows), (True, 2))
    lines = rec.path.read_text().splitlines()
    eq("header then two frames", (lines[0], len(lines)), ("seconds,interval_ms,frame_bytes,width,height,flag", 3))
    check("the interval and flag are written", lines[2].split(",")[1] == "100.0" and lines[2].endswith("late"))
    eq("seconds are clamped", analysis.Recording(Path(TMP) / "rec", "x", 99999).seconds, 3600)


class Conn:
    def __init__(self, backend, name, low):
        self.backend, self.name, self.low, self.n = backend, name, low, 0

    def receive(self, timeout_ms):
        time.sleep(0.01)         # allow-sleep: stands in for the wait for the next frame
        self.n += 1
        if self.n % 3 == 0 and not self.low:
            return {"audio_db": -23.0, "audio": sine(-23, start=self.n * 1600), "rate": 48000}
        if self.n == 5:
            return {"meta": '<ndi_product long_name="Fake Cam" version="1.0"/>'}
        return {**frame(64, 36), "fps": 30.0}

    def perf(self):
        return {"video": {"frames_total": self.n, "frames_dropped": 2}}

    def close(self):
        self.backend.closed.append((self.name, self.low))


class Backend:
    def __init__(self):
        self.opened, self.closed = [], []

    def connect(self, name, address="", low=False):
        self.opened.append((name, low))
        return Conn(self, name, low)


with section("an analysis on the shared pool"):
    backend = Backend()
    runtime.install(backend)
    s = svc.service
    asyncio.run(s.start())
    s.merge([{"name": SRC, "machine": "CAM", "source": "Stage", "host": "", "address": "10.0.0.5", "port": 5961,
              "groups": ["public"], "converter": None}])
    MODULE.attach()

    async def session():
        key = await asyncio.to_thread(s.analysis_open, SRC)
        eq("the key is the source", key, SRC)
        check("frames are measured", wait_for(lambda: (s.analysis.tracks.get(SRC) and s.analysis.tracks[SRC].timing.frames > 10)))
        check("audio is metered while watched", wait_for(lambda: s.analysis_snapshot(key).get("audio")))
        check("the sender's product arrives", wait_for(lambda: s.analysis_snapshot(key)["info"].get("name") == "Fake Cam"))
        check("the SDK's drop count is read", wait_for(lambda: s.analysis_snapshot(key)["dropped"] == 2))
        snap = s.analysis_snapshot(key)
        check("scopes are computed from the newest frame", "picture" in snap and snap["picture"]["read"]["width"] == 64)
        eq("host and port come from discovery", (snap["info"]["host"], snap["info"]["port"]), ("10.0.0.5", 5961))
        eq("the listed source carries a health", s.sources()[0]["health"] in ("ok", "warn", "bad", "unknown", "quiet"), True)
        eq("it was opened once", backend.opened, [(SRC, False)])
        again = await asyncio.to_thread(s.analysis_open, SRC)
        eq("opening twice keeps one receiver", (again, backend.opened), (SRC, [(SRC, False)]))
        low = await asyncio.to_thread(s.analysis_open, SRC, True)
        check("low bandwidth is its own receiver", low != SRC and (SRC, True) in backend.opened)
        check("it carries no audio", wait_for(lambda: s.analysis.tracks[low].timing.frames > 3) and s.analysis_snapshot(low)["audio"] is None)
        eq("the low receiver is not a listener on the list", s.sources()[0]["listeners"], 1)
        s.analysis.record(key, 1)
        check("a recording collects frames", wait_for(lambda: s.analysis.tracks[key].recording.rows > 3))
        s.analysis.stop_recording(key)
        check("and is kept for download", s.analysis.last_recording[key].path.exists())
        await asyncio.to_thread(s.analysis_close, low)
        check("closing lets go of the low receiver", wait_for(lambda: (SRC, True) in backend.closed))
        s._open[key]["expires"] = time.monotonic() - 1
        await asyncio.to_thread(s._health_tick)
        check("an analysis nobody asks for is closed", key not in s._open and wait_for(lambda: (SRC, False) in backend.closed))
        eq("its numbers are gone with its receiver", s.analysis.health(SRC)[0], "unknown")

    asyncio.run(session())
    eq("a source that cannot be received refuses", asyncio.run(asyncio.to_thread(s.analysis_open, "")), None)

with section("the routes"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"ndi": {"enabled": True}}})
    admin = _db.client(app)
    viewer_role = _db.client(app, "viewer", username="watcher")
    svc.service.analysis.last_recording.clear()
    enc = "CAM%20%28Stage%29"
    eq("an unknown source is 404", admin.post("/api/admin/ndi/analysis/open", json={"source": "nope"}).status_code, 404)
    eq("low must be a boolean", admin.post("/api/admin/ndi/analysis/open", json={"source": SRC, "low": "yes"}).status_code, 400)
    r = admin.post("/api/admin/ndi/analysis/open", json={"source": SRC})
    eq("open", r.status_code, 200)
    check("it answers with the numbers", r.json()["source"] == SRC and "timing" in r.json())
    check("a viewer role may look", viewer_role.post("/api/admin/ndi/analysis/open", json={"source": SRC}).status_code == 200)
    check("reading keeps it open and returns scopes", wait_for(lambda: "picture" in admin.get(f"/api/admin/ndi/analysis?source={enc}").json()))
    r = admin.get(f"/api/admin/ndi/analysis?source={enc}&scopes=false")
    check("scopes can be left out", "picture" not in r.json())
    eq("reading something not open is 404", admin.get("/api/admin/ndi/analysis?source=nope").status_code, 404)
    eq("record: seconds are checked", admin.post("/api/admin/ndi/analysis/record", json={"source": SRC, "seconds": 0}).status_code, 400)
    eq("a viewer role may not record", viewer_role.post("/api/admin/ndi/analysis/record", json={"source": SRC, "seconds": 2}).status_code, 403)
    eq("download before any recording is 404", admin.get(f"/api/admin/ndi/analysis/recording?source={enc}").status_code, 404)
    r = admin.post("/api/admin/ndi/analysis/record", json={"source": SRC, "seconds": 2})
    eq("record", (r.status_code, r.json()["seconds"]), (200, 2))
    check("frames are written", wait_for(lambda: svc.service.analysis.last_recording[SRC].rows > 2))
    admin.post("/api/admin/ndi/analysis/record/stop", json={"source": SRC})
    r = admin.get(f"/api/admin/ndi/analysis/recording?source={enc}")
    check("the CSV downloads", r.status_code == 200 and r.text.startswith("seconds,interval_ms") and "text/csv" in r.headers["content-type"])
    eq("reset", admin.post("/api/admin/ndi/analysis/reset", json={"source": SRC}).status_code, 200)
    eq("close", admin.delete(f"/api/admin/ndi/analysis?source={enc}").status_code, 200)
    eq("then it is not open", admin.get(f"/api/admin/ndi/analysis?source={enc}").status_code, 404)
    asyncio.run(svc.service.stop())

done()
