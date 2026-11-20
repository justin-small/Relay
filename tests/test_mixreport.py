"""The stream-mix report (#149): K-weighted loudness against known tones, peak, correlation, octaves, the one-row-a-second
recorder on a fake bus, gated integrated loudness, the verdicts and the admin routes. No devices.
Run: .venv/bin/python tests/test_mixreport.py"""
import collections
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

from _harness import check, done, eq, section  # noqa: E402

RATE = mr.RATE


def tone(freq, amp, seconds, phase=0.0):
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * math.pi * freq * t + phase)).astype(np.float32)


with section("K-weighted loudness of a stereo tone"):
    # BS.1770: a 997 Hz sine at -20 dBFS peak in both channels reads about -20 - 3.01 + 3.01 - 0.691 = -23.7 LUFS...
    # per channel mean square 0.01/2*... the check is against the standard's own reference: 0 dBFS-peak 997 Hz on
    # both channels is -3.01 LUFS, so -20 dBFS peak is -23.0 LUFS.
    x = tone(997, 0.1, 2.0)
    row = mr.analyse_second(x, x, -50.0, collections.deque())
    check("stereo -20 dBFS tone reads -20.0 LUFS", row and abs(row["m"] + 20.0) < 0.3, str(row and row["m"]))
    check("short-term agrees on a steady tone", abs(row["s"] - row["m"]) < 0.3)
    check("peak is the tone's amplitude", abs(row["pk"] + 20.0) < 0.2, str(row["pk"]))
    eq("identical channels correlate fully", row["co"], 1.0)
    eq("ten octave levels", len(row["oct"]), 10)
    eq("the tone is in the 1k octave", int(np.argmax(row["oct"])), 5)

with section("K-weighting turns low frequencies down"):
    lo = mr.analyse_second(tone(60, 0.1, 2.0), tone(60, 0.1, 2.0), -50.0, collections.deque())
    check("60 Hz reads quieter than 997 Hz at the same level", lo["m"] < row["m"] - 0.5, f"{lo['m']} {row['m']}")

with section("correlation"):
    a = tone(500, 0.1, 2.0)
    out = mr.analyse_second(a, -a, -50.0, collections.deque())
    eq("opposite channels", out["co"], -1.0)
    b = tone(500, 0.1, 2.0, phase=math.pi / 2)
    check("quadrature channels are uncorrelated", abs(mr.analyse_second(a, b, -50.0, collections.deque())["co"]) < 0.05)

with section("silence writes nothing"):
    z = np.zeros(RATE, dtype=np.float32)
    eq("silent", mr.analyse_second(z, z, -50.0, collections.deque()), None)
    eq("too short", mr.analyse_second(a[:100], a[:100], -50.0, collections.deque()), None)

with section("integrated loudness gates quiet passages out"):
    loud = [-16.0] * 100
    quiet = [-45.0] * 100
    check("steady", abs(mr.integrated(loud) + 16.0) < 0.05)
    check("a quiet half under the relative gate does not drag it down", abs(mr.integrated(loud + quiet) + 16.0) < 0.05)
    check("below the absolute gate is nothing", mr.integrated([-80.0] * 10) is None)

with section("the verdicts"):
    cfg = {"target_lufs": -16.0, "tolerance_lu": 2.0, "peak_limit_dbfs": -1.0}
    oct_ok = [-40, -36, -34, -32, -32, -34, -36, -40, -45, -50]
    rows = [{"t": i, "m": -16.0, "s": -16.0, "pk": -6.0, "co": 0.8, "oct": oct_ok} for i in range(60)]
    j = mr.judge(rows, cfg)
    eq("a good mix passes", j["overall"], "pass")
    eq("every check present", sorted(v["check"] for v in j["verdicts"]),
       ["Balance", "Consistency", "Loudness", "Peaks", "Phase"])
    loud_rows = [{**r, "m": -9.0, "s": -9.0} for r in rows]
    jl = mr.judge(loud_rows, cfg)
    eq("far too loud fails loudness", [v["status"] for v in jl["verdicts"] if v["check"] == "Loudness"], ["fail"])
    hot = [{**r, "pk": 0.0} for r in rows]
    eq("clipping fails peaks", [v["status"] for v in mr.judge(hot, cfg)["verdicts"] if v["check"] == "Peaks"], ["fail"])
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
    bus = audiobus.Bus()
    root = Path(tempfile.mkdtemp())
    rec = mr.Recorder(bus, root)
    rec.config = mr.normalize({"enabled": True, "left": 1, "right": 2})
    t0 = 1_800_000_000.0
    eq("nothing yet", rec.tick(t0), None)
    for i in range(3):
        bus.lane(1).push(tone(997, 0.1, 1.0))
        bus.lane(2).push(tone(997, 0.1, 1.0))
        got = rec.tick(t0 + 1 + i)
        check(f"second {i + 1} is a row", got is not None and abs(got["m"] + 20.0) < 0.5, str(got))
    bus.lane(1).push(np.zeros(RATE, dtype=np.float32))
    bus.lane(2).push(np.zeros(RATE, dtype=np.float32))
    eq("a silent second writes nothing", rec.tick(t0 + 5), None)
    rec.config["rehearsal"] = True
    bus.lane(1).push(tone(997, 0.1, 1.0))
    bus.lane(2).push(tone(997, 0.1, 1.0))
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
    check("the csv has the octave columns", "1k_hz_dbfs" in csv[0])
    # a damaged line is skipped, not fatal
    with files[0].open("a") as f:
        f.write("{not json\n")
    eq("a damaged line is skipped", len(mr.read_session(s[0]["id"], root)[1]), 4)

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
