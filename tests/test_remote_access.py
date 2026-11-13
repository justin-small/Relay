"""Remote access (#50): enrolling with Relay's cloud, checking in, running cloudflared, releasing, and that the secret
and the tunnel token never leave config.json.

Offline: a fake relay-cloud on 127.0.0.1 and a fake cloudflared (a Python script). Run: python tests/test_remote_access.py
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()
os.environ["RELAY_VERSION"] = "9.9.9"

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.remote_access import cloud, service as svcmod, tunnel as tunmod  # noqa: E402
from app.modules.remote_access.service import service  # noqa: E402
from app.modules.remote_access.tunnel import Tunnel, tunnel  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond):
    eq(label, bool(cond), True)


# ------------------------------------------------------------------ a fake relay-cloud
GOOD_CODE = "ABCD-EFGH-JKMN"
seen = []                     # (method, path, body, authorization)
cloud_state = {"secret": None, "fail_next": None, "port": None, "version": None, "released": False}


class FakeCloud(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def _record(self, body):
        seen.append((self.command, self.path, body, self.headers.get("Authorization", "")))

    def _install(self, code=200):
        self._send(code, {"install_id": "inst-1", "hostname": "k7m2p9qa.relaychurchsystems.com",
                          "tunnel_token": f"tok-{cloud_state['port']}"})

    def do_GET(self):
        self._record(None)
        self._send(200, {"ok": True}) if self.path == "/health" else self._send(404, {"error": "not_found"})

    def do_POST(self):
        body = self._body()
        self._record(body)
        if cloud_state["fail_next"]:
            kind, cloud_state["fail_next"] = cloud_state["fail_next"], None
            return self._send(502, {"error": kind})
        if body.get("code", "").replace(" ", "").upper() != GOOD_CODE:
            return self._send(403, {"error": "bad_code"})
        if cloud_state["secret"] not in (None, body["secret"]):
            return self._send(409, {"error": "code_used"})
        cloud_state.update(secret=body["secret"], port=body["viewer_port"], version=body["relay_version"])
        self._install(201)

    def _authed(self):
        return not cloud_state["released"] and self.headers.get("Authorization") == f"Bearer {cloud_state['secret']}"

    def do_PUT(self):
        body = self._body()
        self._record(body)
        if not self._authed():
            return self._send(401, {"error": "unauthorised"})
        cloud_state.update(port=body["viewer_port"], version=body["relay_version"])
        self._install()

    def do_DELETE(self):
        self._record(None)
        if not self._authed():
            return self._send(401, {"error": "unauthorised"})
        cloud_state["released"] = True
        self._send(204)


server = HTTPServer(("127.0.0.1", 0), FakeCloud)
threading.Thread(target=server.serve_forever, daemon=True).start()
CLOUD = f"http://127.0.0.1:{server.server_port}"

# ------------------------------------------------------------------ a fake cloudflared
FAKE_LOG = Path(TMP) / "cloudflared.json"
fake = Path(TMP) / "cloudflared"
fake.write_text(f"""#!{sys.executable}
import json, os, sys, time
json.dump({{"argv": sys.argv[1:], "token": os.environ.get("TUNNEL_TOKEN", "")}}, open({str(FAKE_LOG)!r}, "w"))
sys.stderr.write("2026-11-13T00:00:00Z INF Registered tunnel connection connIndex=0 location=ord01 protocol=quic\\n")
sys.stderr.flush()
time.sleep(60)
""")
fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
os.environ["RELAY_CLOUDFLARED"] = str(fake)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"remote_access": {"enabled": True}},
             "remote_access": {"cloud_url": CLOUD}})

# The routes' tunnel calls are recorded here; the real Tunnel is tried on its own further down, in one event loop.
started = []


def _fake_start(token):
    started.append(token)


async def _fake_stop():
    started.append(None)


real_start, real_stop = tunnel.start, tunnel.stop
tunnel.start, tunnel.stop = _fake_start, _fake_stop

admin = _db.client(app, base_url="http://relay.local:8001")
viewer_user = _db.client(app, "viewer", base_url="http://relay.local:8001")
URL = "/api/admin/remote-access"

print("before enrolling")
v = admin.get(URL).json()
eq("not enrolled", v["enrolled"], False)
eq("no url", v["url"], "")
eq("cloudflared is found", v["cloudflared"], True)
eq("cloud url defaults to relay-cloud, kept when set", config.get()["remote_access"]["cloud_url"], CLOUD)

print("enroll")
r = admin.post(URL + "/enroll", json={"code": "WRONG-CODE-HERE"})
eq("a wrong code is refused", r.status_code, 400)
eq("with a fixed sentence", r.json()["error"], cloud.MESSAGES["bad_code"])
first_secret = config.get()["remote_access"]["pending_secret"]
check("the secret is saved before the first call", len(first_secret) >= 32)

cloud_state["fail_next"] = "provision_failed"
r = admin.post(URL + "/enroll", json={"code": GOOD_CODE})
eq("a cloud failure is a 502", r.status_code, 502)
eq("with its sentence", r.json()["error"], cloud.MESSAGES["provision_failed"])

eq("a viewer can't enroll", viewer_user.post(URL + "/enroll", json={"code": GOOD_CODE}).status_code, 403)

r = admin.post(URL + "/enroll", json={"code": GOOD_CODE.lower()})
eq("a good code enrolls", r.status_code, 200)
v = r.json()
eq("enrolled", v["enrolled"], True)
eq("the url", v["url"], "https://k7m2p9qa.relaychurchsystems.com")
enroll_bodies = [b for (m, p, b, _a) in seen if m == "POST"]
check("every attempt sent the same secret", {b["secret"] for b in enroll_bodies} == {first_secret})
eq("the viewer port was sent", cloud_state["port"], 8000)
eq("the version was sent", cloud_state["version"], "9.9.9")
s = config.get()["remote_access"]
check("the secret is kept", s["secret"] == first_secret)
check("the pending secret is cleared", s["pending_secret"] == "")
check("the tunnel token is kept", s["tunnel_token"] == "tok-8000")
check("the tunnel started with that token", started[-1] == "tok-8000")
eq("enrolling again is refused while enrolled", admin.post(URL + "/enroll", json={"code": GOOD_CODE}).status_code, 409)

print("secrets stay in config.json")
state_body = admin.get("/api/admin/state").text
for label, value in (("secret", first_secret), ("tunnel token", "tok-8000")):
    check(f"the {label} is not in the admin state", value not in state_body)
    check(f"the {label} is not in the view", value not in json.dumps(admin.get(URL).json()))
eq("the admin state says only enrolled and the url", admin.get("/api/admin/state").json()["config"]["remote_access"],
   {"enrolled": True, "url": "https://k7m2p9qa.relaychurchsystems.com"})

print("the viewer port never reaches it")
from fastapi.testclient import TestClient  # noqa: E402
eq("viewer port 404s the module's API", TestClient(app, base_url="http://relay.local:8000").get(URL).status_code, 404)

print("check-in")
seen.clear()
config.save({"port": 8080})
asyncio.run(service.begin())
put = [x for x in seen if x[0] == "PUT"]
eq("one check-in", len(put), 1)
check("it carries the secret", put[0][3] == f"Bearer {first_secret}")
eq("and the new viewer port", put[0][2]["viewer_port"], 8080)
check("a new token from the cloud is saved", config.get()["remote_access"]["tunnel_token"] == "tok-8080")
check("and the tunnel restarts with it", started[-1] == "tok-8080")

cloud_state["released"] = True
started.clear()
asyncio.run(service.begin())
check("a released install doesn't start the tunnel", started == [None])
eq("the view says why", admin.get(URL).json()["cloud_error"], cloud.MESSAGES["unauthorised"])
from app.modules.remote_access import MODULE  # noqa: E402
eq("health says so", MODULE.health().state, "error")
cloud_state["released"] = False

real_url = config.get()["remote_access"]["cloud_url"]
config.save({"remote_access": {**config.get()["remote_access"], "cloud_url": "http://127.0.0.1:1"}},
            replace=("remote_access",))
started.clear()
asyncio.run(service.begin())
check("the cloud being down still starts the tunnel with the saved token", started == ["tok-8080"])
config.save({"remote_access": {**config.get()["remote_access"], "cloud_url": real_url}}, replace=("remote_access",))

print("release")
r = admin.post(URL + "/release")
eq("release answers", r.status_code, 200)
eq("not enrolled after", r.json()["enrolled"], False)
eq("the cloud was told", cloud_state["released"], True)
s = config.get()["remote_access"]
check("everything secret is cleared", not any(s[k] for k in ("secret", "tunnel_token", "hostname", "install_id")))
check("the tunnel stopped", started[-1] is None)

tunnel.start, tunnel.stop = real_start, real_stop

print("the tunnel itself")


async def run_tunnel():
    t = Tunnel()
    t.start("tok-secret-value")
    for _ in range(100):
        if t.state == "connected":
            break
        await asyncio.sleep(0.05)
    eq("connected once cloudflared registers", t.state, "connected")
    got = json.loads(FAKE_LOG.read_text())
    check("the token goes in through the environment", got["token"] == "tok-secret-value")
    check("and never on the command line", "tok-secret-value" not in " ".join(got["argv"]))
    eq("cloudflared runs the tunnel without self-updating", got["argv"], ["tunnel", "--no-autoupdate", "--loglevel",
                                                                         "info", "run"])
    proc = t._proc
    await t.stop()
    eq("off after stop", t.state, "off")
    check("the process is gone", proc is not None and proc.returncode is not None)


# The fake cloudflared is a script with a #! line, which Windows can't start as a program.
if sys.platform != "win32":
    asyncio.run(run_tunnel())

t = Tunnel()
t.observe("INF Registered tunnel connection connIndex=0")
t.observe("INF Registered tunnel connection connIndex=1")
t.observe("WRN Connection terminated connIndex=0")
eq("one connection down of two is still connected", t.state, "connected")
t.observe("WRN Connection terminated connIndex=1")
eq("all down is retrying", t.state, "retrying")

os.environ["RELAY_CLOUDFLARED"] = os.path.join(TMP, "missing")
eq("a missing binary is found missing", tunmod.find_binary(), None)


async def run_missing():
    t = Tunnel()
    t.start("x")
    await asyncio.sleep(0.1)
    eq("and the tunnel says so", t.state, "missing")


asyncio.run(run_missing())

print()
print("all passed" if not fails else f"{len(fails)} FAILED: {fails}")
server.shutdown()
sys.exit(1 if fails else 0)
