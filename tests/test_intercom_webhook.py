"""Intercom's LiveKit webhook and Windows job object (#238).

Offline, with a throwaway state folder and database. Run: python tests/test_intercom_webhook.py
The job-object check runs on Windows only (it kills a process to prove its child dies with it).
"""
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import tempfile
import textwrap
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

from app import config, db, paths, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.intercom import livekit, store, webhook, winjob  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def sign(body: bytes, key="relay", secret="s3cret", exp=None, sha=None, alg="HS256") -> str:
    """What livekit-server puts in Authorization: an HS256 JWT vouching for the body."""
    head = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    claims = {"iss": key, "nbf": int(time.time()) - 5, "exp": exp or int(time.time()) + 300,
              "sha256": sha or base64.b64encode(hashlib.sha256(body).digest()).decode()}
    pay = b64(json.dumps(claims).encode())
    sig = hmac.new(secret.encode(), f"{head}.{pay}".encode(), hashlib.sha256).digest()
    return f"{head}.{pay}.{b64(sig)}"


def count(table):
    return db.one(f"select count(*) as n from {table}")["n"]


print("verify")
body = b'{"event":"room_started"}'
good = sign(body)
check("a signed body passes", webhook.verify(good, body, "relay", "s3cret"))
check("a Bearer prefix is accepted", webhook.verify("Bearer " + good, body, "relay", "s3cret"))
check("a changed body fails", not webhook.verify(good, body + b" ", "relay", "s3cret"))
check("the wrong secret fails", not webhook.verify(sign(body, secret="other"), body, "relay", "s3cret"))
check("the wrong key fails", not webhook.verify(sign(body, key="other"), body, "relay", "s3cret"))
check("an expired token fails", not webhook.verify(sign(body, exp=int(time.time()) - 3600), body, "relay", "s3cret"))
check("a wrong body hash fails", not webhook.verify(sign(body, sha="AAAA"), body, "relay", "s3cret"))
check("alg none fails", not webhook.verify(sign(body, alg="none"), body, "relay", "s3cret"))
check("an empty header fails", not webhook.verify("", body, "relay", "s3cret"))
check("garbage fails", not webhook.verify("a.b.c", body, "relay", "s3cret"))
check("not three parts fails", not webhook.verify("abc", body, "relay", "s3cret"))

print("parse")
ev = webhook.parse(json.dumps({"event": "participant_joined", "room": {"name": "ch-7"},
                               "participant": {"identity": "u3-d9", "name": "Alice"}}).encode())
eq("a join is read", (ev["kind"], ev["channel"], ev["user"], ev["device"]), ("participant_joined", 7, 3, 9))
check("a name never reaches the log detail", "Alice" not in json.dumps(ev))
check("another room is ignored", webhook.parse(json.dumps({"event": "room_started", "room": {"name": "x"}}).encode())
      is None)
check("an unknown event is ignored", webhook.parse(json.dumps({"event": "egress_started",
                                                               "room": {"name": "ch-1"}}).encode()) is None)
check("not JSON is ignored", webhook.parse(b"nope") is None)
mic = webhook.parse(json.dumps({"event": "track_published", "room": {"name": "ch-1"},
                                "participant": {"identity": "weird"}, "track": {"source": "MICROPHONE"}}).encode())
eq("a track from a odd identity has no user", (mic["user"], mic["detail"].get("source")), (None, "microphone"))

print("config")
text = livekit.config_text("10.0.0.5", "relay", "s3cret", "http://127.0.0.1:80/intercom/livekit-webhook")
check("webhook block is written", 'webhook:\n  api_key: "relay"\n  urls:\n    - "http://127.0.0.1:80/intercom/'
      'livekit-webhook"' in text)
check("a lease can unmute a talker's track", "enable_remote_unmute: true" in text)
check("no webhook block without a URL", "webhook:" not in livekit.config_text("10.0.0.5", "relay", "s3cret"))
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
check("the URL is on this computer's viewer port",
      livekit.webhook_url() == "http://127.0.0.1:8000/intercom/livekit-webhook")

print("route")
users.SCRYPT_N = 2 ** 10
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
store.forget()
store.ready()
chan = store.create_channel("General")
with db.transaction() as tx:
    tx.execute("insert into intercom_devices (user_id, credential_hash, issued_at) values (?, 'h', ?)",
               (alice.id, db.now()))
dev = db.one("select id from intercom_devices")["id"]
http = TestClient(app, base_url="http://relay.local:8000")
paths.make_private_dir(livekit.state_folder())
key, secret = livekit.keys()


def post(payload, auth=True, **kw):
    raw = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
    headers = {"Authorization": sign(raw, key=key, secret=secret)} if auth else {}
    return http.post(webhook.PATH, content=raw, headers={**headers, "Content-Type": "application/webhook+json"})


evt = {"event": "participant_joined", "room": {"name": f"ch-{chan['id']}"},
       "participant": {"identity": f"u{alice.id}-d{dev}"}}
eq("404 while the module is off", post(evt).status_code, 404)
config.save({"modules": {"intercom": {"enabled": True}}})
_modules.attach("intercom")
before = count("intercom_events")
eq("a signed event is accepted", post(evt).status_code, 200)
row = db.one("select * from intercom_events order by id desc limit 1")
eq("it is logged with its ids", (row["kind"], row["channel_id"], row["user_id"], row["device_id"]),
   ("participant_joined", chan["id"], alice.id, dev))
eq("an unsigned event is refused", post(evt, auth=False).status_code, 401)
bad = json.dumps(evt).encode()
eq("a forged signature is refused", http.post(webhook.PATH, content=bad, headers={
    "Authorization": sign(bad, secret="guess")}).status_code, 401)
eq("refused events write nothing", count("intercom_events"), before + 1)
eq("an event for a deleted user is still logged, without the user",
   post({**evt, "event": "participant_left", "participant": {"identity": "u9999-d9999"}}).status_code, 200)
last = db.one("select * from intercom_events order by id desc limit 1")
eq("unknown ids are left out", (last["kind"], last["user_id"], last["device_id"]), ("participant_left", None, None))
eq("a room that is not a channel's is acknowledged and ignored",
   post({"event": "room_started", "room": {"name": "other"}}).status_code, 200)
eq("and not logged", db.one("select * from intercom_events order by id desc limit 1")["id"], last["id"])
eq("a huge body is refused", http.post(webhook.PATH, content=b"x" * (webhook.MAX_BODY + 1),
                                       headers={"Authorization": "x"}).status_code, 413)

print("job object")
check("off Windows it does nothing", sys.platform == "win32" or winjob.attach(os.getpid()) is False)
if sys.platform == "win32":
    # A parent puts a sleeping child in the job, then dies the hard way: the child must go with it.
    script = textwrap.dedent(f"""
        import os, subprocess, sys
        sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})
        from app.modules.intercom import winjob
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print(child.pid, winjob.attach(child.pid), flush=True)
        os._exit(1)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60).stdout.split()
    eq("the child was attached", out[1:2], ["True"])
    pid = int(out[0])

    def alive(p):
        r = subprocess.run(["tasklist", "/FI", f"PID eq {p}", "/NH"], capture_output=True, text=True)
        return str(p) in r.stdout

    end = time.time() + 15
    while alive(pid) and time.time() < end:
        time.sleep(0.3)
    check("the child dies when its parent is killed", not alive(pid))

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
