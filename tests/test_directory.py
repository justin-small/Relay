"""Core person links and app/directory.py (#524). Run: python tests/test_directory.py"""
import os
import shutil
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
from app import config, db, directory, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.crew import MODULE, people  # noqa: E402
from app.modules.planning_center.pclive import follower, Track  # noqa: E402

from _harness import check, done, eq, section, tmpdir  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
_db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
_db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
users.update(mike.id, name="Mike Smith", actor=None)
users.update(sara.id, name="Sara Lee", actor=None)

with section("links from before the move survive the Crew migration"):
    old = Path(tmpdir()) / "crew"
    old.mkdir()
    for f in sorted((Path(people.MIGRATIONS)).glob("*.sql")):
        if f.stem < "0010":
            shutil.copy(f, old / f.name)
    db.migrate("crew", old)                      # Crew as it was: the link lives in crew_profiles
    now = db.now()
    db.execute("insert into crew_profiles (user_id, role, pc_person, pc_name, suggested_name, dismissed_name, updated_at)"
               " values (?, 'Camera 1', '501', 'Michael Smith', 'Michael Smith', null, ?)", (mike.id, now))
    db.execute("insert into crew_profiles (user_id, role, pc_person, pc_name, suggested_name, dismissed_name, updated_at)"
               " values (?, 'Audio', null, null, null, 'Sarah Lee', ?)", (sara.id, now))
    people.forget()
    people.ready()                               # applies 0010
    eq("the link is in core", directory.user_for("pco", "501"), mike.id)
    got = directory.link_of(mike.id, "pco")
    eq("with its saved and suggested name", (got["name"], got["suggested_name"], got["ref"]),
       ("Michael Smith", "Michael Smith", "person:pco:501"))
    eq("a refused spelling is kept", directory.dismissed(sara.id, "pco"), "Sarah Lee")
    eq("nobody else is linked", directory.links("pco"), {"501": mike.id})
    eq("the roles stayed", (people.profile(mike.id)["crew_role"], people.profile(sara.id)["crew_role"]), ("Camera 1", "Audio"))
    cols = {r["name"] for r in db.query("pragma table_info(crew_profiles)")}
    eq("crew_profiles no longer holds the link, and its role is the crew role", cols, {"user_id", "crew_role", "updated_at"})

with section("reading"):
    eq("user_for", directory.user_for("pco", 501), mike.id)
    eq("an unknown person", directory.user_for("pco", "999"), None)
    eq("another provider is separate", directory.user_for("other", "501"), None)
    eq("external_id", (directory.external_id(mike.id, "pco"), directory.external_id(sara.id, "pco")), ("501", None))
    p = directory.person(mike.id)
    eq("person", (p["ref"], p["name"], sorted(p["links"])), (f"person:relay:{mike.id}", "Mike Smith", ["pco"]))
    eq("no such user", directory.person(99999), None)
    eq("refs", (directory.ref("pco", 7), directory.parse_ref("person:pco:7"), directory.parse_ref("x:pco:7"),
                directory.parse_ref("person:pco"), directory.parse_ref(5)), ("person:pco:7", ("pco", "7"), None, None, None))
    eq("a pco ref names the linked user", directory.user_from_ref("person:pco:501"), mike.id)
    eq("a relay ref names the user", directory.user_from_ref(directory.ref_for_user(sara.id)), sara.id)
    eq("a relay ref to nobody", directory.user_from_ref("person:relay:99999"), None)

with section("writing"):
    check("link", directory.link(sara.id, "pco", "502", "Sara Lee", matched_by="admin", confirmed_by=mike.id))
    eq("how it was made", (directory.link_of(sara.id, "pco")["matched_by"], directory.link_of(sara.id, "pco")["confirmed_by"]),
       ("admin", mike.id))
    check("a person held by another user is refused", not directory.link(sara.id, "pco", "501", "Mike"))
    eq("and nothing changed", directory.external_id(sara.id, "pco"), "502")
    directory.unlink(sara.id, "pco")
    eq("unlink forgets it", directory.user_for("pco", "502"), None)
    eq("and remembers the spelling", directory.dismissed(sara.id, "pco"), "Sara Lee")

with section("with Crew off, the link still answers"):
    MODULE.attach()
    MODULE.detach()                              # Crew switched off
    eq("intercom, the rundown and paging ask core", directory.user_for("pco", "501"), mike.id)
    eq("the whole map too", directory.links("pco"), {"501": mike.id})

with section("Planning Center provides the people"):
    eq("nobody while it is off", directory.people("pco"), [])
    modules.attach("planning_center")
    tr = follower.tracks.setdefault("7", Track("7"))
    tr.team = [{"person": "501", "name": "Michael Smith"}, {"person": "503", "name": "Someone Else"},
               {"person": "501", "name": "Michael Smith"}, {"name": "No id"}]
    eq("each person once", directory.people("pco"),
       [{"id": "501", "name": "Michael Smith"}, {"id": "503", "name": "Someone Else"}])

with section("Crew matches through the directory"):
    MODULE.attach()
    tr.team = [{"person": "501", "name": "Michael Smith"}, {"person": "502", "name": "Sara Lee"}]
    MODULE._seen = ""
    MODULE._scan()
    eq("a new match is stored in core", directory.user_for("pco", "502"), sara.id)
    eq("a link is never moved", directory.user_for("pco", "501"), mike.id)
    row = [p for p in people.people_list() if p["id"] == sara.id][0]
    eq("the admin list reads it", (row["linked"], row["pc_name"], row["suggested_name"]), (True, "Sara Lee", ""))
    r = admin.post(f"/api/admin/crew/people/{mike.id}/name", json={"action": "accept"})
    eq("accepting the suggested name renames the account", (r.status_code, users.get(mike.id).name), (200, "Michael Smith"))
    eq("and clears the suggestion", directory.suggestion(mike.id, "pco"), None)
    eq("unlinking through crew", admin.delete(f"/api/admin/crew/people/{sara.id}/match").status_code, 200)
    eq("removes it from core", directory.user_for("pco", "502"), None)

with section("a user's links go when the user does"):
    db.execute("delete from users where id = ?", (mike.id,))
    eq("no link left", directory.user_for("pco", "501"), None)

done()
