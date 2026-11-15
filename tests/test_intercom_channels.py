"""Intercom channels, members and the production label (#275), through the admin routes and the phone socket.

Offline: a throwaway database and the app's own lifespan; LiveKit is not running (the live side copes).
Run: python tests/test_intercom_channels.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.intercom import devices, rooms, store  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


users.SCRYPT_N = 2 ** 10
store.forget()
store.ready()
rooms.WATCH_EVERY_S = rooms.RECONCILE_EVERY_S = 1e9
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": True}}})
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
bob = users.create("bob", "Bob", "member", "correct horse", temporary=False)
carol = users.create("carol", "Carol", "viewer", "correct horse", temporary=False)
users.update(carol.id, disabled=True, actor=None)

A = "/api/admin/intercom"
admin = _db.client(app, role="admin")

print("channels")
r = admin.post(f"{A}/channels", json={"name": "Main"})
main = r.json()["channel"]
eq("a new channel defaults to Party line, 8 talkers, 60 s latch",
   (r.status_code, main["mode"], main["max_talkers"], main["latch_seconds"]), (200, "party", 8, 60))
r = admin.post(f"{A}/channels", json={"name": "Pastor IFB", "mode": "queue"})
ifb = r.json()["channel"]
eq("One at a time defaults to one talker", ifb["max_talkers"], 1)
cams = admin.post(f"{A}/channels", json={"name": "Cameras", "mode": "preempt", "max_talkers": 3}).json()["channel"]
eq("a duplicate name is refused", admin.post(f"{A}/channels", json={"name": "Main"}).status_code, 409)
eq("a blank name is refused", admin.post(f"{A}/channels", json={"name": " "}).status_code, 400)
eq("an unknown mode is refused", admin.post(f"{A}/channels", json={"name": "X", "mode": "loud"}).status_code, 400)
eq("the table lists them in order", [c["name"] for c in admin.get(f"{A}/channels").json()["channels"]],
   ["Main", "Pastor IFB", "Cameras"])

print("people")
eq("only active accounts that may use the intercom are offered",
   sorted(p["username"] for p in admin.get(f"{A}/people").json()["people"] if p["username"] in ("alice", "bob", "carol")),
   ["alice", "bob"])
eq("search narrows them", [p["username"] for p in admin.get(f"{A}/people?q=ali").json()["people"]], ["alice"])

print("members")
ch = admin.get(f"{A}/channels/{main['id']}").json()["channel"]
body = {"version": ch["version"], "name": "Main", "colour": "#e55", "mode": "preempt", "max_talkers": 2,
        "latch_seconds": 30, "members": [{"user_id": alice.id, "can_talk": True, "can_listen": True, "priority": 2},
                                          {"user_id": bob.id, "can_talk": False, "can_listen": True, "priority": 5}]}
r = admin.put(f"{A}/channels/{main['id']}", json=body)
saved = r.json()["channel"]
eq("saving settings and members in one go", (r.status_code, saved["mode"], saved["max_talkers"], saved["colour"],
                                              [(m["name"], m["can_talk"], m["priority"]) for m in saved["members"]]),
   (200, "preempt", 2, "#e55", [("Alice", 1, 2), ("Bob", 0, 5)]))
eq("the version moves on", saved["version"], ch["version"] + 1)
eq("one permission_change per member", len(db.query(
    "select id from intercom_events where kind = 'permission_change' and channel_id = ?", (main["id"],))), 2)
r = admin.put(f"{A}/channels/{main['id']}", json=body)
eq("a second admin saving from the old copy is refused", (r.status_code, "someone else" in r.json()["error"]),
   (409, True))
eq("and nothing was written", admin.get(f"{A}/channels/{main['id']}").json()["channel"]["version"], saved["version"])
body2 = {**body, "version": saved["version"], "members": [body["members"][0]]}
admin.put(f"{A}/channels/{main['id']}", json=body2)
eq("a member left out of the list is removed", [m["name"] for m in
                                                admin.get(f"{A}/channels/{main['id']}").json()["channel"]["members"]],
   ["Alice"])
eq("and logged", len(db.query("select id from intercom_events where kind = 'permission_change' and channel_id = ?"
                              " and detail like '%removed%'", (main["id"],))), 1)
now = admin.get(f"{A}/channels/{main['id']}").json()["channel"]
for label, members in (("a member with no rights is refused", [{"user_id": alice.id, "can_talk": False,
                                                                "can_listen": False, "priority": 5}]),
                       ("priority out of range is refused", [{"user_id": alice.id, "can_talk": True,
                                                              "can_listen": True, "priority": 10}]),
                       ("a made-up user is refused", [{"user_id": 9999, "can_talk": True, "can_listen": True,
                                                       "priority": 5}])):
    r = admin.put(f"{A}/channels/{main['id']}", json={**body, "version": now["version"], "members": members})
    check(label, r.status_code in (400, 409))
eq("a channel that is not there", admin.put(f"{A}/channels/999", json=body).status_code, 404)

print("order")
ids = [c["id"] for c in admin.get(f"{A}/channels").json()["channels"]]
eq("reordering", admin.post(f"{A}/channels/reorder", json={"ids": list(reversed(ids))}).status_code, 200)
eq("sets the order", [c["id"] for c in admin.get(f"{A}/channels").json()["channels"]], list(reversed(ids)))
eq("a stale list is refused", admin.post(f"{A}/channels/reorder", json={"ids": ids[:2]}).status_code, 409)
eq("renaming never changes rights", [m["user_id"] for m in store.channel_view(main["id"])["members"]], [alice.id])
admin.put(f"{A}/channels/{main['id']}", json={**body2, "version": store.channel_view(main["id"])["version"],
                                              "name": "Main hall", "members": body2["members"]})
eq("a rename keeps the member", (store.channel_view(main["id"])["name"], len(store.channel_view(main["id"])["members"])),
   ("Main hall", 1))

print("phones hear about it")
cred, dev = devices.create(alice.id)
with TestClient(app, base_url="http://relay.local:8001") as _c:     # the app's lifespan runs inside
    with _c.websocket_connect("/intercom/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "credential": cred}))
        w = json.loads(ws.receive_text())
        eq("the welcome lists Alice's channels in order", [c["name"] for c in w["channels"]], ["Main hall"])
        t0 = time.time()
        cur = store.channel_view(cams["id"])
        store.update_channel(cams["id"], cur["version"], name="Cameras", colour="", mode="preempt", max_talkers=3,
                             latch_seconds=60, members=[{"user_id": alice.id, "can_talk": True, "can_listen": True,
                                                         "priority": 5}])
        m = json.loads(ws.receive_text())
        eq("adding a member pushes the new configuration", (m["type"], sorted(c["name"] for c in m["channels"])),
           ("config", ["Cameras", "Main hall"]))
        check("within a second, with no reconnect", time.time() - t0 < 1.0)
        store.set_production("Sunday 9:00", actor_id=None)
        m = json.loads(ws.receive_text())
        eq("a new label reaches phones", (m["type"], m["production"]), ("config", "Sunday 9:00"))
        store.remove_member(cams["id"], alice.id)
        m = json.loads(ws.receive_text())
        eq("a removed member loses the channel", [c["name"] for c in m["channels"]], ["Main hall"])

print("delete")
r = admin.delete(f"{A}/channels/{cams['id']}")
eq("a channel is deleted", (r.status_code, admin.get(f"{A}/channels/{cams['id']}").status_code), (200, 404))
eq("deleting what is not there", admin.delete(f"{A}/channels/{cams['id']}").status_code, 404)

print("production label")
eq("the label survives a restart", (admin.put(f"{A}/production", json={"label": "  Easter   service "}).json()["label"],
                                    store.production()), ("Easter service", "Easter service"))
r = admin.get(f"{A}/production").json()
eq("with no plan source switched on there is no plan button", (r["label"], r["plan_available"]), ("Easter service", False))
eq("and the plan route is absent", admin.get(f"{A}/production/plan").status_code, 404)
config.save({"modules": {"planning_center": {"enabled": True}}})
check("with Planning Center on the button is offered", admin.get(f"{A}/production").json()["plan_available"])
eq("it reads today's plan name only (none today)", admin.get(f"{A}/production/plan").json(), {"label": ""})
eq("the registry answers for the module", modules.plan_label(), "")
config.save({"modules": {"planning_center": {"enabled": False}}})
eq("off again, it is gone", modules.plan_label(), None)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
