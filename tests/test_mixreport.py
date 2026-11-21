"""The stream-mix report (#149, #604): the one-row-a-second recorder reading a Rust loudness probe on a fake bus, the
verdicts on saved rows, the files and the admin routes. The loudness maths itself (K-weighting, gating, true peak,
correlation, octaves) is tested against known tones in Rust: native/relay-bus/crates/relay-bus/tests/probes.rs.
No devices. Run: .venv/bin/python tests/test_mixreport.py"""
import json
import math
import os
import sys
import tempfile
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

from app import audiobus, config, mixreport as mr  # noqa: E402
from app.main import app  # noqa: E402

from _buslane import feed, lane_bus  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402

RATE = audiobus.RATE


def tone(freq, amp, seconds, phase=0.0):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * math.pi * freq * t + phase)).astype(np.float32)


with section("the verdicts"):
    cfg = {"target_lufs": -16.0, "tolerance_lu": 2.0, "peak_limit_dbfs": -1.0}
    oct_ok = [-40, -36, -34, -32, -32, -34, -36, -40, -45, -50]
    rows = [{"t": i, "m": -16.0, "s": -16.0, "i": -16.0, "lra": 3.0, "pk": -6.0, "tp": -5.5, "co": 0.8, "oct": oct_ok}
            for i in range(60)]
    j = mr.judge(rows, cfg)
    eq("a good mix passes", j["overall"], "pass")
    eq("every check present", sorted(v["check"] for v in j["verdicts"]),
       ["Balance", "Consistency", "Loudness", "Peaks", "Phase"])
    loud_rows = [{**r, "m": -9.0, "s": -9.0, "i": -9.0} for r in rows]
    jl = mr.judge(loud_rows, cfg)
    eq("far too loud fails loudness", [v["status"] for v in jl["verdicts"] if v["check"] == "Loudness"], ["fail"])
    hot = [{**r, "pk": 0.0, "tp": 0.4} for r in rows]
    eq("clipping fails peaks", [v["status"] for v in mr.judge(hot, cfg)["verdicts"] if v["check"] == "Peaks"], ["fail"])
    inter = [{**r, "pk": -3.0, "tp": -0.2} for r in rows]
    check("a true peak over the limit warns even when the samples are under it",
          [v["status"] for v in mr.judge(inter, cfg)["verdicts"] if v["check"] == "Peaks"] == ["warn"])
    jg = mr.judge(rows, cfg)
    eq("the programme loudness is the meter's own running value", (jg["integrated_lufs"], jg["loudness_range"]), (-16.0, 3.0))
    old = [{k: v for k, v in r.items() if k not in ("i", "lra", "tp")} for r in rows]
    eq("a row without an integrated value reads as too quiet", [v["status"] for v in mr.judge(old, cfg)["verdicts"] if v["check"] == "Loudness"], ["warn"])
    phase = [{**r, "co": -0.5} for r in rows]
    eq("out of phase fails", [v["status"] for v in mr.judge(phase, cfg)["verdicts"] if v["check"] == "Phase"], ["fail"])
    bass = [{**r, "oct": [-20, -20, -20, -40, -40, -40, -40, -45, -50, -55]} for r in rows]
    eq("bass heavy warns", [v["status"] for v in mr.judge(bass, cfg)["verdicts"] if v["check"] == "Balance"], ["warn"])
    wander = [{**r, "s": -16.0 - (i % 2) * 15} for i, r in enumerate(rows)]
    eq("a wandering level warns", [v["status"] for v in mr.judge(wander, cfg)["verdicts"] if v["check"] == "Consistency"], ["warn"])
    eq("no rows", mr.judge([], cfg)["overall"], "warn")
    eq("the worst verdict is the overall", mr.judge(loud_rows, cfg)["overall"], "fail")
    eq("the graph is thinned", len(mr.series([{"t": i, "m": -16.0} for i in range(5000)])), mr.SERIES_POINTS)

with section("the setting is cleaned"):
    n = mr.normalize({"enabled": 1, "left": 99, "right": "x", "target_lufs": 5, "tolerance_lu": 0, "zzz": 1})
    eq("lane clamped", n["left"], audiobus.LANES)
    eq("bad lane takes the default", n["right"], 2)
    eq("target clamped", n["target_lufs"], -5.0)
    eq("tolerance clamped", n["tolerance_lu"], 0.5)
    check("unknown keys dropped", "zzz" not in n)
    eq("junk is the default", mr.normalize("x"), mr.default_config())

with section("the recorder saves a row a second while there is sound"):
    bus = lane_bus(1, 2)
    root = Path(tempfile.mkdtemp())
    rec = mr.Recorder(bus, root)
    rec.config = mr.normalize({"enabled": True, "left": 1, "right": 2})
    rec._open_probe_locked()
    pid = rec._probe[0]
    t0 = 1_800_000_000.0
    eq("nothing yet", rec.tick(t0), [])
    seen = [0]

    def put(block):
        """Both lanes get `block`; wait until the Rust probe has written the rows for it."""
        feed(bus, 1, block)
        feed(bus, 2, block)
        seen[0] += len(block) // RATE
        check("the probe caught up", wait_for(lambda: bus.engine.probe_status(pid)["seconds"] >= seen[0]), str(seen[0]))

    got = []
    for i in range(3):
        put(tone(997, 0.1, 1.0))
        got += rec.tick(t0 + 1 + i)
    eq("three seconds of tone are three rows", len(got), 3)
    check("momentary loudness is the tone's", all(abs(r["m"] + 20.0) < 0.3 for r in got), str([r["m"] for r in got]))
    check("the true peak is the tone's amplitude", abs(got[-1]["tp"] + 20.0) < 0.3, str(got[-1]["tp"]))
    eq("identical channels correlate fully", got[-1]["co"], 1.0)
    eq("ten octave levels, the tone in the 1k one", (len(got[-1]["oct"]), got[-1]["oct"].index(max(got[-1]["oct"]))), (10, 5))
    check("a running integrated loudness after three seconds", abs(got[-1]["i"] + 20.0) < 0.3, str(got[-1]["i"]))
    put(np.zeros(RATE, dtype=np.float32))
    eq("a silent second writes nothing", rec.tick(t0 + 5), [])
    rec.config["rehearsal"] = True
    put(tone(997, 0.1, 1.0))
    rec.tick(t0 + 6)
    files = list(root.glob("*.jsonl"))
    eq("one file", len(files), 1)
    lines = [json.loads(x) for x in files[0].read_text().splitlines()]
    eq("a header and four rows", len(lines), 5)
    eq("the header says where it came from", lines[0]["label"], "No captions session running")
    eq("only the last row is rehearsal", [bool(r.get("r")) for r in lines[1:]], [False, False, False, True])
    s = mr.list_sessions(root)
    eq("listed", (len(s), s[0]["seconds"]), (1, 4))
    head, rows = mr.read_session(s[0]["id"], root)
    eq("read back", len(rows), 4)
    eq("a bad id is refused", mr.read_session("../etc/passwd", root), None)
    eq("a missing session", mr.read_session("nothing", root), None)
    csv = mr.csv_text(rows).splitlines()
    eq("a csv header and rows", len(csv), 5)
    check("the csv has the octave and true-peak columns", "1k_hz_dbfs" in csv[0] and "true_peak_dbtp" in csv[0])
    # a damaged line is skipped, not fatal
    with files[0].open("a") as f:
        f.write("{not json\n")
    eq("a damaged line is skipped", len(mr.read_session(s[0]["id"], root)[1]), 4)
    rec.stop()
    eq("stopping closes the probe", rec._probe, None)

with section("the admin API"):
    config.ensure_file()
    admin = _db.client(app, role="admin", username="adminuser")
    viewer = _db.client(app, role="viewer", username="viewuser")
    r = admin.get("/api/admin/mixreport")
    eq("read", r.status_code, 200)
    eq("off by default", r.json()["status"]["running"], False)
    eq("a viewer may read", viewer.get("/api/admin/mixreport").status_code, 200)
    eq("a viewer may not save", viewer.post("/api/admin/mixreport", json={"enabled": True}).status_code, 403)
    r = admin.post("/api/admin/mixreport", json={"enabled": True, "left": 3, "right": 4, "target_lufs": -14})
    eq("save", r.status_code, 200)
    eq("saved", config.get()["mixreport"]["left"], 3)
    eq("running", r.json()["status"]["running"], True)
    r = admin.post("/api/admin/mixreport", json={"rehearsal": True})
    eq("what is left out is kept", config.get()["mixreport"]["target_lufs"], -14.0)
    import shutil
    mr.folder().mkdir(parents=True, exist_ok=True)
    shutil.copy(files[0], mr.folder() / files[0].name)
    sid = s[0]["id"]
    got = admin.get(f"/api/admin/mixreport/sessions/{sid}")
    eq("a session is judged", got.status_code, 200)
    eq("rehearsal seconds are left out by default", got.json()["report"]["seconds"], 3)
    eq("and counted", got.json()["rehearsal_seconds"], 1)
    eq("or included", admin.get(f"/api/admin/mixreport/sessions/{sid}?rehearsal=1").json()["report"]["seconds"], 4)
    eq("unknown session", admin.get("/api/admin/mixreport/sessions/nothing").status_code, 404)
    admin.post("/api/admin/mixreport", json={"enabled": False})
    eq("off again", admin.get("/api/admin/mixreport").json()["status"]["running"], False)

done()
