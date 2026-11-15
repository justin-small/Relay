"""Intercom phones and LiveKit access (#273, #239): device credentials, the phone socket, tokens, live permissions.

Offline: a throwaway database, the app's own lifespan, and a fake LiveKit room service on a spare port that records
what Relay asks of it. Run: python tests/test_intercom_phones.py
"""
import base64
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
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
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.intercom import devices, livekit, rooms, store, webhook  # noqa: E402
from app.modules.intercom.arbiter import arbiter  # noqa: E402
from app.modules.intercom import presence as presence_mod  # noqa: E402
from app.modules.intercom.presence import presence  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails, wait_for  # noqa: E402


def raises(label, exc, fn):
    try:
        fn()
    except exc:
        return check(label, True)
    except Exception as e:  # noqa: BLE001
        return eq(label, repr(e), exc.__name__)
    check(label, False)


# ------------------------------------------------------------------ a fake LiveKit room service
CALLS: list[tuple[str, dict]] = []
PARTICIPANTS: dict[str, list[str]] = {}
PERMS: dict[tuple[str, str], dict] = {}          # what Relay last set for a participant
TRACKS: dict[tuple[str, str], list[dict]] = {}   # a participant's published tracks


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def address_string(self):
        return "127.0.0.1"          # no reverse lookup, which stalls on some runners

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        CALLS.append((self.path.rsplit("/", 1)[-1], body, self.headers.get("Authorization", "")))
        out = {}
        room = body.get("room")
        if self.path.endswith("ListParticipants"):
            out = {"participants": [{"identity": i, "permission": PERMS.get((room, i), {}),
                                     "tracks": TRACKS.get((room, i), [])} for i in PARTICIPANTS.get(room, [])]}
        elif self.path.endswith("UpdateParticipant"):
            PERMS[(room, body["identity"])] = body["permission"]
        elif self.path.endswith("MutePublishedTrack"):
            for t in TRACKS.get((room, body["identity"]), []):
                if t["sid"] == body["trackSid"]:
                    t["muted"] = body["muted"]
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def spare():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
livekit.SIGNAL_PORT = fake.server_address[1]
threading.Thread(target=fake.serve_forever, daemon=True).start()


def methods():
    return [c[0] for c in CALLS]


def settle():
    """Let the live side finish what earlier changes started."""
    time.sleep(0.4)


def b64d(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


# ------------------------------------------------------------------ setup
users.SCRYPT_N = 2 ** 10
store.forget()
store.ready()
rooms.WATCH_EVERY_S = rooms.RECONCILE_EVERY_S = 1e9        # the background loop is tested on its own below
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": True}}})
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
bob = users.create("bob", "Bob", "member", "correct horse", temporary=False)
general = store.create_channel("General")
stage = store.create_channel("Stage")
store.set_member(general["id"], alice.id, priority=2)
store.set_member(stage["id"], bob.id)

# ------------------------------------------------------------------ device credentials
print("device credentials")
cred, dev = devices.create(alice.id, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)")
eq("named from the user agent", dev["name"], "iPhone")
check("the credential is 43 URL-safe characters (32 random bytes)", len(cred) == 43)
check("only a hash is stored", cred not in json.dumps(db.one("select * from intercom_devices")))
got = devices.authenticate(cred)
check("it signs in", got is not None and got[0]["id"] == dev["id"] and got[1].id == alice.id)
eq("no rotation yet", got[2], None)
check("a wrong credential fails", devices.authenticate("x" * 43) is None)
check("a short or non-text credential fails", devices.authenticate("abc") is None and devices.authenticate(None) is None)
eq("the organisation is always 1", dev["org_id"], 1)

# Rotation after 30 days of use, and a reply that never arrived.
db.execute("update intercom_devices set rotates_at = ? where id = ?", (db.later(-5), dev["id"]))
dev2, _user, fresh = devices.authenticate(cred)
check("an overdue credential is replaced on connect", fresh and fresh != cred)
check("the new one works", devices.authenticate(fresh) is not None)
lost = devices.authenticate(cred)
check("the old one still works for 10 minutes (the reply was lost)", lost is not None)
check("and is replaced again so the phone ends up with a live one", lost[2] and lost[2] != fresh)
db.execute("update intercom_devices set previous_valid_until = ? where id = ?", (db.later(-1), dev["id"]))
check("after 10 minutes the old one is dead", devices.authenticate(cred) is None)

# Expiry, revoking and turned-off accounts.
db.execute("update intercom_devices set last_seen_at = ? where id = ?", (db.later(-31 * 86400), dev["id"]))
current = db.one("select credential_hash from intercom_devices")["credential_hash"]
check("a phone unused for 30 days has expired", devices.authenticate(lost[2]) is None)
check("and is signed out", db.one("select revoked_at from intercom_devices")["revoked_at"] is not None)
cred2, d2 = devices.create(alice.id)
users.update(alice.id, disabled=True, actor=None)
check("a turned-off account cannot connect", devices.authenticate(cred2) is None)
users.update(alice.id, disabled=False, actor=None)
cred3, d3 = devices.create(alice.id)
check("revoking signs a phone out", devices.revoke(d3["id"]) is not None and devices.authenticate(cred3) is None)
check("revoking twice finds nothing", devices.revoke(d3["id"]) is None)
check("a user cannot revoke another's phone", devices.revoke(d2["id"], user_id=bob.id) is None)
raises("a blank name is refused", devices.Refused, lambda: devices.rename(d2["id"], " "))
devices.rename(d2["id"], "  Alice's   phone ")
eq("renamed and tidied", devices.listing(alice.id)[0]["name"], "Alice's phone")
kinds = {e["kind"] for e in store.events(200)}
check("signing in, rotating, expiring and revoking are all logged",
      {"device_signed_in", "device_rotated", "device_expired", "device_revoked"} <= kinds)
check("choices need membership", True)
raises("a choice on a channel you are not on is refused", devices.Refused,
       lambda: devices.set_choice(alice.id, stage["id"], talk_selected=1, listen_selected=1, volume=50))
devices.set_choice(alice.id, general["id"], talk_selected=True, listen_selected=True, volume=40)
eq("a choice is kept", [(c["id"], c["listen_selected"], c["volume"]) for c in devices.phone_config(alice.id)],
   [(general["id"], 1, 40)])
raises("volume must be 0 to 100", devices.Refused,
       lambda: devices.set_choice(alice.id, general["id"], talk_selected=1, listen_selected=1, volume=101))

# ------------------------------------------------------------------ tokens
print("tokens")
key, secret = livekit.keys()
tok = rooms.mint_token(key, secret, channel_id=7, user_id=3, device_id=9, name="Alice", can_subscribe=True, now=1000)
head, pay, sig = tok.split(".")
claims = json.loads(b64d(pay))
eq("one room, publishing off, microphone only",
   (claims["video"]["room"], claims["video"]["canPublish"], claims["video"]["canPublishSources"],
    claims["video"]["canPublishData"], claims["video"]["roomJoin"]), ("ch-7", False, ["microphone"], False, True))
eq("identity is user and device", (claims["sub"], claims["iss"]), ("u3-d9", key))
eq("five minutes to live", claims["exp"] - 1000, 300 + 0)
import hashlib  # noqa: E402
import hmac  # noqa: E402

check("signed with the secret", hmac.new(secret.encode(), f"{head}.{pay}".encode(), hashlib.sha256).digest()
      == b64d(sig))
check("the secret is not in the token", secret not in tok and secret not in pay)
check("listening off means no subscribing", json.loads(b64d(rooms.mint_token(
    key, secret, channel_id=1, user_id=1, device_id=1, name="x", can_subscribe=False).split(".")[1]))
    ["video"]["canSubscribe"] is False)

# ------------------------------------------------------------------ the socket
print("socket")
admin = _db.client(app, role="admin")
with TestClient(app, base_url="http://relay.local:8001") as member:
    member.post("/admin/login", data={"username": "alice", "password": "correct horse"}, follow_redirects=False)
    check("the module is running", presence.loop is not None)
    r = member.post("/intercom/api/phones", json={"name": "Pixel"}, headers={"User-Agent": "Android"})
    eq("a signed-in member makes a device", r.status_code, 200)
    cred = r.json()["credential"]
    pid = r.json()["phone"]["id"]
    eq("the phone comes back named", r.json()["phone"]["name"], "Pixel")
    eq("signed out, no device", TestClient(app, base_url="http://relay.local:8001").post("/intercom/api/phones").status_code,
       401)

    def hello(ws, credential):
        ws.send_text(json.dumps({"type": "hello", "credential": credential}))

    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, "x" * 43)
        try:
            ws.receive_text()
            check("a bad credential is refused", False)
        except WebSocketDisconnect as e:
            eq("a bad credential is refused", e.code, 4401)

    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred)
        w = json.loads(ws.receive_text())
        eq("welcome", (w["type"], w["org_id"], w["user"]["name"], w["device"]["id"]), ("welcome", 1, "Alice", pid))
        eq("with exactly the user's channels", [c["name"] for c in w["channels"]], ["General"])
        check("and no credential or key", "credential" not in w and secret not in json.dumps(w))
        check("the phone is on", presence.online(pid))
        CALLS.clear()
        ws.send_text(json.dumps({"type": "token", "channel": general["id"]}))
        t = json.loads(ws.receive_text())
        eq("a token for a channel the user is on", (t["type"], t["room"]), ("token", f"ch-{general['id']}"))
        check("the room was made first", methods()[:1] == ["CreateRoom"])
        ws.send_text(json.dumps({"type": "token", "channel": stage["id"]}))
        t = json.loads(ws.receive_text())
        eq("a channel the user is not on is refused", (t["type"], t["for"]), ("error", "token"))
        ws.send_text(json.dumps({"type": "token", "channel": "1 or 1"}))
        eq("a made-up channel is refused", json.loads(ws.receive_text())["type"], "error")
        ws.send_text(json.dumps({"type": "choice", "channel": general["id"], "talk_selected": 1,
                                 "listen_selected": 1, "volume": 70}))
        ws.send_text(json.dumps({"type": "ping"}))
        eq("the socket answers", json.loads(ws.receive_text())["type"], "pong")
        eq("a choice was stored", db.one("select volume from intercom_user_choices where user_id = ?",
                                         (alice.id,))["volume"], 70)
        # rate limit
        for _ in range(27):         # three were asked for above: 30 in all
            ws.send_text(json.dumps({"type": "token", "channel": general["id"]}))
            ws.receive_text()
        ws.send_text(json.dumps({"type": "token", "channel": general["id"]}))
        eq("more than 30 tokens a minute is refused", json.loads(ws.receive_text())["type"], "error")
        try:
            ws.receive_text()
            check("and the socket closes", False)
        except WebSocketDisconnect as e:
            eq("and the socket closes", e.code, 4429)
    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred)
        json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "token", "channel": general["id"]}))
        eq("a new socket does not reset the count: the limit is per phone (#278)",
           json.loads(ws.receive_text())["type"], "error")
    presence_mod._token_times.clear(), presence_mod._token_logged.clear()

    check("a closed socket leaves the live set", not presence.online(pid))
    check("the session was recorded", db.one("select ended_at from intercom_sessions where device_id = ?",
                                              (pid,))["ended_at"] is not None)

    # Rotation delivers a new credential in the welcome.
    db.execute("update intercom_devices set rotates_at = ? where id = ?", (db.later(-1), pid))
    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred)
        w = json.loads(ws.receive_text())
        check("an overdue credential is replaced in the welcome", w.get("credential") and w["credential"] != cred)
        cred = w["credential"]

    # ---- signing out closes the socket and removes the phone from LiveKit
    print("sign out")
    PARTICIPANTS.clear()
    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred)
        ws.receive_text()
        CALLS.clear()
        r = admin.delete(f"/api/admin/intercom/phones/{pid}")
        eq("an admin signs the phone out", r.status_code, 200)
        try:
            ws.receive_text()
            check("its socket closes", False)
        except WebSocketDisconnect as e:
            eq("its socket closes", e.code, 4403)
    removed = [c[1] for c in CALLS if c[0] == "RemoveParticipant"]
    check("it is removed from LiveKit", {"room": f"ch-{general['id']}", "identity": f"u{alice.id}-d{pid}"} in removed)
    check("the room service was called with an admin token", all(c[2].startswith("Bearer ") for c in CALLS))
    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred)
        try:
            ws.receive_text()
            check("a revoked credential is refused", False)
        except WebSocketDisconnect as e:
            eq("a revoked credential is refused", e.code, 4401)

    # ---- a password change signs every phone of the user out
    cred_a, da = devices.create(alice.id)
    with member.websocket_connect("/intercom/ws") as ws:
        hello(ws, cred_a)
        ws.receive_text()
        users.set_password(alice.id, "another horse", temporary=False)
        try:
            ws.receive_text()
            check("a password change closes the phone", False)
        except WebSocketDisconnect as e:
            eq("a password change closes the phone", e.code, 4403)
    check("and signs it out", devices.authenticate(cred_a) is None)

    # ---- membership removal reaches the running room
    print("membership")
    cred_b, db_ = devices.create(bob.id)
    store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
    settle()
    PARTICIPANTS[f"ch-{general['id']}"] = [f"u{bob.id}-d{db_['id']}", f"u{alice.id}-d1"]
    CALLS.clear()
    store.remove_member(general["id"], bob.id)
    wait_for(lambda: "RemoveParticipant" in methods(), 1)
    removed = [c[1] for c in CALLS if c[0] == "RemoveParticipant"]
    eq("removing a member removes only their phones, within a second",
       {(c["room"], c["identity"]) for c in removed}, {(f"ch-{general['id']}", f"u{bob.id}-d{db_['id']}")})
    store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
    settle()
    CALLS.clear()
    store.set_member(general["id"], bob.id, can_talk=True, can_listen=False)
    wait_for(lambda: "UpdateParticipant" in methods(), 1)
    upd = [c[1] for c in CALLS if c[0] == "UpdateParticipant"]
    check("losing Listen turns subscribing off", upd and upd[0]["permission"]["canSubscribe"] is False)
    CALLS.clear()
    store.delete_channel(stage["id"])
    wait_for(lambda: "DeleteRoom" in methods(), 1)
    check("deleting a channel closes its room", "DeleteRoom" in methods())

    # ------------------------------------------------------------------ talk leases over the socket (#274)
    print("talk leases over the socket")

    arbiter.reset()
    store.set_member(general["id"], alice.id, can_talk=True, can_listen=True)
    settle()
    cred2, row2 = devices.create(alice.id)
    with member.websocket_connect("/intercom/ws") as ws:
        ws.send_text(json.dumps({"type": "hello", "credential": cred2}))
        json.loads(ws.receive_text())
        CALLS.clear()
        ws.send_text(json.dumps({"type": "talk.request", "channels": [general["id"], stage["id"]], "seq": 4}))
        got = {}
        for _ in range(3):                      # a grant, a refusal and the activity broadcast, in any order
            m = json.loads(ws.receive_text())
            got[m["type"] + str(m.get("channel"))] = m
        eq("the channel Alice is on is granted", got[f"talk.granted{general['id']}"]["seq"], 4)
        eq("one she is not on is refused", got[f"talk.denied{stage['id']}"]["reason"], "not_allowed")
        eq("listeners are told who talks", [t["name"] for t in got[f"activity{general['id']}"]["talkers"]], ["Alice"])
        check("the lease is held", arbiter.holds(general["id"], alice.id))
        ws.send_text(json.dumps({"type": "talk.beat"}))
        ws.send_text(json.dumps({"type": "talk.release", "channels": [general["id"]]}))
        m = json.loads(ws.receive_text())
        eq("releasing tells listeners it is quiet", (m["type"], m["talkers"]), ("activity", []))
        ws.send_text(json.dumps({"type": "talk.request", "channels": [general["id"]]}))
        json.loads(ws.receive_text()), json.loads(ws.receive_text())
        check("held again", arbiter.holds(general["id"], alice.id))
    wait_for(lambda: not arbiter.holds(general["id"], alice.id), 1)
    check("closing the socket releases the lease", not arbiter.holds(general["id"], alice.id))


# ------------------------------------------------------------------ live permissions
print("armed phones and talk leases")
import asyncio  # noqa: E402

rooms.armed.clear()
rooms.talking.clear()
room = f"ch-{general['id']}"
bob_dev = 5
ident = f"u{bob.id}-d{bob_dev}"
key_ = (general["id"], bob_dev)
store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
settle()
PARTICIPANTS[room] = [ident]
PERMS.clear()
TRACKS[(room, ident)] = [{"sid": "TR_1", "type": "AUDIO", "muted": False}]


def publish_flag():
    return [c[1]["permission"]["canPublish"] for c in CALLS if c[0] == "UpdateParticipant"]


CALLS.clear()
asyncio.run(rooms.apply(bob.id, general["id"]))
eq("without Talk selected a phone may not publish", (publish_flag(), key_ in rooms.armed), ([False], False))
devices.set_choice(bob.id, general["id"], talk_selected=True, listen_selected=True, volume=100)
CALLS.clear()
asyncio.run(rooms.apply(bob.id, general["id"]))
eq("with Talk selected it is armed: it may publish", (publish_flag(), key_ in rooms.armed), ([True], True))
CALLS.clear()
asyncio.run(rooms.apply(bob.id, general["id"]))
eq("applying again changes nothing", methods().count("UpdateParticipant"), 0)

CALLS.clear()
asyncio.run(rooms.enforce(general["id"]))
mutes = [(c[1]["trackSid"], c[1]["muted"]) for c in CALLS if c[0] == "MutePublishedTrack"]
eq("an armed phone with an open track and no lease is muted", mutes, [("TR_1", True)])
CALLS.clear()
asyncio.run(rooms.enforce(general["id"]))
eq("and a muted one is left alone", methods().count("MutePublishedTrack"), 0)

CALLS.clear()
asyncio.run(rooms.set_talking(general["id"], bob.id, bob_dev, True))
eq("a lease unmutes the track", [(c[1]["trackSid"], c[1]["muted"]) for c in CALLS if c[0] == "MutePublishedTrack"],
   [("TR_1", False)])
check("and the phone is marked as talking", key_ in rooms.talking)
CALLS.clear()
asyncio.run(rooms.enforce(general["id"]))
eq("a phone holding a lease is never muted by the watch", methods().count("MutePublishedTrack"), 0)
CALLS.clear()
asyncio.run(rooms.set_talking(general["id"], bob.id, bob_dev, False))
eq("the end of a lease mutes it again", [(c[1]["trackSid"], c[1]["muted"]) for c in CALLS
                                         if c[0] == "MutePublishedTrack"], [("TR_1", True)])
check("and it is not talking", key_ not in rooms.talking)

# A phone that unmutes itself is caught by the watch.
TRACKS[(room, ident)][0]["muted"] = False
CALLS.clear()
asyncio.run(rooms.enforce(general["id"]))
check("a phone that unmutes itself without a lease is muted again", methods().count("MutePublishedTrack") == 1)

# Starting a lease for a phone that is not armed arms it first (the slow path).
rooms.armed.clear()
rooms.talking.clear()
PERMS.clear()
TRACKS[(room, ident)][0]["muted"] = True
CALLS.clear()
asyncio.run(rooms.set_talking(general["id"], bob.id, bob_dev, True))
eq("a lease on an unarmed phone grants publishing", publish_flag(), [True])

# Rights are enforced: no Talk right means no publishing and no unmuting, whatever was asked.
store.set_member(general["id"], bob.id, can_talk=False, can_listen=True)
settle()
rooms.armed.clear()
rooms.talking.clear()
PERMS.clear()
CALLS.clear()
asyncio.run(rooms.set_talking(general["id"], bob.id, bob_dev, True))
unmutes = [c for c in CALLS if c[0] == "MutePublishedTrack" and c[1]["muted"] is False]
eq("a member who cannot talk gets no lease and nothing is unmuted", (len(unmutes), key_ in rooms.talking), (0, False))
CALLS.clear()
asyncio.run(rooms.apply(bob.id, general["id"]))
eq("and may not publish", publish_flag(), [False])
CALLS.clear()
asyncio.run(rooms.set_talking(general["id"], 999, bob_dev, True))
eq("a stranger gets no lease", [c for c in CALLS if c[0] == "MutePublishedTrack" and c[1]["muted"] is False], [])

print("events and the watch loop")
store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
settle()
rooms.armed.clear()
rooms.talking.clear()
PERMS.clear()
CALLS.clear()
asyncio.run(rooms.event({"kind": "participant_joined", "channel": general["id"], "user": bob.id, "device": bob_dev}))
eq("joining a room applies the phone's rights", (publish_flag(), key_ in rooms.armed), ([True], True))
TRACKS[(room, ident)][0]["muted"] = False
CALLS.clear()
asyncio.run(rooms.event({"kind": "track_published", "channel": general["id"], "user": bob.id, "device": bob_dev}))
eq("a track published without a lease is muted at once", methods().count("MutePublishedTrack"), 1)
asyncio.run(rooms.event({"kind": "participant_left", "channel": general["id"], "user": bob.id, "device": bob_dev}))
check("leaving forgets the phone", key_ not in rooms.armed)

# The loop itself: with a short beat it mutes an open track and, on its reconcile, arms a phone nobody asked about.
rooms.WATCH_EVERY_S, rooms.RECONCILE_EVERY_S = 0.05, 0.2
rooms.armed.clear()
rooms.talking.clear()
PERMS.clear()
TRACKS[(room, ident)][0]["muted"] = False


async def watch_a_little():
    task = asyncio.create_task(rooms.watch())
    await asyncio.sleep(1.0)
    task.cancel()


CALLS.clear()
asyncio.run(watch_a_little())
check("the reconcile arms a phone that is in a room", key_ in rooms.armed)
check("and the watch mutes its open track", TRACKS[(room, ident)][0]["muted"] is True)
rooms.WATCH_EVERY_S = rooms.RECONCILE_EVERY_S = 1e9

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
