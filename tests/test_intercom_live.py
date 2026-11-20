"""The Intercom live page (#241): the snapshot, the operator's overrides and the log, through the admin routes.

Offline: a throwaway database and a fake connected phone put straight into the live state; no LiveKit, no sockets.
Run: python tests/test_intercom_live.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.intercom import arbiter as arb, devices, live, rooms, store  # noqa: E402
from app.modules.intercom.presence import Conn, presence  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


users.SCRYPT_N = 2 ** 10
store.forget()
store.ready()
rooms.WATCH_EVERY_S = rooms.RECONCILE_EVERY_S = 1e9
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": True}}})
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
bob = users.create("bob", "Bob", "member", "correct horse", temporary=False)
main = store.create_channel("Main")
stage = store.create_channel("Stage")
store.set_member(main["id"], alice.id, priority=2)
store.set_member(main["id"], bob.id)
store.set_member(stage["id"], bob.id)
_, a_dev = devices.create(alice.id, name="Alice phone")
_, b_dev = devices.create(bob.id, name="Bob phone")
for u, ch in ((alice, main), (bob, main)):
    devices.set_choice(u.id, ch["id"], talk_selected=True, listen_selected=True, volume=100)

arbiter = arb.arbiter
arbiter.reset()
presence.conns.clear()
for u, d in ((alice, a_dev), (bob, b_dev)):
    c = Conn(None, d, SimpleNamespace(id=u.id, name=u.name), 1)
    presence.conns[d["id"]] = c
presence.conns[a_dev["id"]].stats = {"packets_lost": 40, "jitter_ms": 80.0, "rtt_ms": 10, "tracks": 1}
from app.modules.intercom.watchdog import watchdog  # noqa: E402

watchdog.phone_levels[a_dev["id"]] = (3.0, 80.0, "error")   # what the watchdog worked out over its 10 s window
now = time.monotonic()
arbiter.leases[main["id"]] = {alice.id: arb.Lease(alice.id, a_dev["id"], "Alice", 2, now, now, False, 0)}

A = "/api/admin/intercom/live"
viewer = _db.client(app, role="viewer")
operator = _db.client(app, role="operator")
admin = _db.client(app, role="admin")
op_id = users.get_by_username("operator").id


def events(kind):
    return [e for e in store.events(200) if e["kind"] == kind]


print("snapshot")
snap = viewer.get(A).json()
eq("a viewer may read it", viewer.get(A).status_code, 200)
eq("two phones are connected", snap["connected_count"], 2)
chans = {c["name"]: c for c in snap["channels"]}
eq("who is talking, by name", chans["Main"]["talking"], ["Alice"])
eq("listening counts connected listeners", (chans["Main"]["listening"], chans["Stage"]["listening"]), (2, 0))
people = {p["user"]: p for p in snap["connected"]}
eq("each phone shows where it is and what it holds", (people["Alice"]["where"], people["Alice"]["talking"],
                                                       people["Alice"]["listening"]), ("Local", ["Main"], ["Main"]))
eq("priority is the user's best", (people["Alice"]["priority"], people["Bob"]["priority"]), (2, 5))
eq("loss and jitter past the thresholds are marked", (people["Alice"]["loss_level"], people["Alice"]["jitter_level"]),
   ("warn", "bad"))
eq("as the watchdog's numbers", (people["Alice"]["loss_pct"], people["Alice"]["jitter_ms"]), (3.0, 80.0))
eq("a phone with no stats has no level", people["Bob"]["loss_level"], "")
eq("nothing is muted", (snap["muted_all"], chans["Main"]["muted"], people["Alice"]["muted"]), (False, False, False))

print("who may act")
eq("a viewer may not mute all", viewer.post(f"{A}/mute-all", json={}).status_code, 403)
eq("a viewer may not set the label", viewer.put(f"{A}/production", json={"label": "x"}).status_code, 403)
eq("an operator may", operator.put(f"{A}/production", json={"label": "Sunday 9:00"}).json(), {"label": "Sunday 9:00"})
eq("the label is on the snapshot and the pill", (viewer.get(A).json()["label"], live.pills()[0]["text"],
                                                  live.pills()[0]["count"]),
   ("Sunday 9:00", "INTERCOM LIVE · Sunday 9:00", 2))
eq("the module offers the pill to the top bar", modules.pills()[0]["id"], "intercom")
eq("the label change is one logged row with the operator", [e["actor_id"] for e in events("production_label")],
   [op_id])
eq("use_plan with no plan source is refused", operator.put(f"{A}/production", json={"use_plan": True}).status_code, 404)

print("mute channel")
eq("it exists", operator.post(f"{A}/channels/9999/mute", json={}).status_code, 404)
eq("mute", operator.post(f"{A}/channels/{main['id']}/mute", json={}).status_code, 200)
check("the lease on it is gone", not arbiter.leases.get(main["id"]))
check("new presses are refused", main["id"] in arbiter.muted_channels)
eq("it shows muted", {c["name"]: c["muted"] for c in viewer.get(A).json()["channels"]}["Main"], True)
eq("one row, with the channel and the operator", [(e["channel_id"], e["actor_id"]) for e in events("mute_channel")],
   [(main["id"], op_id)])
operator.post(f"{A}/channels/{main['id']}/mute", json={"on": False})
check("unmute lifts it", main["id"] not in arbiter.muted_channels)
eq("and is logged", len(events("unmute_channel")), 1)

print("mute user")
arbiter.leases[main["id"]] = {alice.id: arb.Lease(alice.id, a_dev["id"], "Alice", 2, now, now, False, 0)}
eq("an unknown user is refused", operator.post(f"{A}/users/99999/mute", json={}).status_code, 404)
operator.post(f"{A}/users/{alice.id}/mute", json={})
check("their lease is gone and they are held", not arbiter.leases.get(main["id"]) and alice.id in arbiter.muted_users)
eq("shown on their row", {p["user"]: p["user_muted"] for p in viewer.get(A).json()["connected"]},
   {"Alice": True, "Bob": False})
eq("logged against them", [(e["user_id"], e["actor_id"]) for e in events("mute_user")], [(alice.id, op_id)])
operator.post(f"{A}/users/{alice.id}/mute", json={"on": False})
check("unmute lifts it", alice.id not in arbiter.muted_users)

print("release all")
arbiter.leases[main["id"]] = {alice.id: arb.Lease(alice.id, a_dev["id"], "Alice", 2, now, now, True, 60)}
operator.post(f"{A}/release-all")
check("every lease is gone", not arbiter.leases)
check("but nobody is muted: new presses are allowed", not arbiter.muted_all and not arbiter.muted_users)
eq("one row", len(events("release_all")), 1)

print("mute all")
arbiter.leases[main["id"]] = {alice.id: arb.Lease(alice.id, a_dev["id"], "Alice", 2, now, now, False, 0)}
operator.post(f"{A}/mute-all", json={})
check("every lease is gone and the switch is on", not arbiter.leases and arbiter.muted_all)
snap = viewer.get(A).json()
eq("the page says so on every row", (snap["muted_all"], all(p["muted"] for p in snap["connected"])), (True, True))
import asyncio  # noqa: E402


async def _lookup(user_id, channel_id):
    return {"can_talk": 1, "can_listen": 1, "priority": 5}, {"mode": "party", "max_talkers": 8, "latch_seconds": 0}


arbiter.lookup = _lookup
r = asyncio.run(arbiter.request(alice.id, a_dev["id"], "Alice", [main["id"]]))
eq("a new press is refused until unmuted", r, {main["id"]: "muted"})
operator.post(f"{A}/mute-all", json={"on": False})
check("unmute all lifts it", not arbiter.muted_all)
eq("both are logged", (len(events("mute_all")), len(events("unmute_all"))), (1, 1))
r = asyncio.run(arbiter.request(alice.id, a_dev["id"], "Alice", [main["id"]]))
eq("after unmute a press is granted", r, {main["id"]: "granted"})
arbiter.lookup = None
arbiter.muted_all = True
arbiter.muted_users.add(1)
arbiter.reset()
check("reset clears them", not arbiter.muted_all and not arbiter.muted_users and not arbiter.muted_channels)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
