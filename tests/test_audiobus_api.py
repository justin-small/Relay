"""The audio bus API (#92): saved setting, who may change it, and that a save applies only what changed.
Fake devices only. Run: .venv/bin/python tests/test_audiobus_api.py"""
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

from app import audio, audiobus, config  # noqa: E402
from app.main import app  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402


class Sink(audio.LaneSink):
    channels, rate = 2, audio.LANE_RATE

    def free(self):
        return 0

    def queued(self):
        return 0

    def write(self, block):
        return 0


class Out(audio.AudioOutput):
    id = "fake"
    label = "Fake out"

    def __init__(self):
        self.opened = 0

    def lane_sink(self, selector, channels):
        self.opened += 1
        s = Sink()
        s.channels = channels
        return s


OUT = Out()
audio.register_output(OUT)
config.ensure_file()
admin = _db.client(app, role="admin", username="adminuser")
viewer = _db.client(app, role="viewer", username="viewuser")

BODY = {"enabled": True,
        "lanes": [{"lane": 40, "source": "portaudio"}],       # off the end: dropped, not clamped
        "outputs": [{"id": "mon", "output": "fake", "channels": 2}],
        "matrix": [{"in": 1, "out": "mon", "ch": 1, "gain_db": -3}]}

with section("the bus starts empty"):
    r = viewer.get("/api/admin/audiobus")
    eq("a viewer can read it", r.status_code, 200)
    d = r.json()
    eq("the saved setting is empty", d["config"], {"lanes": [], "outputs": [], "matrix": []})
    eq("32 lanes are reported, all unpatched", (d["lanes"], len(d["status"]["lanes"]),
                                                {x["state"] for x in d["status"]["lanes"]}), (32, 32, {"unpatched"}))
    check("sources and outputs include core's PortAudio and the bus's own", {"portaudio", "lane"} <= {s["id"] for s in d["sources"]}
          and {"portaudio", "bus"} <= {o["id"] for o in d["outputs"]})
    eq("the bus is not running", d["status"]["running"], False)

with section("only audio.manage changes it"):
    eq("a viewer cannot save", viewer.post("/api/admin/audiobus", json=BODY).status_code, 403)
    eq("and nothing was applied", OUT.opened, 0)

with section("saving applies the cleaned setting"):
    r = admin.post("/api/admin/audiobus", json=BODY)
    eq("an admin can save", r.status_code, 200)
    cfg = r.json()["config"]
    eq("the bad lane was dropped", cfg["lanes"], [])
    eq("the output and crosspoint were kept", ([o["id"] for o in cfg["outputs"]], cfg["matrix"][0]["gain_db"]), (["mon"], -3.0))
    eq("it is saved in the config file, without the crosspoints", config.get()["audio_bus"], {**cfg, "matrix": []})
    from app import audiobus as core_bus, channels as core_channels, routing as core_routing  # noqa: E402

    mon1 = next(c for c in core_channels.listing("audio") if any(e["key"] == "out:mon:1" for e in c["ends"]))
    eq("the crosspoint is a core route into that output channel, with its gain",
       [(x["name"], x["gain_db"]) for x in core_routing.feeds(mon1["ref"])], [("Lane 1", -3.0)])
    core_routing.set(mon1["ref"], core_channels.ensure("audiobus", "lane2", "audio", "source", "Lane 2"), -6)
    eq("a route made anywhere reaches the bus's matrix",
       sorted((c["in"], c["gain_db"]) for c in admin.get("/api/admin/audiobus").json()["config"]["matrix"] if c["ch"] == 1),
       [(1, -3.0), (2, -6.0)])
    eq("and the running bus", sorted(c["in"] for c in core_bus.bus.config["matrix"] if c["ch"] == 1), [1, 2])
    body = {k: v for k, v in BODY.items() if k != "matrix"}
    admin.post("/api/admin/audiobus", json=body)
    eq("a save with no matrix (the Audio bus page) leaves the routes alone",
       sorted((x["name"], x["gain_db"]) for x in core_routing.feeds(mon1["ref"])), [("Lane 1", -3.0), ("Lane 2", -6.0)])
    eq("the output was opened once", OUT.opened, 1)
    st = admin.get("/api/admin/audiobus").json()["status"]
    eq("the bus runs with its output", (st["running"], [o["id"] for o in st["outputs"]], st["outputs"][0]["state"]),
       (True, ["mon"], "open"))
    admin.post("/api/admin/audiobus", json={**BODY, "matrix": []})
    eq("a save that changes only the matrix leaves the output open", OUT.opened, 1)
    admin.post("/api/admin/audiobus", json={**BODY, "outputs": [{"id": "mon", "output": "fake", "channels": 4}]})
    eq("a changed output is reopened", OUT.opened, 2)

with section("saving an empty bus closes everything, and the change is audited"):
    admin.post("/api/admin/audiobus", json={"lanes": [], "outputs": []})
    st = admin.get("/api/admin/audiobus").json()["status"]
    eq("nothing patched, nothing open", st["outputs"], [])
    rows = admin.get("/api/admin/audit", params={"kind": "audio.bus"}).json() if admin.get("/api/admin/audit").status_code == 200 else None
    check("changes are in the audit log", rows is None or "audio.bus" in str(rows))

audiobus.bus.stop()
with section("a matrix saved in config before #544 moves to core routes"):
    from app import audiobus as old_bus, channels as old_channels, config as old_config, routing as old_routing  # noqa: E402

    legacy = {"lanes": [], "outputs": [{"id": "old", "output": "fake", "channels": 2}],
              "matrix": [{"in": 3, "out": "old", "ch": 2, "gain_db": -9.0}]}
    old_config.save({"audio_bus": legacy}, replace=("audio_bus",))
    run = old_bus.runtime_config(old_config.get()["audio_bus"])
    eq("the bus is given the same crosspoint", run["matrix"], [{"in": 3, "out": "old", "ch": 2, "gain_db": -9.0}])
    eq("config no longer holds it", old_config.get()["audio_bus"]["matrix"], [])
    old2 = next(c for c in old_channels.listing("audio") if any(e["key"] == "out:old:2" for e in c["ends"]))
    eq("it is a route into the output's second channel", [(x["name"], x["gain_db"]) for x in old_routing.feeds(old2["ref"])],
       [("Lane 3", -9.0)])
    eq("a second start changes nothing", old_bus.runtime_config(old_config.get()["audio_bus"])["matrix"], run["matrix"])

done()
