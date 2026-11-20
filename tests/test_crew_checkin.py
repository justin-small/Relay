"""Crew check-in and the arrival board (#231): arrival from the church network or inside a geofence, the board
against call times, and arrivals that clear when a new plan takes over. Run: python tests/test_crew_checkin.py"""
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
from app.modules.crew import MODULE, calltimes, checkin, people  # noqa: E402
from app.modules.planning_center.pclive import Track  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import eq, fails  # noqa: E402


print("addresses")
for host, want in (("192.168.1.20", True), ("10.0.5.9", True), ("172.16.0.4", True), ("fe80::1", True),
                   ("127.0.0.1", False), ("::1", False), ("8.8.8.8", False), ("0.0.0.0", False),
                   ("testclient", False), ("", False), (None, False)):
    eq(f"{host!r} {'is' if want else 'is not'} the church network", checkin.is_lan(host), want)

print("the geofence")
eq("none set", checkin.clean_geofence(None), None)
eq("blank fields are none", checkin.clean_geofence({"lat": None, "lon": None, "radius_m": 150}), None)
eq("a good one", checkin.clean_geofence({"lat": 40.7128, "lon": -74.006, "radius_m": 200}),
   {"lat": 40.7128, "lon": -74.006, "radius_m": 200})
eq("radius defaults", checkin.clean_geofence({"lat": 1, "lon": 2})["radius_m"], 150)
for label, bad in (("half filled", {"lat": 40.0, "lon": None}), ("off the map", {"lat": 91, "lon": 0}),
                   ("text", {"lat": "40", "lon": "1"}), ("a bool", {"lat": True, "lon": 1}),
                   ("too small", {"lat": 1, "lon": 2, "radius_m": 5}), ("too big", {"lat": 1, "lon": 2, "radius_m": 9000}),
                   ("not a dict", "x"), ("infinite", {"lat": float("inf"), "lon": 1})):
    try:
        checkin.clean_geofence(bad)
        eq(f"refused: {label}", False, True)
    except ValueError:
        eq(f"refused: {label}", True, True)
eq("distance across 0.001 degrees of latitude is about 111 m", 100 < checkin.distance_m(40, -74, 40.001, -74) < 120, True)
FENCE = {"lat": 40.7128, "lon": -74.006, "radius_m": 150}
eq("inside", checkin.inside(FENCE, 40.7130, -74.006, 20), True)
eq("outside", checkin.inside(FENCE, 40.7200, -74.006, 20), False)
eq("a rough fix does not count", checkin.inside(FENCE, 40.7130, -74.006, 900), False)
eq("no accuracy does not count", checkin.inside(FENCE, 40.7130, -74.006, None), False)
eq("no geofence", checkin.inside(None, 40.7130, -74.006, 20), False)
eq("nonsense", checkin.inside(FENCE, "40", -74.006, 20), False)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
booth = _db.client(app, role="operator", base_url="http://relay.local:8001", username="booth")
mike_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
sara_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
users.update(mike.id, name="Mike Smith", actor=None)
users.update(sara.id, name="Sara Lee", actor=None)

print("the settings")
eq("none by default", config.get()["crew"]["geofence"], None)
r = admin.post("/api/admin/crew", json={"name": "Relay crew", "show_plan": True,
                                        "geofence": {"lat": 40.7128, "lon": -74.006, "radius_m": 150}})
eq("an admin saves a geofence", (r.status_code, r.json()["geofence"]), (200, FENCE))
eq("a bad one is refused", admin.post("/api/admin/crew", json={"name": "A", "show_plan": True,
                                                              "geofence": {"lat": 99, "lon": 0}}).status_code, 400)
eq("a hand edit that makes no sense is no geofence", MODULE.clean({"geofence": {"lat": "x"}})["geofence"], None)
eq("the crew page may use the location", "geolocation=(self)" in mike_c.get("/crew").headers["permissions-policy"], True)
eq("no other page may", "geolocation=()" in mike_c.get("/home").headers["permissions-policy"], True)

print("checking in")
ZONE = ZoneInfo("America/New_York")
SUN = datetime(2026, 11, 15, 8, 0, tzinfo=ZONE).timestamp()
S1 = SUN + 3600                                    # the service is at 9:00
tr = Track("7")
tr.plan = {"id": "700", "times": [{"id": "t1", "type": "service", "starts_at": S1}]}
tr.team = [{"person": "501", "name": "Mike Smith", "team": "Camera", "status": "confirmed", "times": None},
           {"person": "502", "name": "Sara Lee", "team": "Camera", "status": "confirmed", "times": None},
           {"person": "503", "name": "Drum Kit", "team": "Band", "status": "confirmed", "times": None}]
TRACKS = [tr]
calltimes._state = lambda: (TRACKS, ZONE)
people.ready()
for uid, pid in ((mike.id, "501"), (sara.id, "502")):
    directory.link(uid, directory.PCO, pid, "x")
st = mike_c.get("/crew/api/checkin").json()
eq("not arrived, not on the network", (st["arrived"], st["lan"], st["geofence"]), (False, False, True))
eq("away from the network with no position is refused", mike_c.post("/crew/api/checkin", json={}).status_code, 403)
eq("outside the geofence is refused", mike_c.post("/crew/api/checkin", json={"lat": 40.73, "lon": -74.0, "accuracy": 10}).status_code, 403)
eq("a rough fix is refused", mike_c.post("/crew/api/checkin", json={"lat": 40.7130, "lon": -74.006, "accuracy": 800}).status_code, 403)
r = mike_c.post("/crew/api/checkin", json={"lat": 40.7130, "lon": -74.006, "accuracy": 15})
eq("inside the geofence, a tap checks in", (r.status_code, r.json()["arrived"], r.json()["method"]), (200, True, "geofence"))
first = r.json()["ts"]
eq("checking in again keeps the first arrival", mike_c.post("/crew/api/checkin", json={"lat": 40.7130, "lon": -74.006, "accuracy": 15}).json()["ts"], first)
checkin.is_lan = lambda host: True
eq("on the network the state says so", sara_c.get("/crew/api/checkin").json()["lan"], True)
r = sara_c.post("/crew/api/checkin", json={})
eq("on the network no position is needed", (r.status_code, r.json()["method"]), (200, "network"))
eq("no coordinates are kept", sorted(c["name"] for c in db.query("pragma table_info(crew_arrivals)")),
   ["arrived_at", "method", "plan_id", "user_id"])
checkin.is_lan = lambda host: False

print("the board")
rule = calltimes.clean({"team": "Camera", "kind": "before", "minutes": 60, "message": "Camera call time"})
calltimes.save(rule)                               # call time 8:00 for the Camera team
db.execute("delete from crew_arrivals")
key = checkin.plan_key(TRACKS, ZONE, SUN)
eq("the key is the plan", key, "700")
checkin.arrive(mike.id, key, "network", now=SUN - 600)            # 7:50, before the call
checkin.arrive(sara.id, key, "geofence", now=SUN + 300)           # 8:05, after the call
b = checkin.board(TRACKS, ZONE, now=SUN + 600)
got = {p["name"]: p["state"] for p in b["people"]}
eq("on time, late, and the band person has no account", got, {"Mike Smith": "arrived", "Sara Lee": "late_arrival",
                                                              "Drum Kit": "no_account"})
eq("counts leave out people with no account", (b["arrived"], b["total"]), (2, 2))
db.execute("delete from crew_arrivals where user_id = ?", (sara.id,))
eq("not arrived after the call time is overdue", {p["name"]: p["state"] for p in checkin.board(TRACKS, ZONE, now=SUN + 600)["people"]}["Sara Lee"], "overdue")
eq("before it, they are only waiting", {p["name"]: p["state"] for p in checkin.board(TRACKS, ZONE, now=SUN - 1800)["people"]}["Sara Lee"], "waiting")
eq("ordered by call time then name", [p["name"] for p in checkin.board(TRACKS, ZONE, now=SUN)["people"]],
   ["Mike Smith", "Sara Lee", "Drum Kit"])

print("the admin board")
eq("a member may not read it", mike_c.get("/api/admin/crew/arrivals").status_code, 403)
r = booth.get("/api/admin/crew/arrivals")
eq("an operator reads it", (r.status_code, r.json()["plan"]), (200, "700"))
eq("an operator clears it", (booth.delete("/api/admin/crew/arrivals").status_code,
                             db.one("select count(*) as n from crew_arrivals")["n"]), (200, 0))

print("a new plan takes over")
checkin.arrive(mike.id, "700", "network")
tr.plan = {"id": "701", "times": [{"id": "t9", "type": "service", "starts_at": S1 + 86400 * 7}]}
b = checkin.board(TRACKS, ZONE, now=SUN)
eq("the old arrivals are gone", (db.one("select count(*) as n from crew_arrivals")["n"], b["plan"]), (0, "701"))
eq("with no plan, the day is the key", checkin.plan_key([], ZONE, SUN), "day:2026-11-15")

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
