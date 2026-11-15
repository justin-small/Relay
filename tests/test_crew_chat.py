"""Crew team chat (#230): a team channel and a channel per crew role, 30 days of history with the newest 100 shown,
live delivery to open chats, quiet notifications that are off until chosen. Run: python tests/test_crew_chat.py"""
import asyncio
import os
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
from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import chat, paging, people, webpush  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"crew": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
booth = _db.client(app, role="operator", base_url="http://relay.local:8001", username="booth")
cam1 = _db.client(app, role="member", base_url="http://relay.local:8001", username="cam1")
cam2 = _db.client(app, role="member", base_url="http://relay.local:8001", username="cam2")
aud = _db.client(app, role="member", base_url="http://relay.local:8001", username="audio")
anon = TestClient(app, base_url="http://relay.local:8001")
viewer_port = TestClient(app, base_url="http://relay.local:8000")
U = {n: users.get_by_username(n) for n in ("cam1", "cam2", "audio", "booth")}
for n, uid in (("cam1", "Camera 1"), ("cam2", "camera  1"), ("audio", "Audio")):
    admin.put(f"/api/admin/crew/people/{U[n].id}/role", json={"role": uid})

print("channels")
ch = lambda c: [(x["id"], x["title"]) for x in c.get("/crew/api/chat/channels").json()["channels"]]
eq("team and your own role", ch(cam1), [("team", "Team"), ("role:camera 1", "Camera 1")])
eq("the same role in other letters is the same channel", ch(cam2), [("team", "Team"), ("role:camera 1", "camera 1")])
eq("another role, another channel", ch(aud), [("team", "Team"), ("role:audio", "Audio")])
eq("a moderator sees every role in use", sorted(i for i, _ in ch(booth)), ["role:audio", "role:camera 1", "team"])
eq("signed out cannot", anon.get("/crew/api/chat/channels").status_code, 401)
eq("the viewer port has none of it", viewer_port.get("/crew/api/chat/channels").status_code, 404)

print("posting")
send = lambda c, channel, body: c.post("/crew/api/chat/send", json={"channel": channel, "body": body})
r = send(cam1, "team", "Anyone have a spare battery?")
eq("a member posts to the team", (r.status_code, r.json()["message"]["author"], r.json()["message"]["role"]), (200, "Cam1", "Camera 1"))
eq("to their own role channel", send(cam1, "role:camera 1", "Check shot 3").status_code, 200)
eq("not to another role's channel", send(cam1, "role:audio", "hi").status_code, 403)
eq("nor to one that does not exist", send(cam1, "nope", "hi").status_code, 403)
eq("a moderator may post in any role channel", send(booth, "role:audio", "Soundcheck at 8").status_code, 200)
eq("empty is refused", send(cam1, "team", "   ").status_code, 400)
eq("too long is refused", send(cam1, "team", "x" * 501).status_code, 400)
eq("a control character is refused", send(cam1, "team", "a\x00b").status_code, 400)
eq("signed out cannot post", send(anon, "team", "hi").status_code, 401)

print("reading")
hist = lambda c, channel, q="": c.get("/crew/api/chat/history", params={"channel": channel, **({"after": q} if q else {})})
eq("the team reads the team channel", [m["body"] for m in hist(aud, "team").json()["messages"]], ["Anyone have a spare battery?"])
eq("a role channel is private to its role", hist(aud, "role:camera 1").status_code, 403)
eq("the role reads its own", [m["body"] for m in hist(cam2, "role:camera 1").json()["messages"]], ["Check shot 3"])
eq("a moderator reads any", hist(booth, "role:camera 1").status_code, 200)
first = hist(cam1, "team").json()["messages"][0]["id"]
send(cam1, "team", "second")
eq("only what is newer than an id", [m["body"] for m in hist(cam1, "team", first).json()["messages"]], ["second"])

print("history is kept 30 days and the newest 100 are shown")
t0 = 5_000_000.0
chat._recent.clear()          # these messages are dated in the past; the rate limit counts real time
for i in range(120):
    chat.post(U["booth"], "team", f"m{i}", now=t0 + i * 20)
shown = chat.history("team")
eq("100 shown, the newest, oldest first", (len(shown), shown[0]["body"], shown[-1]["body"]), (100, "m20", "m119"))
chat.post(U["booth"], "team", "late", now=t0 + 31 * 86400)
eq("a message older than 30 days is gone", db.one("select count(*) as n from crew_chat_messages where created_ts < ?",
                                                  (t0 + 86400,))["n"], 0)

print("a few at a time")
chat._recent.clear()
res = [chat.post(U["cam2"], "team", f"r{i}", now=9_000_000.0 + i) if i < 5 else None for i in range(5)]
try:
    chat.post(U["cam2"], "team", "r5", now=9_000_005.0)
    eq("a sixth in ten seconds is refused", False, True)
except chat.Refused as exc:
    eq("a sixth in ten seconds is refused", exc.status, 429)
eq("and allowed again later", chat.post(U["cam2"], "team", "ok", now=9_000_030.0)["body"], "ok")

print("deleting")
mine = send(cam1, "team", "oops").json()["message"]["id"]
eq("a member may not delete another's", aud.delete(f"/crew/api/chat/message/{mine}").status_code, 403)
eq("the author may", cam1.delete(f"/crew/api/chat/message/{mine}").status_code, 200)
theirs = send(cam1, "team", "rude").json()["message"]["id"]
eq("a moderator may delete anyone's", booth.delete(f"/crew/api/chat/message/{theirs}").status_code, 200)
eq("gone twice is not found", booth.delete(f"/crew/api/chat/message/{theirs}").status_code, 404)

print("the open chats")


async def live():
    chat.hub.loop = asyncio.get_running_loop()
    s_cam = chat.hub.subscribe(U["cam1"], "role:camera 1")
    s_aud = chat.hub.subscribe(U["audio"], "role:audio")
    s_mod = chat.hub.subscribe(U["booth"], None)
    chat.hub.publish("team", {"type": "message", "message": {"id": 1}})
    chat.hub.publish("role:camera 1", {"type": "message", "message": {"id": 2}})
    got = {n: [] for n in ("cam", "aud", "mod")}
    for n, s in (("cam", s_cam), ("aud", s_aud), ("mod", s_mod)):
        while not s.queue.empty():
            got[n].append(s.queue.get_nowait()["message"]["id"])
    eq("the team reaches everyone, a role only its people and moderators", got,
       {"cam": [1, 2], "aud": [1], "mod": [1, 2]})
    eq("who is watching", chat.hub.watching(), {U["cam1"].id, U["audio"].id, U["booth"].id})
    chat.hub.signed_out(U["cam1"].id)
    await asyncio.sleep(0.01)
    eq("a person who signs out has their stream ended", s_cam.queue.get_nowait(), None)
    for _ in range(205):
        chat.hub.publish("team", {"type": "tick"})
    drained = []
    while not s_aud.queue.empty():
        drained.append(s_aud.queue.get_nowait())
    eq("a stream that falls far behind is closed, not buffered", drained[-1] is None and len(drained) <= 201, True)
    for s in (s_cam, s_aud, s_mod):
        chat.hub.unsubscribe(s)


asyncio.run(live())

print("quiet notifications")
eq("off until chosen", [c["push"] for c in cam1.get("/crew/api/chat/channels").json()["channels"]], [False, False])
eq("a channel you cannot read cannot be turned on", cam1.put("/crew/api/chat/push", json={"channel": "role:audio", "on": True}).status_code, 400)
eq("turn the team on", [c["push"] for c in cam1.put("/crew/api/chat/push", json={"channel": "team", "on": True}).json()["channels"]], [True, False])
eq("a non-boolean is refused", cam1.put("/crew/api/chat/push", json={"channel": "team", "on": "yes"}).status_code, 400)
k = webpush.new_key()
sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/cam1", "keys": {"p256dh": webpush.b64e(webpush.public_bytes(k)),
                                                                       "auth": webpush.b64e(b"0123456789abcdef")}}
cam1.post("/crew/api/push", json=sub)
aud.put("/crew/api/chat/push", json={"channel": "team", "on": True})
sent = []
webpush.post = lambda endpoint, headers, body: (sent.append(endpoint) or 201)
msg = chat.post(U["booth"], "team", "Everyone to the stage")
eq("an opted-in person with a phone is told", (chat.notify("team", msg, set()), len(sent)), (1, 1))
sent.clear()
eq("not when they are already looking at the chat", (chat.notify("team", msg, {U["cam1"].id}), len(sent)), (0, 0))
own = chat.post(U["cam1"], "team", "mine")
eq("nor the author", chat.notify("team", own, set()), 0)
cam1.put("/crew/api/chat/push", json={"channel": "team", "on": False})
eq("nor once turned off", chat.notify("team", msg, set()), 0)

print("a message is text")
eq("stored as typed", send(cam1, "team", "<b>hi</b>").json()["message"]["body"], "<b>hi</b>")
eq("the chat's script sets words as text", "innerHTML" in open("app/modules/crew/static/chat.js").read(), False)

print("account removal")
chat._recent.clear()
chat.post(U["cam2"], "team", "last words")
users.delete(U["cam2"].id, actor=None)
eq("their messages stay, unattributed", db.one("select count(*) as n from crew_chat_messages where user_id is null")["n"] > 0, True)

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
