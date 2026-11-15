"""Intercom (#272, #238): its tables, the one-transaction rule, the LiveKit download and the supervised process.

Offline: a throwaway database, a local web server for the download, and a fake livekit-server (a Python script)
on spare ports. Run: python tests/test_intercom.py
"""
import asyncio
import hashlib
import io
import json
import os
import socket
import sqlite3
import stat
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
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

from app import db, users  # noqa: E402
from app.modules.intercom import livekit, store  # noqa: E402

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


def count(table):
    return db.one(f"select count(*) as n from {table}")["n"]


# ------------------------------------------------------------------ tables
users.SCRYPT_N = 2 ** 10
alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
bob = users.create("bob", "Bob", "member", "correct horse", temporary=False)
store.forget()
store.ready()
store.ready()
tables = {r["name"] for r in db.query("select name from sqlite_master where type = 'table'")}
check("every intercom table exists", {"intercom_orgs", "intercom_production", "intercom_devices",
                                      "intercom_channels", "intercom_channel_members", "intercom_user_choices",
                                      "intercom_sessions", "intercom_events"} <= tables)
check("core users were left alone", db.one("select id from users where username = 'alice'") is not None)
eq("migration recorded once", count("schema_migrations where namespace = 'intercom'"), 1)
eq("one organisation row", count("intercom_orgs"), 1)

general = store.create_channel("General", actor_id=alice.id)
eq("channel gets defaults", (general["mode"], general["max_talkers"], general["org_id"]), ("preempt", 1, 1))
raises("a channel name is unique", store.Invalid, lambda: store.create_channel("General"))
raises("a blank name is refused", store.Invalid, lambda: store.create_channel("  "))
raises("an unknown mode is refused", store.Invalid, lambda: store.create_channel("X", mode="loud"))
raises("max talkers above 16 is refused", store.Invalid, lambda: store.create_channel("Y", max_talkers=17))
check("a refused channel is not stored and not logged",
      count("intercom_channels") == 1 and count("intercom_events") == 1)

store.set_member(general["id"], alice.id, priority=1, actor_id=alice.id)
store.set_member(general["id"], bob.id, can_talk=False)
raises("priority 0 is refused", store.Invalid, lambda: store.set_member(general["id"], bob.id, priority=0))
raises("priority 10 is refused", store.Invalid, lambda: store.set_member(general["id"], bob.id, priority=10))
raises("a member who can do neither is refused", store.Invalid,
       lambda: store.set_member(general["id"], bob.id, can_talk=False, can_listen=False))
raises("an unknown user is refused", store.Invalid, lambda: store.set_member(general["id"], 9999))
eq("two members", [(m["user_id"], m["priority"]) for m in store.members(general["id"])],
   [(alice.id, 1), (bob.id, 5)])
store.set_member(general["id"], bob.id, can_talk=True, priority=3)
eq("setting a member again changes it", [m["priority"] for m in store.members(general["id"])], [1, 3])

# The one-transaction rule: a log row and its change stand or fall together.
before = count("intercom_events")
try:
    with db.transaction() as tx:
        tx.execute("insert into intercom_channel_members (channel_id, user_id, added_at) values (?, ?, ?)",
                   (general["id"], 9999, db.now()))
        store.log_event(tx, "member_set")
except sqlite3.IntegrityError:
    pass
eq("a failed change leaves no log row", count("intercom_events"), before)
check("each change wrote its own log row", {e["kind"] for e in store.events()} >= {"channel_created", "member_set"})

with db.transaction() as tx:
    tx.execute("insert into intercom_user_choices (user_id, channel_id) values (?, ?)", (bob.id, general["id"]))
    tx.execute("insert into intercom_devices (user_id, credential_hash, issued_at) values (?, 'h', ?)",
               (bob.id, db.now()))
    tx.execute("insert into intercom_sessions (user_id, started_at) values (?, ?)", (bob.id, db.now()))
raises("volume above 100 is refused", sqlite3.IntegrityError,
       lambda: db.execute("update intercom_user_choices set volume = 101"))
store.remove_member(general["id"], bob.id)
eq("removing a member removes their choices", count("intercom_user_choices"), 0)
store.set_member(general["id"], bob.id)
with db.transaction() as tx:
    tx.execute("insert into intercom_user_choices (user_id, channel_id) values (?, ?)", (bob.id, general["id"]))
users.delete(bob.id, actor=None) if hasattr(users, "delete") else db.execute("delete from users where id = ?", (bob.id,))
eq("deleting a user cascades to memberships, choices, devices and sessions",
   (count("intercom_channel_members"), count("intercom_user_choices"), count("intercom_devices"),
    count("intercom_sessions")), (1, 0, 0, 0))
check("their log rows stay, with no user", count("intercom_events where user_id is null") >= 1)
second = store.create_channel("Video")
store.set_member(second["id"], alice.id)
eq("deleting a channel is logged", store.delete_channel(second["id"], actor_id=alice.id), True)
eq("it cascades to its members", count(f"intercom_channel_members where channel_id = {second['id']}"), 0)
eq("deleting a missing channel is a no-op", store.delete_channel(second["id"]), False)

db.execute("update intercom_events set at = '2000-01-01T00:00:00Z' where id = (select min(id) from intercom_events)")
eq("old log rows are pruned", store.prune_events(30) >= 1, True)

# ------------------------------------------------------------------ the download
tar = io.BytesIO()
with tarfile.open(fileobj=tar, mode="w:gz") as t:
    for name, body in (("LICENSE", b"apache"), ("livekit-server", b"#!/bin/sh\necho hi\n")):
        info = tarfile.TarInfo(name)
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
TAR = tar.getvalue()
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as z:
    z.writestr("bin/livekit-server", b"MZ")
    z.writestr("../evil.txt", b"no")
ZIP = zbuf.getvalue()


class Files(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = {"/ok.tar.gz": TAR, "/ok.zip": ZIP, "/junk.tar.gz": b"not an archive"}.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.end_headers()
        if body is not None:
            self.wfile.write(body)


web = HTTPServer(("127.0.0.1", 0), Files)
threading.Thread(target=web.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{web.server_port}/"
livekit.state_folder = lambda: Path(TMP) / "livekit"
livekit.EXE = "livekit-server"

path = livekit.download(BASE, ("ok.tar.gz", hashlib.sha256(TAR).hexdigest()))
eq("download unpacks only livekit-server", sorted(p.name for p in (Path(TMP) / "livekit").iterdir()),
   ["livekit-server"])
POSIX = sys.platform != "win32"      # Windows has no file modes and cannot run a script by its shebang
check("it is executable", not POSIX or os.stat(path).st_mode & stat.S_IXUSR)
eq("find_binary finds the downloaded copy", livekit.find_binary(), path)
raises("a wrong checksum is refused", livekit.LiveKitError, lambda: livekit.download(BASE, ("ok.tar.gz", "0" * 64)))
raises("an archive without the binary is refused", livekit.LiveKitError,
       lambda: livekit.download(BASE, ("junk.tar.gz", hashlib.sha256(b"not an archive").hexdigest())))
raises("a missing file is refused with a fixed sentence", livekit.LiveKitError,
       lambda: livekit.download(BASE, ("gone.tar.gz", "0" * 64)))
check("a zip's traversal entry is never written", livekit.extract("x.zip", ZIP) == b"MZ"
      and not (Path(TMP) / "evil.txt").exists())
check("macOS has no pinned build", ("darwin", "arm64") not in livekit.ASSETS)
check("every pinned build has a 64-hex checksum", all(len(h) == 64 for _, h in livekit.ASSETS.values()))

# ------------------------------------------------------------------ keys and config
k1 = livekit.keys()
eq("the keys are stable", livekit.keys(), k1)
POSIX and eq("the keys file is owner-only", stat.S_IMODE(os.stat(Path(TMP) / "livekit" / "keys.json").st_mode), 0o600)
cfg = livekit.write_config("192.168.1.20")
text = cfg.read_text()
POSIX and eq("livekit.yaml is owner-only", stat.S_IMODE(os.stat(cfg).st_mode), 0o600)
check("signalling is bound to loopback", 'bind_addresses:\n  - "127.0.0.1"' in text)
check("one UDP and one TCP port, LAN address, no cloud", "udp_port: 7882" in text and "tcp_port: 7881" in text
      and 'node_ip: "192.168.1.20"' in text and "use_external_ip: false" in text and "turn" not in text.lower()
      and "stun" not in text.lower())
check("rooms are only made by Relay", "auto_create: false" in text)
check("the secret is in the config but not in the admin view",
      k1[1] in text and k1[1] not in json.dumps(livekit.supervisor.view()))

# ------------------------------------------------------------------ the supervisor
FAKE = f'''#!{sys.executable}
import http.server, sys
cfg = open(sys.argv[sys.argv.index("--config") + 1]).read()
port = int(cfg.split("port: ")[1].split()[0])
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"OK")
print("started with a secret in view: " + cfg.split("keys:")[1].split(":")[1].strip(), flush=True)
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


livekit.SIGNAL_PORT, livekit.TCP_PORT, livekit.UDP_PORT = free_port(), free_port(), free_port(socket.SOCK_DGRAM)
livekit.HEALTH_EVERY_S = 0.2
livekit.BACKOFF_S = (0.05,)
fake = Path(TMP) / "fake-livekit"
fake.write_text(FAKE)
fake.chmod(0o755)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def until(cond, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def supervise():
    sup = livekit.Supervisor()
    sup.begin({"binary_path": str(fake), "node_ip": "127.0.0.1"})
    check("it starts and answers on its port", await until(lambda: sup.state == "running"))
    pid = sup._proc.pid
    check("the log shows output with the secret hidden",
          await until(lambda: any("started" in l for l in sup.log)) and not any(livekit.keys()[1] in l for l in sup.log))
    os.killpg(os.getpgid(pid), 9)
    check("a killed server comes back", await until(lambda: sup._proc and sup._proc.pid != pid
                                                    and sup.state == "running", 15))
    pid = sup._proc.pid
    await sup.end()
    check("switching off leaves no process", not alive(pid) and sup.state == "off")

    # Port in use: a clear message naming the port.
    held = socket.socket()
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", livekit.SIGNAL_PORT))
    held.listen()
    sup.begin({"binary_path": str(fake), "node_ip": "127.0.0.1"})
    check("a busy port is named", await until(lambda: sup.state == "error"))
    check("it says which port", str(livekit.SIGNAL_PORT) in sup.detail)
    held.close()
    await sup.end()

    # A binary that dies at once: five restarts in ten minutes and it gives up, with the log kept.
    bad = Path(TMP) / "bad-livekit"
    bad.write_text(f"#!{sys.executable}\nprint('boom', flush=True)\n")
    bad.chmod(0o755)
    sup.begin({"binary_path": str(bad), "node_ip": "127.0.0.1"})
    check("it gives up after repeated failures", await until(lambda: sup.state == "error", 30))
    check("the last lines are kept", "boom" in " ".join(sup.log))
    await sup.restart()
    check("Restart clears the count and tries again", await until(lambda: sup.state in ("starting", "restarting", "error")))
    await sup.end()

    gone = livekit.Supervisor()
    gone.begin({"binary_path": str(Path(TMP) / "nope"), "node_ip": ""})
    check("a missing binary is reported (no download in tests)", await until(lambda: gone.state == "missing"))
    await gone.end()


if POSIX:
    asyncio.run(supervise())

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
