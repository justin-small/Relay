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
from app.modules.intercom.presence import presence  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


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


class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def address_string(self):
        return "127.0.0.1"          # no reverse lookup, which stalls on some runners

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        CALLS.append((self.path.rsplit("/", 1)[-1], body, self.headers.get("Authorization", "")))
        out = {}
        if self.path.endswith("ListParticipants"):
            out = {"participants": [{"identity": i} for i in PARTICIPANTS.get(body.get("room"), [])]}
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
    end = time.time() + 1
    while "RemoveParticipant" not in methods() and time.time() < end:
        time.sleep(0.02)
    removed = [c[1] for c in CALLS if c[0] == "RemoveParticipant"]
    eq("removing a member removes only their phones, within a second",
       {(c["room"], c["identity"]) for c in removed}, {(f"ch-{general['id']}", f"u{bob.id}-d{db_['id']}")})
    store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
    settle()
    CALLS.clear()
    store.set_member(general["id"], bob.id, can_talk=True, can_listen=False)
    end = time.time() + 1
    while "UpdateParticipant" not in methods() and time.time() < end:
        time.sleep(0.02)
    upd = [c[1] for c in CALLS if c[0] == "UpdateParticipant"]
    check("losing Listen turns subscribing off", upd and upd[0]["permission"]["canSubscribe"] is False)
    CALLS.clear()
    store.delete_channel(stage["id"])
    end = time.time() + 1
    while "DeleteRoom" not in methods() and time.time() < end:
        time.sleep(0.02)
    check("deleting a channel closes its room", "DeleteRoom" in methods())

# ------------------------------------------------------------------ live permissions
print("live permissions")
import asyncio  # noqa: E402

CALLS.clear()
store.set_member(general["id"], bob.id, can_talk=True, can_listen=True)
asyncio.run(rooms.set_publish(general["id"], bob.id, 5, True))
asyncio.run(rooms.set_publish(general["id"], bob.id, 5, False))
perms = [c[1]["permission"]["canPublish"] for c in CALLS if c[0] == "UpdateParticipant"]
eq("a lease switches publishing on, its end off", perms, [True, False])
store.set_member(general["id"], bob.id, can_talk=False, can_listen=True)
CALLS.clear()
asyncio.run(rooms.set_publish(general["id"], bob.id, 5, True))
eq("a member who cannot talk is never given publishing", [c[1]["permission"]["canPublish"] for c in CALLS], [False])
CALLS.clear()
asyncio.run(rooms.set_publish(general["id"], 999, 5, True))
eq("a stranger is never given publishing", [c[1]["permission"]["canPublish"] for c in CALLS], [False])

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
