"""Planning Center rundown (#148): clock times, who is serving, song leaders, mics by position, sermon notes, the
"rundown" stream for /screen, and the admin page's LIVE buttons.

Offline. The team members and needed positions are answered in the JSON:API shapes the docs give for PlanPerson
and NeededPosition (written from the docs, not captured from a live account).

Run: python tests/test_pcrundown.py
"""
import os
import random
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.planning_center import pclive, planningcenter as pc, rundown  # noqa: E402
from app.modules.planning_center import routes as pcroutes  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


NY = ZoneInfo("America/New_York")


def at(h, m=0, s=0):
    return datetime(2026, 11, 15, h, m, s, tzinfo=NY).timestamp()


def hm(ts):
    return None if ts is None else datetime.fromtimestamp(ts, NY).strftime("%H:%M")


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"planning_center": {"enabled": True}}})
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")
viewer = TestClient(app, base_url="http://relay.local:8000")

# -- the client: team members and needed positions --------------------------------------------------------------
print("team members")


class Fake(pc.Client):
    def __init__(self, docs):
        super().__init__("id", "secret")
        self.docs = docs

    def get(self, path, params=None):
        return self.docs[path]


TM = "/services/v2/service_types/7/plans/700/team_members"
NP = "/services/v2/service_types/7/plans/700/needed_positions"
fake = Fake({
    TM: {"data": [
        {"type": "PlanPerson", "id": "1", "attributes": {"name": "Sarah Lee", "status": "C",
                                                         "team_position_name": "Worship Leader",
                                                         "photo_thumbnail": "https://avatars.example/sarah.jpg"},
         "relationships": {"team": {"data": {"type": "Team", "id": "t1"}}, "person": {"data": {"id": "p1"}},
                           "times": {"data": [{"type": "PlanTime", "id": "900"}]}}},
        {"type": "PlanPerson", "id": "2", "attributes": {"name": "Kim Alvarez", "status": "U",
                                                         "team_position_name": "Drums",
                                                         "photo_thumbnail": "http://insecure/x.jpg"},
         "relationships": {"team": {"data": {"type": "Team", "id": "t2"}}}},
        {"type": "PlanPerson", "id": "3", "attributes": {"name": "Eli Brooks", "status": "Declined",
                                                         "team_position_name": "Vocals"},
         "relationships": {"team": {"data": {"type": "Team", "id": "t1"}}, "times": {"data": []}}},
    ], "included": [{"type": "Team", "id": "t1", "attributes": {"name": "Vocals"}},
                    {"type": "Team", "id": "t2", "attributes": {"name": "Band"}}]},
    NP: {"data": [{"type": "NeededPosition", "id": "n1", "attributes": {"quantity": 2, "team_position_name": "Bass"},
                   "relationships": {"team": {"data": {"type": "Team", "id": "t2"}}, "time": {"data": None}}}],
         "included": [{"type": "Team", "id": "t2", "attributes": {"name": "Band"}}]},
})
team = fake.team_members("7", "700")
eq("three people, in order", [m["name"] for m in team], ["Sarah Lee", "Kim Alvarez", "Eli Brooks"])
eq("statuses from C, U and Declined", [m["status"] for m in team], ["confirmed", "pending", "declined"])
eq("team names from the include", [m["team"] for m in team], ["Vocals", "Band", "Vocals"])
eq("times: listed, unsaid (None) and none", [m["times"] for m in team], [["900"], None, []])
eq("only an https photo is kept", [m["photo"] for m in team], ["https://avatars.example/sarah.jpg", "", ""])
eq("needed positions", fake.needed_positions("7", "700"),
   [{"team": "Band", "position": "Bass", "quantity": 2, "time": None}])

# -- settings -----------------------------------------------------------------------------------------------------
print("settings")
rs = rundown.clean_rundown({"screen": "yes", "mics": [
    {"team": "Vocals", "position": "Vocals", "mics": ["03", "04", "03", "bad label!!"], "sings": True},
    {"team": "Vocals", "position": "Vocals", "mics": ["05"]},            # a repeat of the same position
    {"team": "", "position": "Host", "mics": ["12"], "sings": False, "label": "Announcements"},
    {"team": "Band", "position": "Keys", "mics": []},                     # no mics: dropped
]})
eq("screen is off unless exactly true", rs["screen"], False)
eq("mics cleaned: duplicates, bad labels and empty positions dropped",
   [(m["position"], m["mics"], m["sings"], m["label"]) for m in rs["mics"]],
   [("Vocals", ["03", "04"], True, ""), ("Host", ["12"], False, "Announcements")])
eq("default note fields", (rs["leader_note"], rs["leader_position"], rs["sermon_note"]),
   ("Leader", "Worship Leader", "Sermon Notes"))
check("saved under planning_center and kept when the token is saved again",
      "rundown" in pc.clean_stored({"rundown": {"screen": True}}) and pc.clean_stored({"rundown": {"screen": True}})["rundown"]["screen"])

# -- mic draws ------------------------------------------------------------------------------------------------------
print("mic draws")
mp = {"team": "Vocals", "position": "Vocals", "mics": ["03", "04"], "sings": True, "label": ""}
ppl = [{"id": "10", "status": "confirmed"}, {"id": "11", "status": "confirmed"}, {"id": "12", "status": "pending"}]
d1 = rundown.draw_mics(mp, ppl, {}, random.Random(1))
eq("two confirmed people get the two mics", sorted(d1.values()), ["03", "04"])
check("a pending person gets none", "12" not in d1)
d2 = rundown.draw_mics(mp, ppl, d1, random.Random(99))
eq("the draw is kept on the next build", d2, d1)
ppl3 = ppl[:2] + [{"id": "13", "status": "confirmed"}]
d3 = rundown.draw_mics(mp, ppl3, d1, random.Random(5))
eq("a third confirmed person gets no mic; the first two keep theirs", d3, d1)
d4 = rundown.draw_mics(mp, [ppl[1], ppl3[2]], d1, random.Random(5))
eq("someone drops out: the one who stays keeps their mic", d4["11"], d1["11"])
eq("...and the newcomer gets the freed one", d4["13"], d1["10"])
picks = {tuple(sorted(rundown.draw_mics(mp, ppl, {}, random.Random(n)).items())) for n in range(30)}
eq("a fresh draw is random", len(picks), 2)

# -- the rundown --------------------------------------------------------------------------------------------------
print("rundown")


def item(i, title, kind, length, pos, seq, key="", notes=(), live=None, end=None, exclude=False):
    return {"id": i, "title": title, "type": kind, "length": length, "position": pos, "sequence": seq, "key": key,
            "arrangement": "", "arrangement_sequence": [], "notes": [{"category": c, "content": t} for c, t in notes],
            "times": [{"id": "it" + i, "item": i, "plan_time": "900", "live_start_at": live, "live_end_at": end,
                       "exclude": exclude},
                      {"id": "iu" + i, "item": i, "plan_time": "1100", "live_start_at": None, "live_end_at": None,
                       "exclude": False}]}


tr = pclive.Track("7")
tr.plan = {"id": "700", "title": "Sunday Gathering", "series_title": "", "dates": "November 15, 2026",
           "sort_date": 0, "updated_at": "",
           "times": [{"id": "900", "type": "service", "name": "", "starts_at": at(9), "ends_at": at(10),
                      "live_starts_at": at(9, 1), "live_ends_at": None},
                     {"id": "1100", "type": "service", "name": "", "starts_at": at(11), "ends_at": at(12),
                      "live_starts_at": None, "live_ends_at": None},
                     {"id": "rh", "type": "rehearsal", "name": "", "starts_at": at(7), "ends_at": at(8),
                      "live_starts_at": None, "live_ends_at": None}]}
tr.items = [
    item("1", "Countdown", "media", 300, "pre", 1, live=at(8, 55), end=at(9, 1)),
    item("2", "Welcome", "item", 120, "during", 2, live=at(9, 1), end=at(9, 3)),
    item("3", "Build My Life", "song", 300, "during", 3, key="G", live=at(9, 3), end=at(9, 9)),
    item("4", "Way Maker", "song", 360, "during", 4, key="E", notes=[("Leader", "Tom Reyes\nkey change at bridge")],
         live=at(9, 9)),
    item("5", "Video", "media", 60, "during", 5, exclude=True),
    item("6", "Sermon", "item", 1920, "during", 6, notes=[("Sermon Notes", "1. Received.\n2. Rooted.")]),
    item("7", "Walk-out", "media", 600, "post", 7),
]
tr.live = {"id": "1", "controller": {"id": "9", "name": "Jen Park"},
           "current": {"id": "it4", "item": "4", "plan_time": "900", "live_start_at": at(9, 9), "live_end_at": None,
                       "exclude": False}, "next": None}
tr.team = [
    {"id": "1", "person": "p1", "name": "Sarah Lee", "status": "confirmed", "position": "Worship Leader",
     "team": "Vocals", "photo": "", "times": None},
    {"id": "2", "person": "p2", "name": "Ana Cruz", "status": "confirmed", "position": "Vocals", "team": "Vocals",
     "photo": "", "times": ["900"]},
    {"id": "3", "person": "p3", "name": "Priya Shah", "status": "confirmed", "position": "Vocals", "team": "Vocals",
     "photo": "", "times": None},
    {"id": "4", "person": "p4", "name": "Late Singer", "status": "confirmed", "position": "Vocals", "team": "Vocals",
     "photo": "", "times": ["1100"]},
    {"id": "5", "person": "p5", "name": "Jo Lin", "status": "confirmed", "position": "Host", "team": "Production",
     "photo": "", "times": None},
]
tr.needed = [{"team": "Band", "position": "Bass", "quantity": 1, "time": None}]
settings = {"rundown": rundown.clean_rundown({"mics": [
    {"team": "Vocals", "position": "Worship Leader", "mics": ["01"]},
    {"team": "Vocals", "position": "Vocals", "mics": ["03", "04"]},
    {"team": "", "position": "Host", "mics": ["12"], "sings": False, "label": "Announcements"},
]})}
now = at(9, 13, 0)
msg, draws = rundown.build(tr, settings, now, NY, rng=random.Random(3))
rows = {r["title"]: r for r in msg["items"]}
eq("the service time LIVE is running is picked", msg["time"]["hhmm"], "0900")
eq("only service times are offered (no rehearsal)", [t["hhmm"] for t in msg["times"]], ["0900", "1100"])
eq("an item excluded from this time is left out", "Video" in rows, False)
eq("pre-service counts back from the start", hm(rows["Countdown"]["planned"]), "08:55")
eq("planned starts add up the lengths", [hm(rows[t]["planned"]) for t in ("Welcome", "Build My Life", "Way Maker", "Sermon")],
   ["09:00", "09:02", "09:07", "09:13"])
eq("post-service starts at the planned end", hm(rows["Walk-out"]["planned"]), "09:45")
eq("done items show when they really started", [hm(rows[t]["actual"]) for t in ("Welcome", "Build My Life")],
   ["09:01", "09:03"])
eq("states", [rows[t]["state"] for t in ("Welcome", "Way Maker", "Sermon", "Walk-out")],
   ["done", "current", "next", ""])
eq("the rest is projected from the current item's real start", hm(rows["Sermon"]["projected"]), "09:15")
eq("projected end", hm(msg["header"]["projected_end"]), "09:47")
eq("planned end", hm(msg["header"]["planned_end"]), "09:45")
eq("header: current and next", (msg["header"]["current"], msg["header"]["next"]), ("Way Maker", "Sermon"))
eq("the leader comes from the item note's first line", rows["Way Maker"]["song"]["leader"], "Tom Reyes")
eq("...else from the leader position", rows["Build My Life"]["song"]["leader"], "Sarah Lee")
eq("a song shows each singer's mic, in mic order", [(m["mic"], m["name"]) for m in rows["Way Maker"]["song"]["mics"]][0],
   ("01", "Sarah Lee"))
eq("the speaking mic is not on a song", sorted(m["mic"] for m in rows["Way Maker"]["song"]["mics"]), ["01", "03", "04"])
names = [p["name"] for p in msg["people"]]
check("someone serving only at 11:00 is not on the 9:00 roster", "Late Singer" not in names)
check("a position still to fill is listed as unfilled",
      any(p["status"] == "unfilled" and p["position"] == "Bass" for p in msg["people"]))
on = {c["mic"]: c["on"] for c in msg["mics"]}
eq("during a song the singing mics are lit and the host's is not", on, {"01": True, "03": True, "04": True, "12": False})
eq("a labelled mic carries its label", [c["label"] for c in msg["mics"] if c["mic"] == "12"], ["Announcements"])
eq("sermon notes come from the next item that has them", (msg["notes"]["item"], msg["notes"]["text"]),
   ("Sermon", "1. Received.\n2. Rooted."))
check("the time zone is sent so the screen shows the organisation's clock", msg["zone"] == "America/New_York")
msg2, draws2 = rundown.build(tr, {"rundown": {**settings["rundown"], "draws": draws}}, now + 5, NY,
                             rng=random.Random(77))
eq("built again: the same draw", draws2, draws)
msg11, _ = rundown.build(tr, settings, now, NY, want_time="1100", rng=random.Random(3))
eq("a chosen service time", (msg11["time"]["hhmm"], msg11["time"]["chosen"]), ("1100", True))
eq("...where nothing is running, so nothing is current or projected",
   [r["state"] for r in msg11["items"]].count("current") + sum(1 for r in msg11["items"] if r["projected"]), 0)
check("...and the 11:00 singer is on its roster", "Late Singer" in [p["name"] for p in msg11["people"]])
eq("panels never see the people: the Service feed's state has no team",
   "team" in (pclive.follower.state("7") or {}), False)

# -- the stream and the admin routes ----------------------------------------------------------------------------
print("stream and admin")
f = pclive.follower


async def _no_poke():
    pass


f.poke = _no_poke
config.save({"planning_center": {"app_id": "id", "secret": "secret", "service_types": ["7"]}},
            replace=("planning_center",))
f.tracks["7"] = tr
f.names["7"] = "Sunday AM"

r = viewer.get("/stream?stream=rundown")
eq("the rundown stream is refused until switched on", r.status_code, 403)
r = viewer.get("/screen?rundown=full")
check("/screen?rundown=full serves the rundown page", r.status_code == 200 and "rundown.js" in r.text)
check("...and it never names a module route", "/api/admin/planning-center" not in r.text)
r = viewer.get("/screen?rundown=side&stream=translation&lang=ES")
check("the side view frames this /screen's captions without the rundown",
      'src="/screen?stream=translation&amp;lang=ES"' in r.text)
r = viewer.get("/screen?rundown=bogus")
check("an unknown rundown view is the ordinary caption screen", "rundown.js" not in r.text)

viewer_role = _db.client(app, role="viewer")
operator = _db.client(app, role="operator")
owner = _db.client(app, role="owner")
r = viewer_role.get("/api/admin/planning-center/rundown")
check("a viewer may look at the rundown in admin", r.status_code == 200 and r.json()["rundown"]["plan"]["id"] == "700")
check("...with every team and position for the mic map",
      {"team": "Band", "position": "Bass"} in r.json()["positions"])
eq("a viewer may not change the settings",
   viewer_role.post("/api/admin/planning-center/rundown/settings", json={"screen": True}).status_code, 403)
eq("an operator may not either", operator.post("/api/admin/planning-center/rundown/settings",
                                               json={"screen": True}).status_code, 403)
eq("a viewer may not press LIVE", viewer_role.post("/api/admin/planning-center/live/next", json={"type": "7"}).status_code, 403)
r = owner.post("/api/admin/planning-center/rundown/settings",
               json={"screen": True, "mics": settings["rundown"]["mics"], "sermon_note": "Sermon Notes"})
check("the owner switches it on and saves the mic map", r.status_code == 200 and r.json()["settings"]["screen"])
check("the draws are never sent to the page", "draws" not in r.json()["settings"])
eq("a non-boolean switch is refused",
   owner.post("/api/admin/planning-center/rundown/settings", json={"screen": "on"}).status_code, 400)

stream_resp = _modules.get("planning_center").stream("rundown", type("Req", (), {"headers": {}})(), {})
check("switched on: the stream answers", stream_resp is not None and stream_resp.media_type == "text/event-stream")
r = viewer.get("/stream?stream=rundown", headers={"cf-ray": "abc-EWR", "cf-connecting-ip": "203.0.113.9"})
eq("...but never through the remote access tunnel", r.status_code, 403)
eq("an unknown service type is refused", viewer.get("/stream?stream=rundown&type=99").status_code, 404)

first = pcroutes.rundown_now("7")
saved = config.get()["planning_center"]["rundown"]["draws"]
check("the mic draw is saved so it holds across restarts", any(k.startswith("700:900:") for k in saved))
eq("built again from the saved draw: same mics", [c["person"]["name"] if c["person"] else None for c in pcroutes.rundown_now("7")["mics"]],
   [c["person"]["name"] if c["person"] else None for c in first["mics"]])
r = operator.post("/api/admin/planning-center/rundown/redraw", json={"type": "7", "team": "Vocals", "position": "Vocals"})
check("an operator may redraw", r.status_code == 200)
check("...and the redraw keeps the other positions' draws",
      "700:900:Vocals|Worship Leader" in config.get()["planning_center"]["rundown"]["draws"])


class LiveFake(pc.Client):
    def __init__(self):
        super().__init__("id", "secret")
        self.posts = []

    def get(self, path, params=None):
        if path == "/services/v2/me":
            return {"data": {"id": "9"}}
        return {"data": {"type": "Live", "id": "1", "relationships": {"controller": {"data": {"type": "Person", "id": "9"}}}},
                "included": [{"type": "Person", "id": "9", "attributes": {"full_name": "Relay Owner"}}]}

    def post(self, path, params=None):
        self.posts.append(path)
        return self.get(path)


lf = LiveFake()
f._client = lf
f.me_id = "9"
r = operator.post("/api/admin/planning-center/live/next", json={"type": "7"})
check("an operator presses Next from admin", r.status_code == 200 and r.json()["ok"])
eq("...which goes to Planning Center as go_to_next_item", lf.posts[-1].rsplit("/", 1)[-1], "go_to_next_item")
last = pcroutes.control.recent()[0]
eq("...and is listed with the panels' presses under the user's name", (last["panel"], last["panel_name"], last["action"]),
   ("admin", "Operator", "next"))
r = operator.post("/api/admin/planning-center/live/take_control", json={"type": "7"})
check("Take control when Relay's person already has it changes nothing", r.status_code == 200 and not lf.posts[1:])
eq("an unknown action is 404", operator.post("/api/admin/planning-center/live/jump", json={"type": "7"}).status_code, 404)
r = operator.post("/api/admin/planning-center/live/next", json={"type": "42"})
eq("a service type Relay does not follow is refused", r.json().get("error"), "not_followed")

print()
print("all passed" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
