"""Crew checklists per position (#232): templates for a Planning Center team or for everyone, ticks kept per plan,
editing by operators and team leads, and the operator's progress board. Run: python tests/test_crew_checklists.py"""
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import directory, config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import calltimes, checklists, people  # noqa: E402
from app.modules.planning_center.pclive import Track  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import eq, fails  # noqa: E402


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
H = "http://relay.local:8001"
booth = _db.client(app, role="operator", base_url=H, username="booth")
mike_c = _db.client(app, role="member", base_url=H, username="mike")
sara_c = _db.client(app, role="member", base_url=H, username="sara")
lead_c = _db.client(app, role="member", base_url=H, username="lena")
mike, sara, lena = (users.get_by_username(n) for n in ("mike", "sara", "lena"))

ZONE = ZoneInfo("America/New_York")
SUN = datetime(2026, 11, 15, 8, 0, tzinfo=ZONE).timestamp()
tr = Track("7")
tr.plan = {"id": "700", "times": [{"id": "t1", "type": "service", "starts_at": SUN + 3600}]}
tr.team = [{"person": "501", "name": "Mike", "team": "Camera", "status": "confirmed", "times": None},
           {"person": "502", "name": "Sara", "team": "Audio", "status": "confirmed", "times": None}]
TRACKS = [tr]
calltimes._state = lambda: (TRACKS, ZONE)
people.ready()
for uid, pid in ((mike.id, "501"), (sara.id, "502")):
    directory.link(uid, directory.PCO, pid, "x")

print("writing templates")
good = {"title": "Camera setup", "position": "camera", "items": ["Power on", "White balance", " ", "Check tally"]}
eq("a member who leads nothing may not", mike_c.post("/crew/api/checklists/templates", json=good).status_code, 403)
eq("a member may not even list them", mike_c.get("/crew/api/checklists/templates").status_code, 403)
r = booth.post("/crew/api/checklists/templates", json=good)
eq("an operator adds one (blank items dropped)", (r.status_code, [i["text"] for i in r.json()["templates"][0]["items"]]),
   (200, ["Power on", "White balance", "Check tally"]))
cam = r.json()["templates"][0]["id"]
r = booth.post("/crew/api/checklists/templates", json={"title": "Everyone", "position": "", "items": ["Phone silent"]})
everyone = [t for t in r.json()["templates"] if not t["position"]][0]["id"]
for label, bad in (("no title", {**good, "title": " "}), ("no items", {**good, "items": []}), ("items not a list", {**good, "items": "x"}),
                   ("a long item", {**good, "items": ["x" * 141]}), ("too many", {**good, "items": ["a"] * 41}),
                   ("not text", {**good, "title": 5})):
    eq(f"refused: {label}", booth.post("/crew/api/checklists/templates", json=bad).status_code, 400)

print("team leads")
eq("an operator names a lead", booth.post("/api/admin/crew/checklist-leads", json={"user_id": lena.id, "position": "Audio"}).status_code, 200)
eq("a member may not name one", mike_c.post("/api/admin/crew/checklist-leads", json={"user_id": mike.id, "position": "Camera"}).status_code, 403)
eq("no such person", booth.post("/api/admin/crew/checklist-leads", json={"user_id": 9999, "position": "Audio"}).status_code, 404)
r = lead_c.get("/crew/api/checklists/templates")
eq("a lead sees only their position's lists (none yet)", (r.status_code, r.json()["templates"], r.json()["all"]), (200, [], False))
r = lead_c.post("/crew/api/checklists/templates", json={"title": "Audio setup", "position": "AUDIO", "items": ["Mute mics", "Soundcheck"]})
eq("a lead writes for their position", r.status_code, 200)
audio = r.json()["templates"][0]["id"]
eq("not for another", lead_c.post("/crew/api/checklists/templates", json=good).status_code, 403)
eq("not for everyone", lead_c.post("/crew/api/checklists/templates", json={"title": "x", "position": "", "items": ["a"]}).status_code, 403)
eq("not to change another's", lead_c.put(f"/crew/api/checklists/templates/{cam}", json=good).status_code, 403)
eq("not to move their own onto another", lead_c.put(f"/crew/api/checklists/templates/{audio}", json={**good, "title": "Audio setup"}).status_code, 403)
eq("not to delete another's", lead_c.delete(f"/crew/api/checklists/templates/{cam}").status_code, 403)
eq("a missing list is 404", lead_c.put("/crew/api/checklists/templates/9999", json=good).status_code, 404)
eq("a lead sees their own list only", [t["title"] for t in lead_c.get("/crew/api/checklists/templates").json()["templates"]], ["Audio setup"])
eq("an operator sees all", len(booth.get("/crew/api/checklists/templates").json()["templates"]), 3)

print("what a person sees")
r = mike_c.get("/crew/api/checklists").json()
eq("Mike gets the camera and everyone lists, not audio", sorted(l["title"] for l in r["lists"]), ["Camera setup", "Everyone"])
eq("the plan key is the plan", r["plan"], "700")
eq("Mike is not an editor", r["editor"], False)
eq("Lena, the lead, is", lead_c.get("/crew/api/checklists").json()["editor"], True)
eq("a person not matched to Planning Center gets only the everyone list", [l["title"] for l in lead_c.get("/crew/api/checklists").json()["lists"]], ["Everyone"])

print("ticking")
items = {l["title"]: [i["id"] for i in l["items"]] for l in mike_c.get("/crew/api/checklists").json()["lists"]}
r = mike_c.put(f"/crew/api/checklists/item/{items['Camera setup'][0]}", json={"done": True})
eq("a tick is kept", (r.status_code, [i["done_ts"] is not None for l in r.json()["lists"] if l["title"] == "Camera setup" for i in l["items"]]), (200, [True, False, False]))
eq("ticking twice changes nothing", mike_c.put(f"/crew/api/checklists/item/{items['Camera setup'][0]}", json={"done": True}).status_code, 200)
eq("another person's list item is refused", mike_c.put(f"/crew/api/checklists/item/{[i['id'] for t in checklists.templates() if t['id'] == audio for i in t['items']][0]}", json={"done": True}).status_code, 404)
eq("done must be a bool", mike_c.put(f"/crew/api/checklists/item/{items['Camera setup'][1]}", json={"done": "yes"}).status_code, 400)
eq("Sara does not see Mike's tick", [i["done_ts"] for l in sara_c.get("/crew/api/checklists").json()["lists"] if l["title"] == "Everyone" for i in l["items"]], [None])
r = mike_c.put(f"/crew/api/checklists/item/{items['Camera setup'][0]}", json={"done": False})
eq("unticking", [i["done_ts"] for l in r.json()["lists"] if l["title"] == "Camera setup" for i in l["items"]][0], None)

print("editing keeps ticks")
mike_c.put(f"/crew/api/checklists/item/{items['Camera setup'][0]}", json={"done": True})
mike_c.put(f"/crew/api/checklists/item/{items['Everyone'][0]}", json={"done": True})
booth.put(f"/crew/api/checklists/templates/{cam}", json={**good, "items": ["Power on", "Lens cap off", "White balance"]})
r = mike_c.get("/crew/api/checklists").json()
cam_items = [i for l in r["lists"] if l["title"] == "Camera setup" for i in l["items"]]
eq("order and text follow the edit, the unchanged tick stays", [(i["text"], i["done_ts"] is not None) for i in cam_items],
   [("Power on", True), ("Lens cap off", False), ("White balance", False)])

print("the board")
eq("a member may not read it", mike_c.get("/api/admin/crew/checklists").status_code, 403)
b = booth.get("/api/admin/crew/checklists").json()
got = {p["name"]: {l["title"]: (l["done"], l["total"]) for l in p["lists"]} for p in b["people"]}
eq("progress per person and list", got, {"Mike": {"Camera setup": (1, 3), "Everyone": (1, 1)},
                                          "Sara": {"Audio setup": (0, 2), "Everyone": (0, 1)}})
eq("the totals", (b["done"], b["total"], b["today"]), (2, 7, True))
print("a new plan starts fresh, the old one stays")
tr.plan = {"id": "701", "times": [{"id": "t9", "type": "service", "starts_at": SUN + 86400 * 7}]}
r = mike_c.get("/crew/api/checklists").json()
eq("nothing ticked on the new plan", [i["done_ts"] for l in r["lists"] for i in l["items"]], [None] * 4)
b = booth.get("/api/admin/crew/checklists").json()
eq("the old plan is listed", [p["plan"] for p in b["plans"]], ["700"])
old = booth.get("/api/admin/crew/checklists?plan=700").json()
eq("and can be read", (old["today"], old["done"], sorted(p["name"] for p in old["people"])), (False, 2, ["Mike"]))

print("removing")
eq("a lead deletes their own", lead_c.delete(f"/crew/api/checklists/templates/{audio}").status_code, 200)
eq("an operator deletes one, its ticks go", (booth.delete(f"/crew/api/checklists/templates/{cam}").status_code,
   db.one("select count(*) as n from crew_checklist_done where item_id in (select id from crew_checklist_items)")["n"]), (200, 1))
r = booth.delete(f"/api/admin/crew/checklist-leads/{lena.id}?position=Audio")
eq("a lead is removed", (r.status_code, r.json()["leads"]), (200, []))
eq("and may no longer edit", lead_c.post("/crew/api/checklists/templates", json={"title": "x", "position": "Audio", "items": ["a"]}).status_code, 403)
db.execute("delete from crew_checklist_done where plan_id = '700'")

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
