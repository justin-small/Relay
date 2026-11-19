"""Scheduled call-time pages (#229): rules per Planning Center team, evaluated on the host, once per occasion and
never late. Run: python tests/test_crew_calltimes.py"""
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
from app.modules.crew import MODULE, calltimes, paging  # noqa: E402
from app.modules.planning_center.pclive import Track  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", base_url="http://relay.local:8001", username="watcher")
mike_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
sara_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
users.update(mike.id, name="Mike Smith", actor=None)
users.update(sara.id, name="Sara Lee", actor=None)

ZONE = ZoneInfo("America/New_York")
SUN = datetime(2026, 11, 15, 8, 0, tzinfo=ZONE).timestamp()           # a Sunday, 8:00 local
S1, S2 = SUN + 3600, SUN + 3 * 3600                                     # services at 9:00 and 11:00
tr = Track("7")
tr.plan = {"id": "700", "times": [{"id": "t1", "type": "service", "starts_at": S1},
                                  {"id": "t2", "type": "service", "starts_at": S2},
                                  {"id": "r1", "type": "rehearsal", "starts_at": SUN}]}
tr.team = [{"person": "501", "name": "Mike Smith", "team": "Camera", "status": "confirmed", "times": None},
           {"person": "502", "name": "Sara Lee", "team": "Camera", "status": "confirmed", "times": ["t2"]},
           {"person": "503", "name": "Drum Kit", "team": "Band", "status": "confirmed", "times": None}]
TRACKS = [tr]
from app.modules.crew import people  # noqa: E402
people.ready()
for uid, pid, name in ((mike.id, "501", "Mike Smith"), (sara.id, "502", "Sara Lee")):
    directory.link(uid, directory.PCO, pid, name)

print("checking a rule")
good = {"team": "Camera", "kind": "before", "minutes": 60, "message": "Camera call time"}
eq("a rule is cleaned", calltimes.clean(good), {"team": "Camera", "team_ref": "", "message": "Camera call time", "enabled": True,
                                                "minutes": 60, "weekday": None, "clock": None, "schedule_ref": "", "kind": "before"})
for label, bad in (("no team", {**good, "team": " "}), ("no words", {**good, "message": ""}),
                   ("too long", {**good, "message": "x" * 141}), ("minutes as text", {**good, "minutes": "60"}),
                   ("minutes as a bool", {**good, "minutes": True}), ("minutes too big", {**good, "minutes": 1441}),
                   ("no kind", {"team": "Camera", "message": "hi"}),
                   ("a bad day", {"team": "C", "kind": "fixed", "weekday": 7, "clock": "08:00", "message": "hi"}),
                   ("a bad clock", {"team": "C", "kind": "fixed", "weekday": 1, "clock": "8am", "message": "hi"}),
                   ("a clock past midnight", {"team": "C", "kind": "fixed", "weekday": 1, "clock": "24:00", "message": "hi"})):
    try:
        calltimes.clean(bad)
        eq(f"refused: {label}", False, True)
    except paging.Invalid:
        eq(f"refused: {label}", True, True)

print("the admin page")
eq("a viewer reads", viewer.get("/api/admin/crew/calltimes").status_code, 200)
eq("a viewer may not add", viewer.post("/api/admin/crew/calltimes", json=good).status_code, 403)
r = admin.post("/api/admin/crew/calltimes", json=good)
eq("an admin adds", (r.status_code, len(r.json()["rules"])), (200, 1))
rid = r.json()["rules"][0]["id"]
eq("a bad rule is refused", admin.post("/api/admin/crew/calltimes", json={**good, "minutes": -5}).status_code, 400)
eq("unknown rule", admin.put("/api/admin/crew/calltimes/9999", json=good).status_code, 404)
r = admin.put(f"/api/admin/crew/calltimes/{rid}", json={**good, "enabled": False})
eq("turned off", r.json()["rules"][0]["enabled"], False)
admin.put(f"/api/admin/crew/calltimes/{rid}", json=good)
eq("fixed rule is cleaned and kept", admin.post("/api/admin/crew/calltimes", json={
    "team": "Band", "kind": "fixed", "weekday": 0, "clock": "08:30", "message": "Band soundcheck"}).status_code, 200)

print("minutes before each service")
sent_before = db.one("select count(*) as n from crew_pages")["n"]
eq("early: nothing", calltimes.run(S1 - 3600 - 5, TRACKS, ZONE), 0)
eq("at 8:00 (an hour before 9:00) Camera is paged, only those serving the 9:00", calltimes.run(S1 - 3600, TRACKS, ZONE), 1)
page = db.one("select id, message from crew_pages order by id desc limit 1")
eq("with the rule's words", page["message"], "Camera call time")
eq("to the person serving that time only", [r["user_id"] for r in db.query(
    "select user_id from crew_page_recipients where page_id = ?", (page["id"],))], [mike.id])
eq("not twice for the same service", calltimes.run(S1 - 3600 + 30, TRACKS, ZONE), 0)
eq("never late: a minute after is skipped", calltimes.run(S1 - 3600 + 61, TRACKS, ZONE), 0)
eq("the 11:00 service has its own call, for both people", calltimes.run(S2 - 3600, TRACKS, ZONE), 1)
p2 = db.one("select id from crew_pages order by id desc limit 1")["id"]
eq("both serve the 11:00", sorted(r["user_id"] for r in db.query(
    "select user_id from crew_page_recipients where page_id = ?", (p2,))), sorted([mike.id, sara.id]))
eq("the rehearsal is not a service", db.one("select count(*) as n from crew_pages")["n"] - sent_before, 2)
last = [r for r in calltimes.list_rules() if r["id"] == rid][0]
eq("the list shows the last run", (last["last_people"], last["last_ts"] is not None), (2, True))

print("minutes before a named schedule (#526)")
from app import schedules as sched_mod  # noqa: E402
from app.modules.crew import MODULE as _crew  # noqa: E402,F401

config.save({"timezone": "America/New_York"})
svc = sched_mod.validate({"name": "Sunday services", "windows": [
    {"repeat": "weekly", "days": [0], "start_time": "09:00", "stop_time": "10:30"},
    {"repeat": "weekly", "days": [0], "start_time": "11:00", "stop_time": "12:30"}]})
config.save({"schedules": [svc]})
ref = sched_mod.ref(svc["id"])
named = calltimes.clean({**good, "schedule_ref": ref})
eq("a rule may name a schedule", (named["kind"], named["schedule_ref"]), ("before", ref))
try:
    calltimes.clean({**good, "schedule_ref": "schedule:relay:nope"})
    eq("a schedule that is not there is refused", False, True)
except paging.Invalid:
    eq("a schedule that is not there is refused", True, True)
nid = calltimes.save(named)
rule = [r for r in calltimes.list_rules() if r["id"] == nid][0]
eq("the list names the schedule", (rule["schedule_ref"], rule["schedule_name"]), (ref, "Sunday services"))
eq("each window of today starts a call time, minutes before it",
   [(round(at - SUN), tid) for _o, at, tid in calltimes.occasions(rule, TRACKS, SUN, ZONE)], [(0, None), (7200, None)])
eq("the call times of a schedule are reported to the Schedules page", calltimes.schedule_users(ref),
   [{"where": "Crew", "what": f"Call time for {rule['team']}, 60 min before"}])
eq("a day the schedule has no window is quiet", calltimes.occasions(rule, TRACKS, SUN + 86400, ZONE), [])
config.save({"schedules": [dict(svc, windows=[dict(svc["windows"][0], start_time="10:00", stop_time="11:00")])]})
eq("moving the schedule moves the call time",
   [round(at - SUN) for _o, at, _t in calltimes.occasions(rule, TRACKS, SUN, ZONE)], [3600])
config.save({"schedules": [dict(svc, enabled=False)]})
eq("a schedule that is off pages nobody", calltimes.occasions(rule, TRACKS, SUN, ZONE), [])
calltimes.delete(nid)
config.save({"schedules": []})

print("a fixed day and time")
at = datetime(2026, 11, 15, 8, 30, tzinfo=ZONE).timestamp()
eq("a minute early", calltimes.run(at - 60, TRACKS, ZONE), 0)
eq("Band at 08:30 Sunday has nobody with an account: no page", calltimes.run(at, TRACKS, ZONE), 0)
eq("and it is not retried that day", calltimes.run(at + 20, TRACKS, ZONE), 0)
monday = datetime(2026, 11, 16, 8, 30, tzinfo=ZONE).timestamp()
eq("not on another weekday", calltimes.run(monday, TRACKS, ZONE), 0)
eq("a team nobody is serving gets nothing", calltimes.occasions({**good, "team": "Lights", "kind": "fixed", "weekday": 0,
                                                                  "clock": "08:30"}, TRACKS, at, ZONE), [])
eq("Band has no account matched, so the run records nobody", [r["last_people"] for r in calltimes.list_rules()
                                                              if r["team"] == "Band"], [0])

print("the booth-off screen (#234)")
admin.put(f"/api/admin/crew/calltimes/{rid}", json=good)
nx = calltimes.next_for(mike.id, TRACKS, ZONE, SUN)
eq("Mike's next call is an hour before the 9:00", (nx["ts"], nx["team"]), (S1 - 3600, "Camera"))
eq("after the first call, the 11:00's", calltimes.next_for(mike.id, TRACKS, ZONE, S1 - 3000)["ts"], S2 - 3600)
eq("Sara serves only the 11:00", calltimes.next_for(sara.id, TRACKS, ZONE, SUN)["ts"], S2 - 3600)
eq("none once every call has passed", calltimes.next_for(mike.id, TRACKS, ZONE, S2), None)
eq("none with no plan", calltimes.next_for(mike.id, [], ZONE, SUN), None)
t = mike_c.get("/crew/api/today")
eq("today answers a member", (t.status_code, sorted(t.json())), (200, ["call", "role", "running", "teams", "zone"]))
eq("capture is not running here", t.json()["running"], False)

print("off, and no Planning Center")
admin.put(f"/api/admin/crew/calltimes/{rid}", json={**good, "enabled": False})
db.execute("delete from crew_calltime_runs")
eq("a rule that is off does not fire", calltimes.run(S1 - 3600, TRACKS, ZONE), 0)
eq("nothing followed: nothing happens", calltimes.run(S1 - 3600, [], ZONE), 0)

print("the pager runs it on the host")
eq("it is called by the module's pager, not a phone", "calltimes.run" in open("app/modules/crew/__init__.py").read(), True)

print("removing")
eq("removed", len(admin.delete(f"/api/admin/crew/calltimes/{rid}").json()["rules"]), 1)
eq("its runs went too", db.one("select count(*) as n from crew_calltime_runs where rule_id = ?", (rid,))["n"], 0)

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
