"""Intercom's admin and phone-management routes (app/modules/intercom/routes.py): who may call each, what a
phone looks like to the pages (no credential), renaming and signing out your own phones versus anyone's, and
bodies that are not JSON or too large. Offline. Run: python tests/test_intercom_routes.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section, tmpdir  # noqa: E402

TMP = tmpdir()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.intercom import devices  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": False}}})
BASE = "http://relay.local:8001"
owner = _db.client(app, base_url=BASE)
viewer = _db.client(app, role="viewer", base_url=BASE)
alice = _db.client(app, role="member", base_url=BASE, username="alice")
bob = _db.client(app, role="member", base_url=BASE, username="bob")
anon = TestClient(app, base_url=BASE)
ALICE = users.get_by_username("alice").id
BOB = users.get_by_username("bob").id

with section("off"):
    eq("the admin view is gone while the module is off", owner.get("/api/admin/intercom").status_code, 404)
    eq("so is signing a phone in", alice.post("/intercom/api/phones", json={}).status_code, 404)

config.save({"modules": {"intercom": {"enabled": True}}})

with section("the media server view"):
    r = viewer.get("/api/admin/intercom")
    eq("a viewer may see LiveKit's state", r.status_code, 200)
    view = r.json()
    check("…with its ports and log, and no key or secret",
          {"state", "ports", "log", "binary"} <= set(view) and "secret" not in str(view).lower()
          and "api_key" not in str(view).lower(), sorted(view))
    eq("a viewer may not restart it", viewer.post("/api/admin/intercom/restart").status_code, 403)
    eq("a member may not see it", alice.get("/api/admin/intercom").status_code, 403)
    eq("nobody signed out may", anon.get("/api/admin/intercom").status_code in (401, 303, 403), True)

with section("signing a phone in"):
    r = alice.post("/intercom/api/phones", json={"name": "Alice's phone", "platform": "ios"})
    eq("a member keeps a phone signed in", r.status_code, 200)
    body = r.json()
    check("…and gets its credential once", isinstance(body.get("credential"), str) and len(body["credential"]) > 20)
    A1 = body["phone"]["id"]
    eq("the phone's name is kept", body["phone"]["name"], "Alice's phone")
    r = alice.post("/intercom/api/phones", content=b"not json", headers={"content-type": "application/json"})
    eq("a body that is not JSON still makes a phone, with the defaults", r.status_code, 200)
    A2 = r.json()["phone"]["id"]
    r = alice.post("/intercom/api/phones", content=b"[1, 2]", headers={"content-type": "application/json"})
    eq("a JSON body that is not an object is treated as empty", r.status_code, 200)
    A3 = r.json()["phone"]["id"]
    r = alice.post("/intercom/api/phones", content=b'{"name": "' + b"x" * 4000 + b'"}',
                   headers={"content-type": "application/json"})
    eq("a body over the limit is ignored, not stored", (r.status_code, len(r.json()["phone"]["name"]) < 100),
       (200, True))
    A4 = r.json()["phone"]["id"]
    B1 = bob.post("/intercom/api/phones", json={"name": "Bob's"}).json()["phone"]["id"]
    check("nobody signed out may sign a phone in", anon.post("/intercom/api/phones", json={}).status_code
          in (401, 303, 403))

with section("my phones"):
    r = alice.get("/intercom/api/my-phones")
    eq("a member lists only their own phones", sorted(p["id"] for p in r.json()["phones"]), sorted([A1, A2, A3, A4]))
    p = next(p for p in r.json()["phones"] if p["id"] == A1)
    eq("a phone as the page sees it: no credential or hash",
       sorted(p), ["id", "last_seen", "name", "platform", "renews_at", "signed_in", "status", "user", "user_id"])
    eq("…not connected, so idle", p["status"], "Idle")
    eq("…on the platform it said", p["platform"], "ios")

    eq("rename your own phone", alice.post(f"/intercom/api/my-phones/{A1}/rename", json={"name": "Stage"}).json(),
       {"ok": True})
    eq("…the name changes", next(p["name"] for p in alice.get("/intercom/api/my-phones").json()["phones"]
                                if p["id"] == A1), "Stage")
    r = alice.post(f"/intercom/api/my-phones/{B1}/rename", json={"name": "Mine now"})
    check("renaming someone else's phone is refused", r.status_code >= 400 and "error" in r.json(), r.status_code)
    r = alice.post(f"/intercom/api/my-phones/{A1}/rename", json={"name": "   "})
    check("an empty name is refused with a message", r.status_code >= 400 and r.json().get("error"), r.text)

    eq("sign out someone else's phone: not found", alice.delete(f"/intercom/api/my-phones/{B1}").status_code, 404)
    eq("sign out your own", alice.delete(f"/intercom/api/my-phones/{A2}").json(), {"ok": True})
    eq("…twice: not found", alice.delete(f"/intercom/api/my-phones/{A2}").status_code, 404)

with section("an old phone"):
    with db.transaction() as tx:
        old = db.later(-40 * 86400)
        tx.execute("update intercom_devices set last_seen_at = ?, issued_at = ? where id = ?", (old, old, A3))
    eq("a phone not seen in 30 days shows as expired",
       next(p["status"] for p in alice.get("/intercom/api/my-phones").json()["phones"] if p["id"] == A3), "Expired")

with section("everyone's phones (manage)"):
    eq("a member may not list everyone's phones", alice.get("/api/admin/intercom/phones").status_code, 403)
    r = owner.get("/api/admin/intercom/phones")
    eq("the owner sees every signed-in phone", sorted(p["id"] for p in r.json()["phones"]), sorted([A1, A3, A4, B1]))
    eq("…with whose it is", {p["user_id"] for p in r.json()["phones"]}, {ALICE, BOB})
    eq("the owner renames anyone's phone",
       owner.post(f"/api/admin/intercom/phones/{B1}/rename", json={"name": "Bass"}).json(), {"ok": True})
    r = owner.post("/api/admin/intercom/phones/999999/rename", json={"name": "x"})
    check("renaming a phone that does not exist is refused", r.status_code >= 400, r.status_code)
    eq("the owner signs one out", owner.delete(f"/api/admin/intercom/phones/{A4}").json(), {"ok": True})
    eq("…twice: not found", owner.delete(f"/api/admin/intercom/phones/{A4}").status_code, 404)
    eq("a member may not sign everyone out", alice.post("/api/admin/intercom/phones/signout-all").status_code, 403)
    eq("sign every phone out", owner.post("/api/admin/intercom/phones/signout-all").json(), {"signed_out": 3})
    eq("…none left", owner.get("/api/admin/intercom/phones").json()["phones"], [])
    eq("…and the devices are revoked in the store", devices.listing(), [])

with section("the live page"):
    eq("a viewer may read the live snapshot", viewer.get("/api/admin/intercom/live").status_code, 200)
    eq("a member may not", alice.get("/api/admin/intercom/live").status_code, 403)

done()
