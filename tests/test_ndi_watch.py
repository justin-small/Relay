"""Watching the sources the booth relies on (#562): which sources, the service window, the hold time, raising once and
clearing, the stage alert, and the saved settings and their route. The decision runs in Rust for real; the time is passed in.
Run: python tests/test_ndi_watch.py
"""
import asyncio
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

from app import alerts, config  # noqa: E402
from app import modules as registry  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.ndi import MODULE, runtime, watch  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, wait_for  # noqa: E402


class Lease:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def release(self):
        self.log.append(("release", self.name))


class World:
    """What the service would tell a watch: settings, what is wrong with each source, the clock, the stage alert."""

    def __init__(self):
        self.cfg = watch.clean_watch({"on": True, "hold": 10})
        self.auto = ["CAM (Stage)"]
        self.bad: dict[str, list] = {}
        self.open = True
        self.log: list = []
        self.alerts: list = []
        self.w = watch.Watch(lambda: self.cfg, lambda: self.auto, self.problems, self.acquire,
                             lambda ref: self.open, lambda why, on: self.alerts.append((why, on)))

    def problems(self, source, sound):
        return [p for p in self.bad.get(source, []) if sound or p[0] not in ("silence", "clipping")]

    def acquire(self, source):
        self.log.append(("acquire", source))
        return Lease(self.log, source)


with section("the saved settings"):
    eq("defaults", watch.clean_watch(None), {"on": False, "auto": True, "sources": [], "hold": 10, "schedule": "", "sound": True})
    c = watch.clean_watch({"on": True, "hold": 1, "sources": ["A", "A", "", 5, "B"], "schedule": " schedule:relay:abc ", "sound": "no"})
    eq("junk is cleaned", (c["on"], c["hold"], c["sources"], c["schedule"], c["sound"]), (True, 3, ["A", "B"], "schedule:relay:abc", True))
    eq("a long hold is capped", watch.clean_watch({"hold": 9999})["hold"], 300)
    eq("a boolean is not a hold time", watch.clean_watch({"hold": True})["hold"], 10)
    eq("the service cleans it too", svc.clean_settings({"watch": {"on": True}})["watch"]["on"], True)

with section("which sources are relied on"):
    w = World()
    w.cfg = watch.clean_watch({"on": True, "sources": ["MIC (Pulpit)"]})
    eq("ticked ones, then the automatic ones", w.w.relied(), ["MIC (Pulpit)", "CAM (Stage)"])
    w.cfg = watch.clean_watch({"on": True, "auto": False, "sources": ["MIC (Pulpit)"]})
    eq("automatic can be switched off", w.w.relied(), ["MIC (Pulpit)"])
    w.cfg = watch.clean_watch({"on": True, "sources": ["CAM (Stage)"]})
    eq("a source named twice is watched once", w.w.relied(), ["CAM (Stage)"])

with section("raising and clearing"):
    w = World()
    w.w.tick(0.0)
    eq("a receiver is held for each watched source", w.log, [("acquire", "CAM (Stage)")])
    w.bad["CAM (Stage)"] = [("frames", "Stage: Dropping frames")]
    w.w.tick(2.0)
    w.w.tick(8.0)
    eq("a problem shorter than the hold raises nothing", (w.w.active(), w.alerts), ([], []))
    w.w.tick(12.0)
    eq("held for ten seconds it is raised", w.w.active(), [{"source": "CAM (Stage)", "kind": "frames", "text": "Stage: Dropping frames"}])
    eq("the stage alert flashes once", w.alerts, [("Stage: Dropping frames", True)])
    w.w.tick(14.0)
    eq("and not again", len(w.alerts), 1)
    w.bad["CAM (Stage)"] = []
    w.w.tick(15.0)
    w.w.tick(19.0)
    eq("a recovery under five seconds is not yet a recovery", len(w.w.active()), 1)
    w.w.tick(21.0)
    eq("it clears and the alert stops", (w.w.active(), w.alerts[-1]), ([], ("", False)))

with section("offline, silence and clipping"):
    w = World()
    w.bad["CAM (Stage)"] = [("offline", "Stage is offline"), ("silence", "Stage has gone silent")]
    w.w.tick(0.0)
    w.w.tick(10.0)
    eq("both kinds are raised, one alert", ([a["kind"] for a in w.w.active()], len(w.alerts)), (["offline", "silence"], 1))
    w2 = World()
    w2.cfg = watch.clean_watch({"on": True, "sound": False})
    w2.bad["CAM (Stage)"] = [("silence", "x"), ("clipping", "y")]
    w2.w.tick(0.0)
    w2.w.tick(60.0)
    eq("sound problems are ignored when sound is off", w2.w.active(), [])

with section("only during a service"):
    w = World()
    w.cfg = watch.clean_watch({"on": True, "schedule": "schedule:relay:sunday"})
    w.open = False
    w.bad["CAM (Stage)"] = [("offline", "gone")]
    w.w.tick(0.0)
    w.w.tick(60.0)
    eq("outside the window nothing is received or raised", (w.log, w.w.active(), w.w.on_service), ([], [], False))
    w.open = True
    w.w.tick(61.0)
    w.w.tick(71.0)
    eq("inside it, the receiver is held and the problem raised", (w.log, len(w.w.active()), w.w.on_service), ([("acquire", "CAM (Stage)")], 1, True))
    w.open = False
    w.w.tick(80.0)
    eq("when the service ends the receiver is let go and the alert stops",
       (w.log[-1], w.w.active(), w.alerts[-1]), (("release", "CAM (Stage)"), [], ("", False)))

with section("switched off and stopped"):
    w = World()
    w.bad["CAM (Stage)"] = [("offline", "gone")]
    w.w.tick(0.0)
    w.w.tick(10.0)
    w.cfg = {**w.cfg, "on": False}
    w.w.tick(11.0)
    eq("switching it off lets everything go", (w.w.active(), w.log[-1]), ([], ("release", "CAM (Stage)")))
    w.cfg = {**w.cfg, "on": True}
    w.w.tick(12.0)
    w.w.tick(22.0)
    w.w.stop()
    eq("stop clears and releases", (w.w.active(), w.log[-1], w.alerts[-1]), ([], ("release", "CAM (Stage)"), ("", False)))
    eq("a source dropped from the list is let go", World().w.snapshot()["relied"], ["CAM (Stage)"])
    w3 = World()
    w3.bad["OLD (x)"] = [("offline", "gone")]
    w3.cfg = watch.clean_watch({"on": True, "auto": False, "sources": ["OLD (x)"]})
    w3.w.tick(0.0)
    w3.w.tick(10.0)
    w3.cfg = watch.clean_watch({"on": True, "auto": False, "sources": []})
    w3.w.tick(11.0)
    eq("and what it raised is cleared", (w3.w.active(), w3.alerts[-1]), ([], ("", False)))

with section("the stage alert button"):
    eq("without a button named NDI nothing happens and nothing breaks", watch.stage_alert("x", True), None)
    eq("stopping without one is quiet too", watch.stage_alert("", False), None)

with section("on the service and its routes"):
    class Backend:
        def connect(self, name, address="", low=False):
            raise OSError("no")

    runtime.install(Backend())
    s = svc.service
    asyncio.run(s.start())
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"ndi": {"enabled": True}},
                 "ndi": {**s.settings(), "tally": {"CAM (Stage)": 2}}})
    MODULE.attach()
    eq("a camera number makes a source relied on", s._watch_auto(), ["CAM (Stage)"])
    eq("an unseen source is offline", s._watch_problems("GONE (x)", True), [("offline", "GONE (x) is offline")])
    s.merge([{"name": "CAM (Stage)", "machine": "CAM", "source": "Stage", "host": "", "address": "", "port": 0,
              "groups": [], "converter": None}])
    eq("a seen source with no frames is not a fault of its own", s._watch_problems("CAM (Stage)", True), [])
    for _ in range(svc.GONE_AFTER):
        s.merge([])
    eq("one that stays gone is offline, by its short name", s._watch_problems("CAM (Stage)", True), [("offline", "Stage is offline")])
    admin = _db.client(app)
    r = admin.put("/api/admin/ndi/settings", json={"watch": {"on": True, "hold": 20, "sources": ["MIC (Pulpit)"]}})
    eq("the watch settings save", (r.status_code, r.json()["watch"]["on"], r.json()["watch"]["hold"]), (200, True, 20))
    eq("and show what is relied on", r.json()["watch"]["relied"], ["MIC (Pulpit)", "CAM (Stage)"])
    for bad, why in (({"hold": 1}, "hold"), ({"on": "yes"}, "on"), ({"sources": "x"}, "sources"), ({"schedule": "nope"}, "schedule")):
        eq(f"a bad {why} is refused", admin.put("/api/admin/ndi/settings", json={"watch": bad}).status_code, 400)
    eq("an unsaved change did not stick", s.settings()["watch"]["hold"], 20)
    eq("nothing uses a schedule yet", s.schedule_users("schedule:relay:abc"), [])
    config.save({"ndi": {**s.settings(), "watch": {**s.settings()["watch"], "schedule": "schedule:relay:abc"}}})
    eq("the Schedules page is told where it is used", s.schedule_users("schedule:relay:abc")[0]["where"], "NDI")
    eq("by ref or by id", s.schedule_users("abc")[0]["where"], "NDI")
    check("the module provides that to core", registry.using_all("schedules.used.") is not None)
    asyncio.run(s.stop())
    MODULE.detach()

done()
