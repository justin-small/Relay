"""Intercom remote (#277): who may connect from outside, the remote socket, the bridge's state machine and the
fixed list of routes the tunnel carries.

Offline: a throwaway database, the app's own lifespan, a fake Cloudflare (relay-cloud) and a fake audio side. The
audio bridge's helpers (media.py: mixing, down-mixing, the fallback when the libraries are missing) are checked here;
its livekit and aiortc calls need real sessions and are not. Run: python tests/test_intercom_remote.py
"""
import asyncio
import json
import os
import sys
import tempfile
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
from app.modules.intercom import store as istore  # noqa: E402
from app.modules.intercom.presence import presence  # noqa: E402
from app.modules.intercom_remote import bridge as bridge_mod, cloud, remote, routes, store  # noqa: E402
from app.modules.intercom_remote.bridge import Bridge, backoff, bridge, mic_name, track_name  # noqa: E402

from _harness import check, eq, section, wait_for  # noqa: E402


class FakeMedia:
    def __init__(self):
        self.calls = []
        self.up = True
        self.missing = ""

    def unavailable(self):
        return self.missing

    async def open(self):
        self.calls.append(("open",))

    async def close(self):
        self.calls.append(("close",))

    def alive(self):
        return self.up

    async def listen(self, channel_id, cf, name):
        self.calls.append(("listen", channel_id, cf, name))

    async def unlisten(self, channel_id):
        self.calls.append(("unlisten", channel_id))

    async def talk(self, channel_id, user_id, device_id, cf, name):
        self.calls.append(("talk", channel_id, user_id, device_id, cf, name))

    async def untalk(self, channel_id, device_id):
        self.calls.append(("untalk", channel_id, device_id))


CLOUD: list[tuple] = []
OPENED = iter(f"sess{n:08d}" for n in range(1, 1000))
cloud.open_session = lambda: (CLOUD.append(("open",)), next(OPENED))[1]
cloud.tracks_new = lambda sid, body: (CLOUD.append(("tracks_new", sid, body)),
                                      {"sessionDescription": {"type": "answer", "sdp": "v=0"}})[1]
cloud.renegotiate = lambda sid, body: (CLOUD.append(("renegotiate", sid, body)), {})[1]
cloud.close_session = lambda sid: CLOUD.append(("close", sid))
cloud.reachable = lambda: True
TURN = {"iceServers": [{"urls": ["stun:stun.cloudflare.com:3478"]},
                       {"urls": ["turns:turn.cloudflare.com:443?transport=tcp"], "username": "u", "credential": "c"}]}
TURN_CALLS: list[str] = []


def _fake_call(method, path, body=None):
    TURN_CALLS.append(path)
    if isinstance(TURN, Exception):
        raise TURN
    return TURN


cloud._call = _fake_call

users.SCRYPT_N = 2 ** 10
istore.forget()
store.forget()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {
    "intercom": {"enabled": True}, "remote_access": {"enabled": True}, "intercom_remote": {"enabled": True}}})
istore.ready()
store.ready()
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
bob = users.create("bob", "Bob", "member", "correct horse", temporary=False)
owner = users.create("owner", "Owner", "owner", "correct horse", temporary=False)
general = istore.create_channel("General")
stage = istore.create_channel("Stage")
istore.set_member(general["id"], alice.id, priority=2)
istore.set_member(stage["id"], alice.id)
istore.set_member(general["id"], bob.id)
for ch in (general, stage):
    db.execute("insert into intercom_user_choices (user_id, channel_id, talk_selected, listen_selected, volume)"
               " values (?, ?, 1, 1, 100)", (alice.id, ch["id"]))


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ the bridge's state machine
with section("TURN for phones on networks that block direct UDP"):
    eq("Relay's cloud is asked for TURN credentials", (cloud.ice_servers(), TURN_CALLS), (TURN["iceServers"], ["/v1/realtime/turn"]))
    cloud.ice_servers()
    eq("they are reused by the install's phones, not fetched for each", len(TURN_CALLS), 1)
    cloud._turn["until"] = 0
    TURN = cloud.CloudError("rate_limited", 429)
    eq("without them a phone still gets Cloudflare's STUN", cloud.ice_servers(), cloud.STUN_ONLY)
    cloud.ice_servers()
    eq("and a failure is not retried for every phone", len(TURN_CALLS), 2)
    TURN = {"iceServers": [{"urls": ["stun:stun.cloudflare.com:3478"]},
                           {"urls": ["turns:turn.cloudflare.com:443?transport=tcp"], "username": "u", "credential": "c"}]}
    cloud._turn["until"] = 0
    check("once the cloud answers again, TURN is back", len(cloud.ice_servers()) == 2)

with section("backoff"):
    eq("1, 2, 4, 8, 16, 30, then every 30 s", [backoff(n) for n in range(9)], [1, 2, 4, 8, 16, 30, 30, 30, 30])
    eq("a channel's track name", track_name(7), "ch7")
    eq("a phone's microphone track name", mic_name(12), "mic12")

with section("the bridge connects, notices the link go and comes back"):
    b = Bridge()
    media = FakeMedia()
    sleeps, states, lost = [], [], []
    failures = [cloud.CloudError("network"), cloud.CloudError("network")]
    real_open = cloud.open_session

    def flaky():
        if failures:
            raise failures.pop(0)
        return real_open()

    cloud.open_session = flaky
    health = iter([False, False])
    cloud.reachable = lambda: next(health, False)

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 6:
            raise asyncio.CancelledError

    async def on_lost():
        lost.append(b.state)

    async def go():
        b.sleep, b.on_lost, b.on_state = fake_sleep, on_lost, states.append
        b.begin(media)
        try:
            await b._task
        except asyncio.CancelledError:
            pass

    run(go())
    eq("waits 1 s then 2 s after two failures, then watches every 10 s", sleeps[:4], [1, 2, 10, 10])
    eq("after the link is lost it starts again from 1 s", sleeps[4], 1)
    check("it went connecting, reconnecting, connected, reconnecting",
          states[:4] == ["connecting", "reconnecting", "connecting", "reconnecting"] and "connected" in states, states)
    eq("remote phones are dropped once, when the link is lost", lost, ["reconnecting"])
    check("one session was made per connect", ("open",) in media.calls)
    cloud.open_session = real_open
    cloud.reachable = lambda: True

with section("a bridge without its audio libraries says so and never connects"):
    b2 = Bridge()
    m2 = FakeMedia()
    m2.missing = "The audio bridge is not installed in this version of Relay."

    async def go2():
        b2.begin(m2)

    run(go2())
    eq("state", (b2.state, b2.detail), ("missing", m2.missing))

# ------------------------------------------------------------------ who may connect remotely
with section("remote allowed users"):
    check("nobody is allowed until an admin says so", not store.allowed(alice.id))
    try:
        store.sign_in("alice", "correct horse")
        check("a user not marked remote allowed is refused", False)
    except Exception as e:  # noqa: BLE001
        check("a user not marked remote allowed is refused", getattr(e, "status", 0) == 403, repr(e))
    try:
        store.sign_in("alice", "wrong")
        check("a wrong password is refused with the same answer", False)
    except Exception as e:  # noqa: BLE001
        same = getattr(e, "message", "")
        check("a wrong password is refused with the same answer", getattr(e, "status", 0) == 403, repr(e))
    store.set_allowed(alice.id, True, owner.id)
    check("an admin marks alice", store.allowed(alice.id))
    cred, row, user = store.sign_in("alice", "correct horse", name="Alice remote")
    check("she gets her own credential", isinstance(cred, str) and len(cred) > 20 and row["name"] == "Alice remote")
    check("which is a remote one", store.is_remote_device(row["id"]))
    local_cred, local_dev = __import__("app.modules.intercom.devices", fromlist=["x"]).create(alice.id)
    check("a local phone's credential is not a remote one", not store.is_remote_device(local_dev["id"]))
    people = {p["id"]: p for p in store.people()}
    eq("the list shows who is allowed and their remote phones", (bool(people[alice.id]["allowed"]),
       people[alice.id]["phones"], bool(people[bob.id]["allowed"])), (True, 1, False))
    kinds = {e["kind"] for e in istore.events(200)}
    check("it is all in the connection log", {"remote_allowed", "remote_signed_in"} <= kinds, kinds)

# ------------------------------------------------------------------ the routes the tunnel carries
with section("the exposed routes are a fixed list"):
    mine = {(m, r.path) for r in routes.router.routes for m in (getattr(r, "methods", None) or {"WEBSOCKET"})
            if not r.path.startswith("/api/admin/")}
    mine = {("WEBSOCKET" if m == "GET" and r.path.endswith("/ws") else m, p) for (m, p) in mine
            for r in [next(x for x in routes.router.routes if x.path == p)]}
    eq("exactly the sign-in and the socket", sorted(mine), sorted(routes.REMOTE_ROUTES))
    check("every one is under the module's prefix", all(p.startswith("/intercom/remote/") for _m, p in routes.REMOTE_ROUTES))

# ------------------------------------------------------------------ the remote socket
viewer = "http://relay.local:8000"


def hello(ws, credential):
    ws.send_text(json.dumps({"type": "hello", "credential": credential}))


def get(ws):
    return json.loads(ws.receive_text())


from app.modules.intercom_remote import media as _media  # noqa: E402

# With livekit and aiortc installed the real audio side would start and run its own connect loop; the test stands in
# for it, so the lifespan gets one that is never available.
_media.load = lambda: _media.NoMedia("test")

with TestClient(app, base_url=viewer) as client:
    # The app's own lifespan started the module with no audio side; stand in a working one.
    fm = FakeMedia()
    bridge.media = fm
    bridge.session_id = "bridge001"
    bridge._set("connected", "Connected to Cloudflare")

    with section("the remote phone page may use the microphone, and only it"):
        r = client.get("/intercom/remote/")
        eq("the page is served", r.status_code, 200)
        check("its Permissions-Policy allows the microphone for itself",
              "microphone=(self)" in r.headers.get("permissions-policy", ""), r.headers.get("permissions-policy"))
        check("its script does not get it", "microphone=()" in client.get(
            "/intercom/remote/phone.js").headers.get("permissions-policy", ""))

    with section("signing in over the remote address"):
        r = client.post("/intercom/remote/api/sign-in", json={"username": "alice", "password": "correct horse"})
        eq("an allowed user signs in", r.status_code, 200)
        body = r.json()
        cred = body["credential"]
        eq("…and is told where the socket is", body["socket"], "/intercom/remote/ws")
        r = client.post("/intercom/remote/api/sign-in", json={"username": "bob", "password": "correct horse"})
        eq("someone not marked remote allowed is refused", r.status_code, 403)
        r = client.post("/intercom/remote/api/sign-in", json={"username": "alice", "password": "nope"})
        eq("a wrong password is refused the same way", r.status_code, 403)
        routes._tries.clear()
        for _ in range(routes.SIGN_INS_PER_MIN):
            client.post("/intercom/remote/api/sign-in", json={"username": "x", "password": "y"})
        eq("too many tries from one address are slowed", client.post(
            "/intercom/remote/api/sign-in", json={"username": "alice", "password": "correct horse"}).status_code, 429)
        routes._tries.clear()

    with section("a local credential is refused on the remote socket"):
        with client.websocket_connect("/intercom/remote/ws") as ws:
            hello(ws, local_cred)
            try:
                ws.receive_text()
                check("refused", False)
            except WebSocketDisconnect as e:
                eq("refused", e.code, 4401)

    with section("a remote phone connects and listens and talks through Cloudflare"):
        CLOUD.clear()
        with client.websocket_connect("/intercom/remote/ws") as ws:
            hello(ws, cred)
            w = get(ws)
            eq("welcome, with the user's channels", (w["type"], sorted(c["id"] for c in w["channels"])),
               ("welcome", sorted([general["id"], stage["id"]])))
            check("it is registered like any phone", presence.online(w["device"]["id"]))
            ws.send_text(json.dumps({"type": "token", "channel": general["id"]}))
            check("a LiveKit token is refused remotely", get(ws)["type"] == "error")
            ws.send_text(json.dumps({"type": "cf.pull"}))
            check("pulling before a session is refused", get(ws)["type"] == "error")
            ws.send_text(json.dumps({"type": "cf.open"}))
            s = get(ws)
            eq("a Cloudflare session of its own", s["type"], "cf.session")
            check("with TURN servers to connect through", any(
                u.startswith("turns:") for x in s.get("iceServers", []) for u in x["urls"]), s.get("iceServers"))
            ws.send_text(json.dumps({"type": "cf.pull", "tracks": [{"trackName": "someone-elses"}]}))
            p = get(ws)
            eq("Relay picks what it pulls", p["type"], "cf.pulled")
            pulled = [c for c in CLOUD if c[0] == "tracks_new"][-1]
            names = sorted(t["trackName"] for t in pulled[2]["tracks"])
            eq("only its listening channels' mixes, from the bridge's session",
               (names, {t["sessionId"] for t in pulled[2]["tracks"]}),
               (sorted([track_name(general["id"]), track_name(stage["id"])]), {"bridge001"}))
            check("the mixes were started on the audio side",
                  {c[1] for c in fm.calls if c[0] == "listen"} == {general["id"], stage["id"]})
            ws.send_text(json.dumps({"type": "cf.push", "sessionDescription": {"type": "offer", "sdp": "v=0"}, "mid": "0"}))
            eq("pushing the microphone is answered", get(ws)["type"], "cf.pushed")
            pushed = [c for c in CLOUD if c[0] == "tracks_new"][-1]
            eq("under a name Relay chose", pushed[2]["tracks"][0]["trackName"], mic_name(w["device"]["id"]))
            check("nothing is pulled before the phone says its connection is up", not [c for c in fm.calls if c[0] == "talk"])
            n = len(CLOUD)
            ws.send_text(json.dumps({"type": "cf.pull"}))
            check("pulling the mixes before its connection is up is refused at once, without asking Cloudflare",
                  get(ws)["type"] == "error" and len(CLOUD) == n)
            ws.send_text(json.dumps({"type": "cf.connected"}))
            eq("its connection being up is acknowledged", get(ws)["type"], "cf.ack")
            check("and handed to the audio side, soon after, for each channel it may talk on",
                  wait_for(lambda: {c[1] for c in fm.calls if c[0] == "talk"} == {general["id"], stage["id"]}, 8.0))
            ws.send_text(json.dumps({"type": "cf.push", "sessionDescription": {"type": "offer", "sdp": ""}, "mid": "0"}))
            eq("a bad offer is refused", get(ws)["type"], "error")
            ws.send_text(json.dumps({"type": "cf.renegotiate", "sessionDescription": {"type": "answer", "sdp": "v=0"}}))
            eq("an answer is passed on", get(ws)["type"], "cf.renegotiated")
            big = "v=0\r\n" + "a=candidate:1 1 udp 1 203.0.113.1 3478 typ relay raddr 0.0.0.0 rport 0\r\n" * 90
            check("an answer with many TURN candidates is over the intercom's usual 4 KB", len(big) > 4096)
            ws.send_text(json.dumps({"type": "cf.renegotiate", "sessionDescription": {"type": "answer", "sdp": big}}))
            eq("…and still passed on for a remote phone", get(ws)["type"], "cf.renegotiated")
            ws.send_text(json.dumps({"type": "cf.diag", "ice": "checking", "local": ["host udp v6"] * 50, "pairs": "x" * 999}))
            eq("why audio did not connect is logged and acknowledged", get(ws)["type"], "cf.ack")
            ws.send_text(json.dumps({"type": "talk.request", "channels": [general["id"]]}))
            msgs = [get(ws) for _ in range(2)]
            check("talking goes through the same arbiter as a local phone",
                  any(m.get("type", "").startswith("talk") for m in msgs), msgs)
            dev_id = w["device"]["id"]
        wait = __import__("_harness").wait_for
        check("closing the socket frees its Cloudflare session and mixes",
              wait(lambda: any(c == ("close", s["sessionId"]) for c in CLOUD)) and
              wait(lambda: {c[1] for c in fm.calls if c[0] == "unlisten"} == {general["id"], stage["id"]}))
        check("the session is on record", wait(lambda: any(
            x["user_id"] == alice.id and x["ended_at"] for x in store.sessions())))

    with section("the internet goes: remote phones are told and cut, local ones stay"):
        local_cred2, _d = __import__("app.modules.intercom.devices", fromlist=["x"]).create(bob.id)
        with client.websocket_connect("/intercom/remote/ws") as rws, client.websocket_connect("/intercom/ws") as lws:
            hello(rws, cred)
            get(rws)
            hello(lws, local_cred2)
            eq("the local phone is on", get(lws)["type"], "welcome")
            client.portal.call(remote.crew.drop_all, "link_lost")
            m = get(rws)
            eq("the remote phone is told", (m["type"], m["state"]), ("link", "lost"))
            try:
                rws.receive_text()
                check("and disconnected", False)
            except WebSocketDisconnect as e:
                eq("and disconnected", e.code, remote.CODE_LINK_LOST)
            lws.send_text(json.dumps({"type": "ping"}))
            eq("the local phone carries on", get(lws)["type"], "pong")
        bridge._set("reconnecting", "Lost the link to Cloudflare. Trying again.")
        with client.websocket_connect("/intercom/remote/ws") as ws:
            hello(ws, cred)
            try:
                ws.receive_text()
                check("nobody connects while the link is down", False)
            except WebSocketDisconnect as e:
                eq("nobody connects while the link is down", e.code, 4401)
        bridge._set("connected", "Connected to Cloudflare")

    with section("taking remote access away signs the phones out"):
        with client.websocket_connect("/intercom/remote/ws") as ws:
            hello(ws, cred)
            get(ws)
            store.set_allowed(alice.id, False, owner.id)
            ids = store.revoke_user_phones(alice.id, owner.id)
            client.portal.call(remote.drop_user, alice.id, "remote_removed")
            try:
                ws.receive_text()
                check("cut", False)
            except WebSocketDisconnect as e:
                eq("cut", e.code, 4403)
        check("the phone is signed out", bool(ids))
        r = client.post("/intercom/remote/api/sign-in", json={"username": "alice", "password": "correct horse"})
        eq("and cannot sign in again", r.status_code, 403)

with section("the audio helpers"):
    import numpy as np
    from app.modules.intercom_remote import media

    eq("stereo is averaged to mono", media.to_mono(np.array([100, 300, -100, -300], np.int16), 2).tolist(), [200, -200])
    eq("planar stereo too", media.to_mono(np.array([100, -100, 300, -300], np.int16), 2, True).tolist(), [200, -200])
    m = media.Mixer()
    eq("no sources is silence", int(np.abs(m.pull()).sum()), 0)
    m.add("a", np.full(media.FRAME * 2, 1000, np.int16))
    m.add("b", np.full(media.FRAME, 2500, np.int16))
    eq("sources are summed", int(m.pull()[0]), 3500)
    eq("a source that ran dry adds silence", int(m.pull()[0]), 1000)
    m.add("c", np.full(media.FRAME, 30000, np.int16))
    m.add("d", np.full(media.FRAME, 30000, np.int16))
    eq("the sum is clipped, not wrapped", int(m.pull().max()), 32767)
    m.drop("c")
    check("a dropped source is gone", "c" not in m.q)
    for _ in range(10):
        m.add("slow", np.ones(media.RATE, np.int16))
    check("a source that falls behind is trimmed", m.n["slow"] <= media.MAX_BUFFER + media.RATE)
    n = media.NoMedia("why")
    eq("the stand-in says why", n.unavailable(), "why")
    check("and is never alive", not n.alive())
    live = media.LiveMedia()
    check("mixes go out and microphones come in on separate Cloudflare links (aiortc renumbers payload types on "
          "its own offers)", live.out is not live.into)
    live.out.session, live.into.session = "out1", "in1"
    eq("phones pull the mixes from the outgoing link's session", live.session, "out1")

    class _NoPerms:
        local_participant = None
    check("an SDK without participant permissions just tries to publish", media._can_publish(_NoPerms()))

from _harness import done  # noqa: E402

done()
