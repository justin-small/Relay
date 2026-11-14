"""Planning Center connection: save, test, service types, disconnect, the client's headers
and rate-limit handling, and that the secret never leaves config.json.

Offline: a fake Planning Center on 127.0.0.1. Run: python tests/test_planningcenter.py
"""
import base64
import json
import logging
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()
os.environ["RELAY_VERSION"] = "9.9.9"

from fastapi.testclient import TestClient

from app import config
from app.modules.planning_center import planningcenter as pc
from app.main import app

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label, bool(cond), True) if cond else eq(label + f" ({extra})", False, True)


APP_ID, SECRET = "appid-1234", "s3cret-pat-value"
TYPES = [{"id": "42", "type": "ServiceType", "attributes": {"name": "Sunday Evening"}},
         {"id": "7", "type": "ServiceType", "attributes": {"name": "Sunday Morning"}},
         {"id": "8", "type": "ServiceType", "attributes": {"name": "Old Youth", "archived_at": "2020-01-01T12:00:00Z"}},
         {"id": "9", "type": "ServiceType", "attributes": {"name": "Gone", "deleted_at": "2020-01-01T12:00:00Z"}}]

seen = []            # (path, headers) of every request the fake got
state = {"limit_429": 0, "retry_after": "0", "forbid": False}   # answer this many calls with 429 first


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        seen.append((self.path, dict(self.headers)))
        want = "Basic " + base64.b64encode(f"{APP_ID}:{SECRET}".encode()).decode()
        if self.headers.get("Authorization") != want:
            # The real API answers a bad token in plain text, not JSON.
            raw = b"HTTP Basic: Access denied.\n"
            self.send_response(401)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if state["forbid"]:
            return self._send(403, {"errors": [{"code": "403", "detail": "TRASH_PANDA"}]})
        if state["limit_429"] > 0:
            state["limit_429"] -= 1
            return self._send(429, {"errors": [{"code": "429", "detail": "Rate limit exceeded"}]},
                              {"Retry-After": state["retry_after"], "X-PCO-API-Request-Rate-Count": "118",
                               "X-PCO-API-Request-Rate-Limit": "100",
                               "X-PCO-API-Request-Rate-Period": "20 seconds"})
        self._send(200, {"data": TYPES, "links": {}},
                   {"X-PCO-API-Request-Rate-Count": "3", "X-PCO-API-Request-Rate-Limit": "100",
                    "X-PCO-API-Request-Rate-Period": "20 seconds"})

    def _send(self, code, body, extra=None):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)


server = HTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
pc.BASE_URL = f"http://127.0.0.1:{server.server_port}"
slept = []
pc._sleep = lambda s: slept.append(s)

log_lines = []


class Grab(logging.Handler):
    def emit(self, record):
        log_lines.append(record.getMessage())


logging.getLogger().addHandler(Grab())
logging.getLogger().setLevel(logging.DEBUG)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"planning_center": {"enabled": True}}})  # the module is switched on (#157)
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")  # its hooks into core, without its background work
admin = _db.client(app, base_url="http://relay.local:8001")
URL = "/api/admin/planning-center"

print("off until a token is saved")
st = admin.get("/api/admin/state").json()["config"]["planning_center"]
eq("state says not connected", st, {"connected": False, "service_types": [], "show_notes": False, "rundown_screen": False})
eq("testing with no token is refused", admin.post(URL + "/test").status_code, 400)
eq("no request reached Planning Center", seen, [])
eq("saving only an ID is refused", admin.post(URL, json={"app_id": APP_ID}).status_code, 400)

print("good token")
r = admin.post(URL, json={"app_id": APP_ID, "secret": SECRET})
eq("save", (r.status_code, r.json()), (200, {"planning_center": {"connected": True, "service_types": [], "show_notes": False, "rundown_screen": False}}))
r = admin.post(URL + "/test")
eq("test is ok", r.status_code, 200)
eq("service types listed by name, archived and deleted left out", [t["name"] for t in r.json()["service_types"]],
   ["Sunday Evening", "Sunday Morning"])
path, hdrs = seen[-1]
hdrs = {k.lower(): v for k, v in hdrs.items()}
check("calls the service_types endpoint", path.startswith("/services/v2/service_types"), path)
eq("API version header", hdrs.get("x-pco-api-version"), "2018-11-01")
eq("User-Agent names Relay and its version", hdrs.get("user-agent"), "Relay/9.9.9 (church caption relay)")

print("choose service types")
r = admin.post(URL + "/service-types", json={"ids": ["7", "7", "x", "42"]})
eq("only numeric ids kept, once each", r.json()["planning_center"]["service_types"], ["7", "42"])
eq("a non-list is refused", admin.post(URL + "/service-types", json={"ids": "7"}).status_code, 400)
admin.post(URL, json={"app_id": "", "secret": ""})
eq("blank fields keep the token and the choice",
   admin.get("/api/admin/state").json()["config"]["planning_center"],
   {"connected": True, "service_types": ["7", "42"], "show_notes": False, "rundown_screen": False})
admin.post(URL, json={"app_id": APP_ID, "secret": SECRET + "x"})
eq("a different token resets the choice",
   admin.get("/api/admin/state").json()["config"]["planning_center"]["service_types"], [])

print("bad token")
r = admin.post(URL + "/test")
eq("rejected with a sentence", (r.status_code, "did not accept" in r.json()["error"]), (400, True))
check("the secret is not in that sentence", SECRET not in r.text)
r = admin.post(URL + "/service-types/refresh")
eq("the page's refresh says the same", (r.status_code, "did not accept" in r.json()["error"]), (400, True))

print("valid token without Services access")
admin.post(URL, json={"app_id": APP_ID, "secret": SECRET})
state["forbid"] = True
r = admin.post(URL + "/test")
eq("403 is told apart from a bad token", (r.status_code, "does not have access" in r.json()["error"]), (400, True))
state["forbid"] = False

print("rate limit")
admin.post(URL, json={"app_id": APP_ID, "secret": SECRET})
state.update(limit_429=2, retry_after="7")
slept.clear()
r = admin.post(URL + "/test")
eq("two 429s then success", r.status_code, 200)
eq("waited the Retry-After each time", slept, [7.0, 7.0])
state.update(limit_429=99, retry_after="3")
slept.clear()
r = admin.post(URL + "/test")
eq("persistent 429 gives up with 429", r.status_code, 429)
eq("…after ATTEMPTS tries, waiting between", slept, [3.0, 3.0])
state["limit_429"] = 0
client = pc.Client(APP_ID, SECRET, base_url=pc.BASE_URL)
client.service_types()
eq("rate headers are read, not assumed ('20 seconds' parses)", client.rate, {"count": 3, "limit": 100, "period": 20})
client.rate.update(count=100)
slept.clear()
client.service_types()
eq("at the ceiling, waits out the period first", slept, [20.0])
check("Retry-After is clamped", pc._retry_after({"Retry-After": "99999"}, None) == pc.MAX_WAIT_S)
check("a missing Retry-After falls back to the period", pc._retry_after({}, 20) == 20)

print("roles")
admin.post(URL, json={"app_id": APP_ID, "secret": SECRET})
admin.post(URL + "/service-types", json={"ids": ["7"]})
operator = _db.client(app, "operator", base_url="http://relay.local:8001")
viewer_user = _db.client(app, "viewer", base_url="http://relay.local:8001")
state.update(limit_429=0, forbid=False)
eq("an operator picks today's plan", operator.post(URL + "/plan", json={"service_type": "7", "plan": ""}).status_code, 200)
eq("an operator reads the live view", operator.get(URL + "/live").status_code, 200)
eq("an operator may not change the setup",
   [operator.post(URL + p, json=b).status_code for p, b in (("", {"app_id": "x", "secret": "y"}), ("/test", {}),
    ("/service-types/refresh", {}), ("/service-types", {"ids": []}), ("/notes", {"show": True}))], [403] * 5)
eq("…nor disconnect", operator.delete(URL).status_code, 403)
eq("a viewer reads the live view", viewer_user.get(URL + "/live").status_code, 200)
eq("a viewer may not pick a plan", viewer_user.post(URL + "/plan", json={"service_type": "7", "plan": ""}).status_code, 403)
eq("…nor change the setup", [viewer_user.post(URL, json={}).status_code, viewer_user.delete(URL).status_code], [403, 403])
check("a viewer's read of the live view leaks no secret", SECRET not in viewer_user.get(URL + "/live").text
      and APP_ID not in viewer_user.get(URL + "/live").text)
eq("the saved followed types were left alone by the refusals",
   admin.get("/api/admin/state").json()["config"]["planning_center"]["service_types"], ["7"])

print("secret stays server-side")
body = json.dumps(admin.get("/api/admin/state").json())
check("not in /api/admin/state", SECRET not in body and APP_ID not in body)
check("not in any save/test response", SECRET not in admin.post(URL, json={"app_id": APP_ID, "secret": SECRET}).text)
check("never in a log line", not any(SECRET in l or APP_ID in l for l in log_lines))
check("it is in config.json, which is 0600", SECRET in config.CONFIG_PATH.read_text()
      and (os.name != "posix" or oct(config.CONFIG_PATH.stat().st_mode & 0o777) == "0o600"))  # Windows has no modes
check("not reachable on the viewer port",
      TestClient(app, base_url="http://relay.local:8000").get("/api/admin/state").status_code in (403, 404))

print("network failure")
dead = pc.Client(APP_ID, SECRET, base_url="http://127.0.0.1:1")
try:
    dead.service_types()
    check("unreachable raises", False)
except pc.PlanningCenterError as exc:
    eq("kind is network", exc.kind, "network")
    check("…and the message holds no secret", SECRET not in str(exc))

print("disconnect")
r = admin.delete(URL)
eq("disconnected", r.json()["planning_center"], {"connected": False, "service_types": [], "show_notes": False, "rundown_screen": False})
check("secret gone from config.json", SECRET not in config.CONFIG_PATH.read_text())
before = len(seen)
eq("test is refused again", admin.post(URL + "/test").status_code, 400)
eq("and nothing reached Planning Center", len(seen), before)

print()
print("the admin page gets fixed sentences, never an error's own text")
leaky = pc.PlanningCenterError("network", "Could not reach Planning Center: [Errno 61] 10.1.2.3 refused")
eq("network: a fixed sentence", pc.operator_message(leaky), "Could not reach Planning Center. Check this machine's internet connection.")
body = pc.PlanningCenterError("http", "Planning Center answered HTTP 422: {\"detail\": \"internal stuff\"}", status=422)
eq("http: only the status", pc.operator_message(body), "Planning Center refused the request (HTTP 422).")
eq("403 is told apart", "does not have access" in pc.operator_message(pc.PlanningCenterError("auth", "x", status=403)), True)

if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails))
    sys.exit(1)
print("all checks passed")
