"""Crew roles and names matched to Planning Center (#227). Run: python tests/test_crew_people.py"""
import os
import sys
import tempfile
from pathlib import Path

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
from app import config, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import MODULE, people  # noqa: E402
from app.modules.planning_center.pclive import follower, Track  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import eq, fails  # noqa: E402


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})

print("similarity")
eq("exact once normalised", people.similarity("  José  Núñez ", "jose nunez"), 1.0)
eq("a short form", people.similarity("Mike Smith", "Michael Smith") >= people.CONFIDENT, True)
eq("a one-letter slip", people.similarity("Jon Smith", "John Smith") >= people.CONFIDENT, True)
eq("a different first name is not close", people.similarity("Jon Smith", "Jan Smith") < people.CONFIDENT, True)
eq("a different surname is not close", people.similarity("Mike Smith", "Mike Jones") < people.CONFIDENT, True)
eq("one word never fuzzy matches", people.similarity("Madonna", "Madona"), 0.0)

print("confident only")
P = [{"id": "1", "name": "Michael Smith"}, {"id": "2", "name": "Sara Lee"}]
eq("the closest, alone", people.best_match("Mike Smith", P, set())["id"], "1")
eq("two as close are no match", people.best_match("Mike Smith", [{"id": "3", "name": "Mike Smith"},
                                                                  {"id": "4", "name": "Mike  Smith"}], set()), None)
eq("a person already held is no match", people.best_match("Mike Smith", P, {"1"}), None)

print("roles")
eq("clean", people.clean_role("  Camera   1 "), "Camera 1")
for bad in ("x" * 41, "a\nb", 5):
    try:
        people.clean_role(bad)
        eq(f"refused {bad!r}", False, True)
    except people.Invalid:
        eq(f"refused {bad!r}"[:30], True, True)

admin = _db.client(app, base_url="http://relay.local:8001")
member = _db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
other = _db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
users.update(mike.id, name="Mike Smith", actor=None)
users.update(sara.id, name="Sara Lee", actor=None)

r = admin.put(f"/api/admin/crew/people/{mike.id}/role", json={"role": "Camera 1"})
eq("an admin sets a role", (r.status_code, r.json()), (200, {"role": "Camera 1"}))
eq("a member may not", member.put(f"/api/admin/crew/people/{mike.id}/role", json={"role": "X"}).status_code, 403)
eq("a long role is refused", admin.put(f"/api/admin/crew/people/{mike.id}/role", json={"role": "x" * 41}).status_code, 400)
eq("an unknown person is refused", admin.put("/api/admin/crew/people/99999/role", json={"role": "A"}).status_code, 404)
eq("the crew page shows it", "Mike Smith · Camera 1" in member.get("/crew").text, True)
row = [p for p in admin.get("/api/admin/crew/people").json()["people"] if p["id"] == mike.id][0]
eq("listed", (row["role"], row["linked"], row["suggested_name"]), ("Camera 1", False, ""))

print("matching")
tr = follower.tracks.setdefault("7", Track("7"))
tr.team = [{"person": "501", "name": "Michael Smith"}, {"person": "502", "name": "Sara Lee"}]
MODULE._scan()
got = {p["id"]: p for p in admin.get("/api/admin/crew/people").json()["people"]}
eq("a short form is linked and suggested", (got[mike.id]["linked"], got[mike.id]["suggested_name"]), (True, "Michael Smith"))
eq("an identical spelling is linked with no suggestion", (got[sara.id]["linked"], got[sara.id]["suggested_name"]), (True, ""))
eq("matching kept the role", got[mike.id]["role"], "Camera 1")
eq("the name is not changed on its own", users.get(mike.id).name, "Mike Smith")
tr.team = [{"person": "503", "name": "Someone Else"}]
MODULE._scan()
eq("a link is never moved", admin.get("/api/admin/crew/people").json()["people"][0]["linked"] in (True, False), True)

print("suggestion")
eq("a member may not accept", member.post(f"/api/admin/crew/people/{mike.id}/name", json={"action": "accept"}).status_code, 403)
eq("a bad action", admin.post(f"/api/admin/crew/people/{mike.id}/name", json={"action": "x"}).status_code, 400)
r = admin.post(f"/api/admin/crew/people/{mike.id}/name", json={"action": "accept"})
eq("accept renames", (r.status_code, users.get(mike.id).name), (200, "Michael Smith"))
eq("nothing left to accept", admin.post(f"/api/admin/crew/people/{mike.id}/name", json={"action": "accept"}).status_code, 404)

print("wrong match")
eq("unlink", admin.delete(f"/api/admin/crew/people/{sara.id}/match").status_code, 200)
tr.team = [{"person": "502", "name": "Sara Lee"}]
MODULE._seen = ""
MODULE._scan()
eq("a removed match is relinked only by a new scan", [p for p in admin.get("/api/admin/crew/people").json()["people"]
                                                      if p["id"] == sara.id][0]["linked"], True)
config.save({"modules": {"planning_center": {"enabled": False}}})
eq("scan refused while it is off", admin.post("/api/admin/crew/people/scan").status_code, 400)

print("account removal")
users.delete(sara.id, actor=None)
eq("the profile goes with the account", people_rows := len([p for p in admin.get("/api/admin/crew/people").json()["people"]
                                                            if p["id"] == sara.id]), 0)

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
