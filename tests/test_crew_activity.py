"""Crew activity trail (#235) and the join QR page (#236). Run: python tests/test_crew_activity.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(TMP, "certs")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import audit, config, db, netinfo, serving, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import MODULE, calltimes, checkin, people  # noqa: E402

from _harness import check, eq, fails, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}}})
ADMIN = "http://relay.local:8001"
admin = _db.client(app, base_url=ADMIN)
booth = _db.client(app, role="operator", base_url=ADMIN, username="booth")
viewer = _db.client(app, role="viewer", base_url=ADMIN, username="vee")
mike_c = _db.client(app, role="member", base_url=ADMIN, username="mike")
mike = users.get_by_username("mike")
users.update(mike.id, name="Mike Smith", actor=None)

with section("what a crew member does is entered in the audit log"):
    people.ready()
    r = mike_c.post("/crew/api/chat/send", json={"channel": "team", "body": "secret words 123"})
    eq("a message is sent", r.status_code, 200)
    checkin.is_lan = lambda host: True
    eq("checking in works", mike_c.post("/crew/api/checkin", json={}).status_code, 200)
    checkin.is_lan = lambda host: False
    rows = audit.search(kind="crew")["entries"]
    acts = [e["action"] for e in rows]
    check("chat and check-in are entered", "crew.chat_send" in acts and "crew.checkin" in acts)
    e = next(e for e in rows if e["action"] == "crew.chat_send")
    eq("by whom", e["actor_username"], "mike")
    check("the message text is not stored", "secret words" not in repr(rows))
    mid = r.json()["message"]["id"]
    eq("deleting one", mike_c.delete(f"/crew/api/chat/message/{mid}").status_code, 200)
    check("and that is entered", "crew.chat_delete" in [e["action"] for e in audit.search(kind="crew")["entries"]])
    r = booth.post("/crew/api/checklists/templates", json={"position": "", "title": "Open up", "items": ["Lights"]})
    if r.status_code == 200:
        check("a checklist edit from the crew page is entered (it used to be dropped)",
              "crew.checklist_add" in [e["action"] for e in audit.search(kind="crew")["entries"]])

with section("who may read it"):
    eq("a member may not", mike_c.get("/crew/api/activity").status_code, 403)
    eq("a viewer may not", viewer.get("/crew/api/activity").status_code, 403)
    r = booth.get("/crew/api/activity")
    eq("an operator may", r.status_code, 200)
    got = r.json()
    check("only crew entries", got["entries"] and all(e["action"].startswith("crew.") for e in got["entries"]))
    check("the people who acted are offered", any(p["name"] == "Mike Smith" for p in got["people"]))
    eq("filter by person", {e["actor_username"] for e in booth.get(f"/crew/api/activity?actor={mike.id}").json()["entries"]}, {"mike"})
    eq("filter by kind", {e["action"] for e in booth.get("/crew/api/activity?kind=checkin").json()["entries"]}, {"crew.checkin"})
    eq("an unknown kind finds nothing, never other areas", booth.get("/crew/api/activity?kind=signin").json()["entries"], [])
    check("the Activity tab shows to an operator only", ">Activity<" in booth.get("/crew").text and ">Activity<" not in mike_c.get("/crew").text)

with section("the join page and QR"):
    serving.publish(8000, 8001)
    j = admin.get("/api/admin/crew/join")
    eq("admin gets the addresses", j.status_code, 200)
    eq("a viewer may read them too", viewer.get("/api/admin/crew/join").status_code, 200)
    eq("a member may not", mike_c.get("/api/admin/crew/join").status_code, 403)
    check("the crew address is HTTPS on the panel port, not the viewer port",
          j.json()["crew_url"] == "" or j.json()["crew_url"].startswith("https://") and ":8001/crew" in j.json()["crew_url"])
    check("the join address is plain HTTP on its own port",
          j.json()["join_url"] == "" or j.json()["join_url"].startswith("http://") and ":8095/crew-join" in j.json()["join_url"])
    eq("the default port", config.get()["crew"]["join_port"], 8095)
    eq("a bad port falls back", MODULE.clean({"join_port": 80})["join_port"], 8095)
    JOIN = "http://192.168.1.5:8095"
    serving.publish_listeners({8095: ("/crew-join",)})
    from fastapi.testclient import TestClient
    phone = TestClient(app, base_url=JOIN)
    r = phone.get("/crew-join")
    eq("the join page needs no sign-in", r.status_code, 200)
    check("it names the HTTPS crew address on the host the phone used", "https://192.168.1.5:8001/crew" in r.text)
    check("without a certificate it says so", "no certificate yet" in r.text)
    eq("and there is nothing to download", phone.get("/crew-join/relay.crt").status_code, 404)
    os.makedirs(os.environ["RELAY_CERT_DIR"], exist_ok=True)
    Path(os.environ["RELAY_CERT_DIR"], "admin.crt").write_text("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
    Path(os.environ["RELAY_CERT_DIR"], "admin.key").write_text("PRIVATE")
    r = phone.get("/crew-join/relay.crt")
    eq("the certificate downloads", (r.status_code, r.headers["content-type"]), (200, "application/x-x509-ca-cert"))
    check("never the key", "PRIVATE" not in r.text)
    check("the page now offers it", "Download the certificate" in phone.get("/crew-join").text)
    eq("the join page is not on the HTTPS port", admin.get("/crew-join").status_code, 404)
    eq("the listener serves nothing else", phone.get("/crew").status_code, 404)
    serving.publish_listeners({})
    serving.reset()

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
