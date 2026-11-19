"""Teams and positions from the directory, stored as a ref plus a saved name (#525). Run: python tests/test_teams_positions.py"""
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
from app import config, db, directory, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.crew import MODULE, calltimes, chat, checklists, notifications, people, refs, resources  # noqa: E402
from app.modules.planning_center import MODULE as PC, planningcenter as pc, rundown  # noqa: E402
from app.modules.planning_center.pclive import Track, follower  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
_db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
mike = users.get_by_username("mike")
users.update(mike.id, name="Mike Smith", actor=None)
people.ready()
modules.attach("planning_center")
MODULE.attach()

ZONE = ZoneInfo("America/New_York")
SUN = datetime(2026, 11, 15, 8, 0, tzinfo=ZONE).timestamp()
tr = follower.tracks.setdefault("7", Track("7"))
tr.catalog = [{"id": "12", "name": "Camera", "positions": [{"id": "34", "name": "Camera 1"}, {"id": "35", "name": "Camera 2"}]},
              {"id": "13", "name": "Sound", "positions": [{"id": "40", "name": "Mixer"}]}]
tr.team = [{"person": "501", "name": "Mike Smith", "team": "Camera", "team_id": "12", "position": "Camera 1",
            "position_id": "34", "status": "confirmed", "times": None},
           {"person": "502", "name": "Sara Lee", "team": "Sound", "team_id": "13", "position": "Mixer",
            "position_id": "40", "status": "confirmed", "times": None}]
directory.link(mike.id, "pco", "501", "Mike Smith")

with section("Planning Center's client lists teams with their positions"):
    class Fake(pc.Client):
        def __init__(self):
            super().__init__("id", "secret")

        def get(self, path, params=None):
            assert path == "/services/v2/service_types/7/teams" and params["include"] == "team_positions"
            return {"data": [{"type": "Team", "id": "12", "attributes": {"name": "Camera "},
                              "relationships": {"team_positions": {"data": [{"type": "TeamPosition", "id": "34"}]}}}],
                    "included": [{"type": "TeamPosition", "id": "34", "attributes": {"name": "Camera 1"}}]}

    eq("ids and names", Fake().teams("7"), [{"id": "12", "name": "Camera", "positions": [{"id": "34", "name": "Camera 1"}]}])
    rows = [{"team_id": "12", "position": "camera 1"}, {"team_id": "12", "position": "Gaffer"}]
    pc.tag_positions(rows, Fake().teams("7"))
    eq("a plan row gets its position's id by team and name", [r["position_id"] for r in rows], ["34", ""])

with section("the directory caches what Planning Center lists"):
    check("the first read adds them", directory.sync("pco"))
    eq("a team", [(t["ref"], t["name"]) for t in directory.teams()], [("team:pco:12", "Camera"), ("team:pco:13", "Sound")])
    eq("positions know their team", [(p["ref"], p["team"], p["name"]) for p in directory.positions()],
       [("position:pco:12/34", "Camera", "Camera 1"), ("position:pco:12/35", "Camera", "Camera 2"),
        ("position:pco:13/40", "Sound", "Mixer")])
    check("reading again changes nothing", not directory.sync("pco"))
    eq("refs", (directory.team_ref("pco", 12), directory.position_ref("pco", 12, 34), directory.parse_item("position:pco:12/34"),
                directory.parse_item("person:pco:1"), directory.team_of("position:pco:12/34")),
       ("team:pco:12", "position:pco:12/34", ("position", "pco", "12/34"), None, "team:pco:12"))
    eq("a name finds its team, ignoring case and accents", directory.match_name("team", "CAMERA "), "team:pco:12")
    eq("and nothing for an unknown name", directory.match_name("team", "Lights"), None)
    try:
        directory.clean_ref("team", "team:pco:99")
        check("a ref the directory never saw is refused", False)
    except ValueError:
        check("a ref the directory never saw is refused", True)
    eq("a listed ref is fine", directory.clean_ref("team", "team:pco:12"), "team:pco:12")

with section("saved names from before get a ref, and what cannot be matched is flagged"):
    db.execute("insert into crew_calltimes (team, kind, minutes, message, enabled, created_at)"
               " values ('camera', 'before', 60, 'Cameras', 1, ?), ('Lights', 'before', 30, 'Lights', 1, ?)", (db.now(), db.now()))
    db.execute("insert into crew_checklists (title, position, updated_at) values ('Setup', 'Mixer', ?), ('All', '', ?)",
               (db.now(), db.now()))
    db.execute("insert into crew_resources (position, kind, title, url, created_by, created_at)"
               " values ('Sound', 'link', 'Guide', 'https://x.example/g', 1, 0)")
    db.execute("insert into crew_checklist_leads (user_id, position) values (?, 'Camera 2')", (mike.id,))
    refs.adopt()
    rules = {r["message"]: r for r in calltimes.list_rules()}
    eq("a team name is matched and takes Planning Center's spelling", (rules["Cameras"]["team_ref"], rules["Cameras"]["team"]),
       ("team:pco:12", "Camera"))
    eq("one that matches nothing stays a name, flagged", (rules["Lights"]["team_ref"], rules["Lights"]["unmatched"],
                                                          rules["Cameras"]["unmatched"]), ("", True, False))
    eq("a checklist 'position' that is a position name finds the position",
       [(t["position_ref"], t["position"]) for t in checklists.templates() if t["title"] == "Setup"],
       [("position:pco:13/40", "Mixer")])
    eq("a checklist for everyone is left alone", [t["position_ref"] for t in checklists.templates() if t["title"] == "All"], [""])
    eq("resources too", [(r["position_ref"], r["position"]) for r in resources.all_rows()], [("team:pco:13", "Sound")])
    eq("and leads", [(r["position_ref"], r["position"]) for r in checklists.list_leads()], [("position:pco:12/35", "Camera 2")])

with section("the admin pages pick from a list and the server checks the pick"):
    r = admin.get("/api/admin/crew/calltimes").json()
    eq("call times offer the teams", [t["ref"] for t in r["teams"]], ["team:pco:12", "team:pco:13"])
    good = {"team_ref": "team:pco:13", "kind": "before", "minutes": 45, "message": "Sound call"}
    r = admin.post("/api/admin/crew/calltimes", json=good)
    eq("a call time saves with a ref and the directory's name", (r.status_code, [x["team"] for x in r.json()["rules"]
                                                                              if x["message"] == "Sound call"]), (200, ["Sound"]))
    eq("a ref that is not a listed team is refused", admin.post("/api/admin/crew/calltimes",
                                                                json={**good, "team_ref": "team:pco:99"}).status_code, 400)
    eq("a position is not a team", admin.post("/api/admin/crew/calltimes",
                                              json={**good, "team_ref": "position:pco:13/40"}).status_code, 400)
    r = admin.get("/crew/api/checklists/templates").json()
    eq("a checklist picks a team or a position", [p["label"] for p in r["picks"]],
       ["Camera", "Sound", "Camera · Camera 1", "Camera · Camera 2", "Sound · Mixer"])
    body = {"title": "Rig", "position_ref": "position:pco:12/34", "items": ["Tripod"]}
    r = admin.post("/crew/api/checklists/templates", json=body)
    eq("a checklist for one position", (r.status_code, [t["position"] for t in r.json()["templates"] if t["title"] == "Rig"]),
       (200, ["Camera 1"]))
    eq("a made-up position is refused", admin.post("/crew/api/checklists/templates",
                                                    json={**body, "position_ref": "position:pco:1/2"}).status_code, 400)
    r = admin.post("/api/admin/crew/checklist-leads", json={"user_id": mike.id, "position_ref": "team:pco:12"})
    eq("a lead is made from the list", (r.status_code, sorted(l["position"] for l in r.json()["leads"])),
       (200, ["Camera", "Camera 2"]))

with section("a rename in Planning Center keeps everything working and updates the saved names"):
    tr.catalog = [{"id": "12", "name": "Cameras", "positions": [{"id": "34", "name": "Camera One"}, {"id": "35", "name": "Camera 2"}]},
                  {"id": "13", "name": "Sound", "positions": [{"id": "40", "name": "Mixer"}]}]
    tr.team[0].update(team="Cameras", position="Camera One")
    refs.refresh()
    rules = {r["message"]: r for r in calltimes.list_rules()}
    eq("the call time shows the new name", rules["Cameras"]["team"], "Cameras")
    eq("and the saved name is the new one", db.one("select team from crew_calltimes where message = 'Cameras'")["team"], "Cameras")
    eq("the checklist for the renamed position", [(t["position"], t["missing"]) for t in checklists.templates() if t["title"] == "Rig"],
       [("Camera One", False)])
    eq("the resource and lead names follow", (db.one("select position from crew_checklist_leads where position_ref = 'position:pco:12/35'")["position"],
                                              db.one("select position from crew_checklist_leads where position_ref = 'team:pco:12'")["position"]),
       ("Camera 2", "Cameras"))
    sat = datetime(2026, 11, 15, 7, 0, tzinfo=ZONE).timestamp()
    rule = {**rules["Cameras"], "kind": "fixed", "weekday": 0, "clock": "07:00"}
    eq("the call time still reaches the people on the renamed team",
       [r["person"] for r in notifications.serving_for([tr], rule["team_ref"], rule["team"])], ["501"])
    eq("and still fires for them", [o[1] for o in calltimes.occasions(rule, [tr], sat, ZONE)], [sat])
    eq("the checklist still applies to who holds the position",
       checklists.applies({"position": "Camera 1", "position_ref": "position:pco:12/34"}, checklists.held(mike.id, [tr])), True)
    eq("a name-only checklist does not follow a rename", checklists.applies({"position": "Camera 1", "position_ref": ""},
                                                                            checklists.held(mike.id, [tr])), False)

with section("one deleted in Planning Center shows as missing, not silently ignored"):
    tr.catalog = [{"id": "13", "name": "Sound", "positions": [{"id": "40", "name": "Mixer"}]}]
    check("the directory notices", directory.sync("pco"))
    rules = {r["message"]: r for r in calltimes.list_rules()}
    eq("the call time says so and keeps its saved name", (rules["Cameras"]["missing"], rules["Cameras"]["team"]), (True, "Cameras"))
    eq("a checklist too", [t["missing"] for t in checklists.templates() if t["title"] == "Rig"], [True])
    eq("it is not offered to pick again", [t["ref"] for t in directory.teams() if not t["missing"]], ["team:pco:13"])
    eq("an empty read from Planning Center forgets nothing", (directory.sync("pco") or True, len(directory.teams())), (True, 2))
    tr.catalog = []
    check("so a Planning Center that has not answered changes nothing", not directory.sync("pco"))

with section("with Planning Center off, saved names still show"):
    PC.detach()
    eq("the cache answers", directory.describe("team:pco:13", "old"), {"ref": "team:pco:13", "name": "Sound", "missing": False,
                                                                       "unmatched": False})
    eq("and the call times list", [r["team"] for r in calltimes.list_rules() if r["message"] == "Sound call"], ["Sound"])
    modules.attach("planning_center")

with section("the crew role defaults from the usual position"):
    tr.catalog = [{"id": "12", "name": "Cameras", "positions": [{"id": "34", "name": "Camera One"}]},
                  {"id": "13", "name": "Sound", "positions": [{"id": "40", "name": "Mixer"}]}]
    directory.sync("pco")
    eq("nothing learned yet", people.usual_position(mike.id), "")
    people.record_positions([tr], today="2026-11-15")
    people.record_positions([tr], today="2026-11-15")
    eq("one day counts once", db.one("select times from crew_position_history where user_id = ?", (mike.id,))["times"], 1)
    people.record_positions([tr], today="2026-11-22")
    eq("a second day counts again", db.one("select times from crew_position_history where user_id = ?", (mike.id,))["times"], 2)
    eq("the usual position is the crew role", (people.profile(mike.id)["crew_role"], people.usual_position(mike.id)),
       ("Camera One", "Camera One"))
    row = [p for p in people.people_list() if p["id"] == mike.id][0]
    eq("the admin list shows both", (row["crew_role"], row["crew_role_set"], row["usual_position"]),
       ("Camera One", "", "Camera One"))
    check("the chat role channel follows it", ("role:camera one", "Camera One") in [(c["id"], c["title"]) for c in chat.channels_for(mike)])
    r = admin.put(f"/api/admin/crew/people/{mike.id}/role", json={"crew_role": "Camera 1"})
    eq("an admin overrides it", (r.status_code, r.json()), (200, {"crew_role": "Camera 1"}))
    eq("the override wins", (people.profile(mike.id)["crew_role"], people.profile(mike.id)["crew_role_set"]), ("Camera 1", "Camera 1"))
    r = admin.put(f"/api/admin/crew/people/{mike.id}/role", json={"crew_role": ""})
    eq("clearing it goes back to the usual position", r.json(), {"crew_role": "Camera One"})
    eq("the chat channel followed the clear", ("role:camera one", "Camera One") in [(c["id"], c["title"]) for c in chat.channels_for(mike)], True)

with section("the rundown's mic mapping follows ids"):
    rs = rundown.clean_rundown({"mics": [{"team": "Cameras", "position": "Camera One", "position_ref": "position:pco:12/34",
                                          "team_ref": "team:pco:12", "mics": ["01"]}],
                                "channels": [{"id": "c1", "name": "Lead", "positions": [
                                    {"service_type": "7", "team": "Cameras", "team_ref": "team:pco:12", "position": "Camera One",
                                     "position_ref": "position:pco:12/34"}]}]})
    eq("refs are kept", (rs["mics"][0]["position_ref"], rs["channels"][0]["positions"][0]["position_ref"]),
       ("position:pco:12/34", "position:pco:12/34"))
    eq("a ref that is not one is dropped", rundown.clean_rundown({"mics": [{"position": "X", "position_ref": "nope", "mics": ["01"]}]})
       ["mics"][0]["position_ref"], "")
    member = {"team_id": "12", "position_id": "34", "team": "Cameras", "position": "Renamed again"}
    check("a member matches by id whatever the names say", rundown._matches(rs["mics"][0], member))
    check("not another position", not rundown._matches(rs["mics"][0], {**member, "position_id": "35"}))
    check("a name-only mapping still matches by name", rundown._matches({"team": "", "position": "Renamed again", "mics": []}, member))
    old = rundown.clean_rundown({"mics": [{"team": "Sound", "position": "mixer", "mics": ["02"]}]})
    check("a name from before has no ref yet", old["mics"][0]["position_ref"] == "")
    check("refresh_saved adopts a ref and the current name", rundown.refresh_saved(old))
    eq("so the mapping is by id from now on", (old["mics"][0]["position_ref"], old["mics"][0]["position"], old["mics"][0]["team_ref"]),
       ("position:pco:13/40", "Mixer", "team:pco:13"))
    check("and a second pass has nothing to do", not rundown.refresh_saved(old))
    tr.catalog = [{"id": "13", "name": "Sound", "positions": [{"id": "40", "name": "Soundboard"}]}]
    directory.sync("pco")
    check("a rename reaches the saved mapping", rundown.refresh_saved(old))
    eq("with the new name", old["mics"][0]["position"], "Soundboard")
    tr.catalog = [{"id": "13", "name": "Sound", "positions": []}]
    directory.sync("pco")
    eq("a position deleted there is flagged", directory.describe("position:pco:13/40", "Soundboard")["missing"], True)
    r = admin.get("/api/admin/planning-center/rundown")
    eq("the admin read answers", r.status_code, 200)

done()
