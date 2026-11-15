"""Intercom's /intercom/rtc WebSocket proxy (#238).

Offline: a throwaway database, the app's own lifespan and a fake LiveKit signalling socket on a spare port.
Run: python tests/test_intercom_rtc.py
"""
import asyncio
import json
import os
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

import websockets  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from app import config, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.intercom import devices, livekit, rooms, rtcproxy, store  # noqa: E402
from app.modules.intercom.presence import presence  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


# ------------------------------------------------------------------ a fake LiveKit /rtc socket
SEEN: list[str] = []


async def echo(ws, path):
    SEEN.append(path)
    async for m in ws:
        await ws.send(m)


ready = threading.Event()
box: dict = {}


def run_fake():
    async def main():
        server = await websockets.serve(echo, "127.0.0.1", 0)
        box["port"] = server.sockets[0].getsockname()[1]
        ready.set()
        await asyncio.Future()

    asyncio.run(main())


threading.Thread(target=run_fake, daemon=True).start()
ready.wait(5)
livekit.SIGNAL_PORT = box["port"]
rooms.WATCH_EVERY_S = rooms.RECONCILE_EVERY_S = 1e9

# ------------------------------------------------------------------ token checks
print("tokens")
KEY, SECRET = "relay", "s3cret"
good = rooms.mint_token(KEY, SECRET, channel_id=4, user_id=7, device_id=9, name="A", can_subscribe=True)
eq("a join token is accepted", rtcproxy.check_token(good, KEY, SECRET), (7, 9))
eq("a wrong secret is refused", rtcproxy.check_token(good, KEY, "other"), None)
eq("a wrong issuer is refused", rtcproxy.check_token(good, "x", SECRET), None)
eq("an expired token is refused", rtcproxy.check_token(good, KEY, SECRET, now=time.time() + 4000), None)
eq("garbage is refused", rtcproxy.check_token("a.b.c", KEY, SECRET), None)
eq("an admin token is refused", rtcproxy.check_token(rooms._admin_token(KEY, SECRET), KEY, SECRET), None)
none_alg = good.split(".")
none_alg[0] = rooms._b64(b'{"alg":"none"}')
eq("alg none is refused", rtcproxy.check_token(".".join(none_alg), KEY, SECRET), None)
noexp = rooms.sign({"iss": KEY, "sub": "u7-d9", "video": {"roomJoin": True, "room": "ch-4"}}, SECRET)
eq("a token without expiry is refused", rtcproxy.check_token(noexp, KEY, SECRET), None)
other = rooms.sign({"iss": KEY, "sub": "u7-d9", "exp": int(time.time()) + 60,
                    "video": {"roomJoin": True, "room": "lobby"}}, SECRET)
eq("a room that is not ours is refused", rtcproxy.check_token(other, KEY, SECRET), None)

# ------------------------------------------------------------------ the proxy
print("proxy")
users.SCRYPT_N = 2 ** 10
store.forget()
store.ready()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": True}}})
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
general = store.create_channel("General")
store.set_member(general["id"], alice.id, priority=2)
cred, dev = devices.create(alice.id, user_agent="Mozilla/5.0 (iPhone)")
key, secret = livekit.keys()
token = rooms.mint_token(key, secret, channel_id=general["id"], user_id=alice.id, device_id=dev["id"],
                         name="Alice", can_subscribe=True)


def refused(client, url):
    try:
        with client.websocket_connect(url):
            return False
    except (WebSocketDisconnect, Exception):  # noqa: BLE001
        return True


with TestClient(app, base_url="http://relay.local:8001") as c:
    check("no token is refused", refused(c, "/intercom/rtc"))
    check("a forged token is refused", refused(c, "/intercom/rtc?access_token=" + token[:-3] + "AAA"))
    check("a good token is refused while the phone is not connected to Relay",
          refused(c, "/intercom/rtc?access_token=" + token))

    with c.websocket_connect("/intercom/ws") as phone:
        phone.send_text(json.dumps({"type": "hello", "credential": cred}))
        eq("the phone is welcomed", json.loads(phone.receive_text())["type"], "welcome")
        with c.websocket_connect(f"/intercom/rtc?access_token={token}&auto_subscribe=1") as rtc:
            rtc.send_text("hello")
            eq("text goes through to LiveKit and back", rtc.receive_text(), "hello")
            rtc.send_bytes(b"\x01\x02")
            eq("binary goes through too", rtc.receive_bytes(), b"\x01\x02")
        check("the query string reaches LiveKit unchanged",
              SEEN and SEEN[-1] == f"/rtc?access_token={token}&auto_subscribe=1")

        with c.websocket_connect(f"/intercom/rtc?access_token={token}") as rtc:
            rtc.send_text("x")
            rtc.receive_text()
            row = devices.revoke(dev["id"], actor_id=alice.id)
            c.portal.call(presence.revoked, row)
            try:
                rtc.receive_text()
                cut = False
            except WebSocketDisconnect as exc:
                cut = exc.code == 4403
            check("signing the phone out cuts its signalling", cut)

    livekit.SIGNAL_PORT = 1          # nothing listens: LiveKit is down
    cred2, dev2 = devices.create(alice.id, user_agent="Mozilla/5.0 (iPhone)")
    token2 = rooms.mint_token(key, secret, channel_id=general["id"], user_id=alice.id, device_id=dev2["id"],
                              name="Alice", can_subscribe=True)
    with c.websocket_connect("/intercom/ws") as phone:
        phone.send_text(json.dumps({"type": "hello", "credential": cred2}))
        phone.receive_text()
        check("LiveKit being down closes the socket", refused(c, f"/intercom/rtc?access_token={token2}"))

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
