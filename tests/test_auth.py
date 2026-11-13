"""The web layer of the user system, end to end: first-run setup, sign-in, PIN sign-in on trusted devices,
forced password change, every /api/admin route against every role, the users API and its rank rules.

Runs against a real throwaway SQLite file (tests/_db.py). Offline: no OpenAI, no hardware.
Run: .venv/bin/python tests/test_auth.py
"""
import asyncio
import concurrent.futures
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"   # the lifespan test starts the app; never multicast from a test
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, users  # noqa: E402
from app import main as M  # noqa: E402
from app import modules as modmod  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import Manifest, Module, modules as registry  # noqa: E402

users.SCRYPT_N = 2 ** 10
M.LOGIN_FAIL_DELAY_S = 0.0                      # the lockout is what is tested, not the wait

ADMIN = "http://relay.local:8001"
VIEWER = "http://relay.local:8000"
PW = "correct horse"
SETUP_TOKEN = "setup-token-1"
DB_DOWN = M.DB_DOWN_SENTENCE
MSG = users.MESSAGES

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        # The values are not printed: this file compares passwords, PINs and tokens, and a CI log is public.
        print("         got and want differ (values withheld; re-run locally with a print to see them)")
        fails.append(label)


def anon(base=ADMIN, **kw):
    return TestClient(app, base_url=base, **kw)


def detail(r):
    try:
        return r.json().get("detail")
    except Exception:
        return None


def cookie_attrs(r, name):
    """The attributes of the Set-Cookie for `name`, lower-cased."""
    for h in r.headers.get_list("set-cookie"):
        if h.lower().startswith(name.lower() + "="):
            return h.lower()
    return ""


def max_age(attrs):
    m = re.search(r"max-age=(\d+)", attrs)
    return int(m.group(1)) if m else None


def login(c, username, password=PW, **kw):
    return c.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False, **kw)


def fresh():
    M._LOGIN_FAILS.clear()


config.ensure_file()
# The blocklist file is written by the config tests below: keep it in the temp dir, not the repo's.
config.save({"admin_token": SETUP_TOKEN, "port": 8000, "admin_port": 8001,
             "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt")})

# ------------------------------------------------------------------ the database is down
print("database unavailable")
good_url = os.environ["RELAY_DATABASE"]
broken = Path(os.environ["RELAY_CONFIG"]).parent / "broken.db"
broken.write_bytes(b"this is not a database file " * 100)        # a corrupt file: it cannot be opened
os.environ["RELAY_DATABASE"] = str(broken)
db.close()
M._db_ready = False
M.forget_users_cache()
c = anon()
r = c.get("/admin")
eq("the admin page is a 503", r.status_code, 503)
check("…with the fixed sentence", DB_DOWN in r.text.replace("&#39;", "'"), r.text[:300])
r = c.get("/api/admin/me")
eq("the API says 503 and the sentence", (r.status_code, detail(r)), (503, DB_DOWN))
eq("signing in is a 503 too", login(c, "owner").status_code, 503)
eq("first-run setup is a 503 too", c.post("/admin/setup", data={"token": SETUP_TOKEN}).status_code, 503)
eq("logging out still clears the cookie", c.post("/admin/logout", follow_redirects=False).status_code, 303)
eq("the viewer page still answers", anon(VIEWER).get("/").status_code, 200)
eq("the caption page still answers", anon(VIEWER).get("/translation").status_code, 200)
eq("healthz still answers", anon(VIEWER).get("/healthz").status_code, 200)
eq("a panel check-in still answers", anon(VIEWER).get("/api/targets").status_code, 200)
db.close()
M._db_ready = False
os.environ["RELAY_DATABASE"] = str(Path(os.environ["RELAY_CONFIG"]).parent / "afile" / "x" / "relay.db")
(Path(os.environ["RELAY_CONFIG"]).parent / "afile").write_text("a file where a folder should be")
eq("a file that cannot be created is the same 503", anon().get("/admin").status_code, 503)
os.environ["RELAY_DATABASE"] = good_url
db.close()
M._db_ready = False
eq("and it recovers when the database comes back", anon().get("/admin").status_code, 200)

# ------------------------------------------------------------------ first run
print("first run")
_db.reset()
fresh()
c = anon()
r = c.get("/admin")
check("no users: the first-run form", 'action="/admin/setup"' in r.text and "Set up Relay" in r.text)
for field in ("token", "api_key", "username", "name", "password", "confirm"):
    check(f"…has a {field} field", f'name="{field}"' in r.text)
check("…and every input has a label", all(f'for="{f}"' in r.text for f in ("token", "username", "name", "password", "confirm")))

# While no account exists, the token header is the Owner (first-run automation).
H = {"x-admin-token": SETUP_TOKEN}
eq("x-admin-token works as Owner before any user", c.get("/api/admin/state", headers=H).status_code, 200)
me = c.get("/api/admin/me", headers=H).json()
eq("…and is an owner", me["user"]["role"], "owner")
eq("a wrong x-admin-token is refused", c.get("/api/admin/state", headers={"x-admin-token": "nope"}).status_code, 401)
eq("no header is refused", c.get("/api/admin/state").status_code, 401)
eq("the setup token cannot make a trusted device (it has no account)",
   c.post("/api/admin/trusted-devices", headers=H, json={"name": "x"}).status_code, 403)

form = {"username": "boss", "name": "The Boss", "password": "first password", "confirm": "first password"}
codes = [c.post("/admin/setup", data={**form, "token": "wrong"}).status_code for _ in range(5)]
eq("a wrong token is refused, five times", codes, [401] * 5)
r = c.post("/admin/setup", data={**form, "token": SETUP_TOKEN})
eq("…and then the right token is locked out too (throttled)", r.status_code, 429)
eq("…no user was made", users.count(), 0)
fresh()
r = c.post("/admin/setup", data={**form, "token": "wrong"})
check("a wrong token says so in a fixed sentence", M.WEB_MESSAGES["bad_token"] in r.text)
check("…and keeps the username but not a password", 'value="boss"' in r.text and "first password" not in r.text)
r = c.post("/admin/setup", data={**form, "token": SETUP_TOKEN, "confirm": "different"})
eq("passwords that differ are refused", r.status_code, 400)
check("…with a fixed sentence", M.WEB_MESSAGES["mismatch"] in r.text)
r = c.post("/admin/setup", data={**form, "token": SETUP_TOKEN, "username": "!"})
eq("a bad username is refused", r.status_code, 400)
check("…with users.py's sentence", MSG["bad_username"].split(":")[0] in r.text)
eq("none of that made a user", users.count(), 0)
config.save({"admin_token": ""})
r = c.post("/admin/setup", data={**form, "token": ""})
eq("with no admin token, setup from another machine is refused", r.status_code, 403)
check("…with a fixed sentence and no form", M.WEB_MESSAGES["local_only"] in r.text and 'name="password"' not in r.text)
check("…and the page itself says to open it on the computer Relay runs on",
      "on the computer Relay runs on" in c.get("/admin").text and 'name="password"' not in c.get("/admin").text)
eq("…and no user was made", users.count(), 0)
local = anon(client=("127.0.0.1", 50001))
check("from this machine the form has no token field but has an API key field",
      'name="token"' not in local.get("/admin").text and 'name="api_key"' in local.get("/admin").text)
eq("a proxied LAN client (loopback peer, forwarded for a LAN address) is refused too",
   local.post("/admin/setup", data=form, headers={"x-forwarded-for": "192.168.1.9"}).status_code, 403)
eq("…while a proxied client that really is local is accepted",
   local.post("/admin/setup", data={**form, "username": "localboss", "api_key": "sk-test-1234"},
              headers={"x-forwarded-for": "127.0.0.1"}, follow_redirects=False).status_code, 303)
check("…and the API key it carried was stored", config.get()["openai_api_key"] == "sk-test-1234")
_db.reset()
config.save({"openai_api_key": ""})
fresh()
eq("an empty x-admin-token is not a way in", c.get("/api/admin/state", headers={"x-admin-token": ""}).status_code, 401)
config.save({"admin_token": SETUP_TOKEN})
fresh()

r = c.post("/admin/setup", data={**form, "token": SETUP_TOKEN}, follow_redirects=False)
eq("the right token creates the Owner and signs in", (r.status_code, r.headers.get("location")), (303, "/admin"))
check("…with a session cookie", bool(c.cookies.get("relay_admin")))
check("…and the shared token is retired: it did its one job", not config.get().get("admin_token"))
boss = users.get_by_username("boss")
eq("…who is an owner without a pending password change", (boss.role, boss.must_change_password), ("owner", False))
check("the admin page opens", '<h1 class="brand">' in c.get("/admin").text)
eq("…and the API", c.get("/api/admin/state").status_code, 200)

c2 = anon()
r = c2.post("/admin/setup", data={**form, "token": SETUP_TOKEN, "username": "second"})
eq("a second setup is refused", r.status_code, 403)
check("…with a fixed sentence and the sign-in form", M.WEB_MESSAGES["already_set_up"] in r.text and 'action="/admin/login"' in r.text)
eq("…and made no user", users.count(), 1)
eq("the token is no longer accepted as x-admin-token", c2.get("/api/admin/state", headers=H).status_code, 401)
eq("…nor on the sign-in form", c2.post("/admin/login", data={"token": SETUP_TOKEN}).status_code, 401)
eq("…nor as a password", login(c2, "boss", SETUP_TOKEN).status_code, 401)
r = c2.get("/admin")
check("signed out with users: the sign-in form, no token field",
      'action="/admin/login"' in r.text and 'name="token"' not in r.text and 'name="username"' in r.text)
check("…with autocomplete hints", 'autocomplete="username"' in r.text and 'autocomplete="current-password"' in r.text)

# Headless install: a one-time setup code replaces "open this on the machine".
print("setup code")
from app import setupcode  # noqa: E402
_db.reset()
config.save({"admin_token": ""})
fresh()
os.environ.pop("RELAY_SETUP_CODE", None)
eq("off by default: nothing is made", setupcode.prepare(False), "")
os.environ["RELAY_SETUP_CODE"] = "1"
code = setupcode.prepare(False)
check("a code is made: three groups of four, no look-alike characters",
      bool(re.fullmatch(r"[A-Z2-9]{4}(-[A-Z2-9]{4}){2}", code)) and not set("01OIL") & set(code))
eq("a restart keeps the same code (journal and installer agree)", setupcode.prepare(False), code)
check("it is stored owner-only in the state folder",
      (setupcode._file().stat().st_mode & 0o077) == 0 or os.name == "nt")
r = c.get("/admin")
check("another device sees a code field, not the 'open this on the computer' page",
      'name="token"' in r.text and "Setup code" in r.text and 'name="password"' in r.text and "/admin/import" not in r.text)
eq("without the code, setup from the LAN is refused", c.post("/admin/setup", data={**form, "token": ""}).status_code, 401)
r = c.post("/admin/setup", data={**form, "token": "AAAA-AAAA-AAAA"})
eq("a wrong code is refused", r.status_code, 401)
check("…with a fixed sentence", M.WEB_MESSAGES["bad_code"] in r.text)
eq("…and no user was made", users.count(), 0)
fresh()
codes = [c.post("/admin/setup", data={**form, "token": "wrong"}).status_code for _ in range(5)]
eq("guessing is throttled like the admin token", c.post("/admin/setup", data={**form, "token": code}).status_code, 429)
fresh()
r = c.post("/admin/setup", data={**form, "token": code.lower().replace("-", " ")}, follow_redirects=False)
eq("the right code (any case, dashes or spaces) creates the Owner from another device",
   (r.status_code, r.headers.get("location")), (303, "/admin"))
check("…and the code is spent: the file is gone", not setupcode._file().exists() and setupcode.active() == "")
eq("…and it is never accepted again", anon().post("/admin/setup", data={**form, "token": code}).status_code, 403)
eq("with accounts, start-up makes no new code", setupcode.prepare(True), "")
os.environ.pop("RELAY_SETUP_CODE", None)
_db.reset()
config.save({"admin_token": SETUP_TOKEN})
fresh()

# Two valid first-run posts at once: one Owner.
_db.reset()
config.save({"admin_token": SETUP_TOKEN})
fresh()
barrier_form = {**form, "token": SETUP_TOKEN}


def setup_once(i):
    return anon().post("/admin/setup", data={**barrier_form, "username": f"racer{i}"}, follow_redirects=False).status_code


with concurrent.futures.ThreadPoolExecutor(6) as pool:
    got = sorted(pool.map(setup_once, range(6)))
eq("six simultaneous valid setups: one wins, the rest are refused", got, [303] + [403] * 5)
eq("…and there is one Owner", users.count(), 1)

# ------------------------------------------------------------------ sign-in, cookies, lockout
print("sign-in")
_db.reset()
fresh()
owner = _db.owner("owner", PW)
M.forget_users_cache()
c = anon()
t0 = time.monotonic()
M.LOGIN_FAIL_DELAY_S = 0.3
r = login(c, "owner", "wrong password")
took = time.monotonic() - t0
M.LOGIN_FAIL_DELAY_S = 0.0
eq("a wrong password is a 401", r.status_code, 401)
check("…with the fixed sentence", MSG["bad_login"] in r.text)
check("…after the failure delay", took >= 0.28, took)
check("…and the password is never echoed", "wrong password" not in r.text)
check("…the username is", 'value="owner"' in r.text)
r2 = login(c, "nobody-here", "wrong password")
eq("an unknown user looks the same", (r2.status_code, MSG["bad_login"] in r2.text), (401, True))
eq("an empty form is a 401, not a 422", c.post("/admin/login", data={}).status_code, 401)

r = login(c, "OWNER ")
eq("the username is not case or space sensitive", r.status_code, 303)
attrs = cookie_attrs(r, "relay_admin")
check("the session cookie is HttpOnly", "httponly" in attrs, attrs)
check("…SameSite=Strict", "samesite=strict" in attrs, attrs)
check("…path /", "path=/" in attrs, attrs)
check("…not Secure over plain http", "secure" not in attrs, attrs)
eq("…and lasts 30 days", max_age(attrs), 30 * 24 * 3600)
tok = c.cookies.get("relay_admin")
check("the cookie is not the password", tok and PW not in tok)
rows = db.query("select token_hash from sessions")
check("only a hash of it is stored", rows and all(row["token_hash"] != tok and tok not in row["token_hash"] for row in rows))

loop = dict(client=("127.0.0.1", 50000))
r = login(anon(**loop), "owner", headers={"x-forwarded-proto": "https"})
check("behind the TLS front end the cookie is Secure", "secure" in cookie_attrs(r, "relay_admin"))
r = login(anon(), "owner", headers={"x-forwarded-proto": "https"})
check("a LAN client's X-Forwarded-Proto is not believed", "secure" not in cookie_attrs(r, "relay_admin"))
r = login(anon(), "owner", headers={"x-forwarded-for": "203.0.113.9"})
check("a LAN client's X-Forwarded-For is not counted", "203.0.113.9" not in M._LOGIN_FAILS)
fresh()
r = login(anon(**loop), "owner", "bad bad bad", headers={"x-forwarded-for": "203.0.113.9"})
check("behind the front end the failure is counted against the forwarded address",
      "203.0.113.9" in M._LOGIN_FAILS, M._LOGIN_FAILS)
fresh()

codes = [login(c, "owner", "bad bad bad").status_code for _ in range(5)]
eq("five failures are 401s", codes, [401] * 5)
r = login(c, "owner")
eq("then even the right password is locked out", r.status_code, 429)
check("…with a fixed sentence", "Too many attempts" in r.text)
fresh()
eq("after the lockout window it works again", login(c, "owner").status_code, 303)
eq("a success clears the count", M._LOGIN_FAILS, {})

users.create("sleeper", "Sleeper", "operator", PW, temporary=False)
sleeper = users.get_by_username("sleeper")
users.update(sleeper.id, disabled=True, actor=None)
r = login(anon(), "sleeper")
eq("a disabled user is refused with their sentence", (r.status_code, MSG["disabled"] in r.text), (403, True))
eq("…and a wrong password for them is just bad_login", (login(anon(), "sleeper", "nope nope nope").status_code), 401)
fresh()

# ------------------------------------------------------------------ logout
print("logout")
c = _db.client(app)
old = c.cookies.get("relay_admin")
eq("signed in", c.get("/api/admin/me").status_code, 200)
r = c.post("/admin/logout", follow_redirects=False)
eq("logout redirects", (r.status_code, r.headers["location"]), (303, "/admin"))
check("…and clears the cookie", "relay_admin=" in r.headers.get("set-cookie", "") and "max-age=0" in r.headers["set-cookie"].lower())
check("…the session is gone from the database", users.session_user(old) is None)
c.cookies.set("relay_admin", old)
eq("a copy of the old cookie is dead", c.get("/api/admin/me").status_code, 401)
eq("logging out signed out is harmless", anon().post("/admin/logout", follow_redirects=False).status_code, 303)

# ------------------------------------------------------------------ the roles, signed in
print("roles")
ROLE_NAMES = ("member", "viewer", "operator", "admin", "owner")
RANK = {r: i + 1 for i, r in enumerate(ROLE_NAMES)}
clients = {role: _db.client(app, role) for role in ROLE_NAMES}
owner_c, admin_c, op_c, view_c = (clients[r] for r in ("owner", "admin", "operator", "viewer"))

# The catalogue, written out here independently of users.py.
MIN_ROLE = {
    "admin.open": "viewer", "alerts.send": "member",
    "session.view": "viewer", "session.control": "operator", "audio.manage": "admin", "targets.manage": "operator",
    "panels.view": "viewer", "panels.manage": "admin", "alerts.view": "viewer", "alerts.manage": "admin",
    "overlays.view": "viewer", "overlays.manage": "operator", "recordings.view": "operator",
    "recordings.manage": "admin", "schedules.view": "viewer", "schedules.manage": "admin",
    "blocklist.manage": "admin", "settings.view": "viewer", "settings.manage": "admin",
    "credentials.manage": "owner", "modules.view": "viewer", "modules.manage": "admin", "users.view": "admin",
    "users.manage": "admin",
    "planning_center.view": "viewer", "planning_center.control": "operator", "planning_center.manage": "admin",
    "propresenter.view": "viewer", "propresenter.control": "operator", "propresenter.manage": "admin",
    "remote_access.view": "viewer", "remote_access.manage": "admin",
}
for role in ROLE_NAMES:
    me = clients[role].get("/api/admin/me").json()
    eq(f"{role}: /me has the contract's shape", sorted(me), ["device", "permissions", "pin_set", "user"])
    eq(f"{role}: user fields", sorted(me["user"]), ["id", "must_change_password", "name", "role", "username"])
    eq(f"{role}: device", me["device"], {"trusted": False, "name": None})
    want = sorted(p for p, mr in MIN_ROLE.items() if RANK[role] >= RANK[mr])
    eq(f"{role}: permissions are exactly the catalogue's", me["permissions"], want)
    r = clients[role].get("/admin", follow_redirects=False)
    if role == "member":
        check("member: /admin sends it to its home page", r.status_code == 303 and r.headers["location"] == "/home",
              r.status_code)
    else:
        check(f"{role}: the admin page opens", r.status_code == 200 and '<h1 class="brand">' in r.text, r.status_code)
check("every permission registered is in the independent table",
      {p.id for p in users.catalogue()} == set(MIN_ROLE), {p.id for p in users.catalogue()} ^ set(MIN_ROLE))

# ------------------------------------------------------------------ every /api/admin route, every role
print("every /api/admin route")
config.save({"modules": {"propresenter": {"enabled": True}, "planning_center": {"enabled": True},
                         "remote_access": {"enabled": True}}})

E = {}      # (METHOD, path) -> permission; None = any signed-in user


def route(method, path, perm):
    E[(method, path)] = perm


# the signed-in user's own
route("GET", "/api/admin/me", None)
route("POST", "/api/admin/me/password", None)
route("POST", "/api/admin/me/pin", None)
route("DELETE", "/api/admin/me/pin", None)
# session and settings
route("GET", "/api/admin/state", "settings.view")
route("GET", "/api/admin/urls", "settings.view")
route("GET", "/api/admin/status", "session.view")
route("GET", "/api/admin/status/stream", "session.view")
route("GET", "/api/admin/devices", "session.view")
route("POST", "/api/admin/config", "settings.view")        # then per field, below
route("POST", "/api/admin/target/{target}", "targets.manage")
route("POST", "/api/admin/start", "session.control")
route("POST", "/api/admin/stop", "session.control")
# modules
route("GET", "/api/admin/modules", "modules.view")
route("POST", "/api/admin/modules/{mid}", "modules.manage")
route("POST", "/api/admin/modules/{mid}/test", "modules.manage")
# alerts
route("GET", "/api/admin/alerts", "alerts.view")
route("POST", "/api/admin/alerts", "alerts.manage")
# panels
route("GET", "/api/admin/panels", "panels.view")
route("GET", "/api/admin/panels/firmware", "panels.view")
route("POST", "/api/admin/panels/firmware", "panels.manage")
route("DELETE", "/api/admin/panels/firmware", "panels.manage")
route("POST", "/api/admin/panels/firmware/update", "panels.manage")
route("GET", "/api/admin/panels/flash", "panels.manage")
route("POST", "/api/admin/panels/flash", "panels.manage")
route("DELETE", "/api/admin/panels/flash", "panels.manage")
route("GET", "/api/admin/panels/flash/{name}", "panels.manage")
route("POST", "/api/admin/panels/{panel_id}", "panels.manage")
route("POST", "/api/admin/panels/{panel_id}/reboot", "panels.manage")
route("DELETE", "/api/admin/panels/{panel_id}", "panels.manage")
# schedules
route("GET", "/api/admin/schedules", "schedules.view")
route("POST", "/api/admin/schedules", "schedules.manage")
route("PUT", "/api/admin/schedules/{sid}", "schedules.manage")
route("DELETE", "/api/admin/schedules/{sid}", "schedules.manage")
# overlays
route("GET", "/api/admin/overlays", "overlays.view")
route("POST", "/api/admin/overlays", "overlays.manage")
route("PUT", "/api/admin/overlays/{oid}", "overlays.manage")
route("DELETE", "/api/admin/overlays/{oid}", "overlays.manage")
# recordings
route("GET", "/api/admin/recordings", "recordings.view")
route("GET", "/api/admin/recordings/{run_id}", "recordings.view")
route("GET", "/api/admin/recordings/{run_id}/export.zip", "recordings.view")
route("GET", "/api/admin/recordings/{run_id}/file/{name}", "recordings.view")
route("DELETE", "/api/admin/recordings/{run_id}", "recordings.manage")
# users and trusted devices
route("GET", "/api/admin/users", "users.view")
route("POST", "/api/admin/users", "users.manage")
route("PUT", "/api/admin/users/{user_id}", "users.manage")
route("POST", "/api/admin/users/{user_id}/password", "users.manage")
route("POST", "/api/admin/users/{user_id}/signout", "users.manage")
route("DELETE", "/api/admin/users/{user_id}", "users.manage")
route("GET", "/api/admin/trusted-devices", "users.view")
route("POST", "/api/admin/trusted-devices", "users.manage")
route("DELETE", "/api/admin/trusted-devices/{device_id}", "users.manage")

# Module routes: one explicit entry for each, with the permission its module declares. A module route added
# without an entry here fails "EVERY /api/admin route has a permission entry".
PCX, PPX, RAX = "/api/admin/planning-center", "/api/admin/propresenter", "/api/admin/remote-access"
for method, path, perm in (
    ("GET", RAX, "remote_access.view"),
    ("POST", RAX + "/enroll", "remote_access.manage"),                   # an empty body never reaches the cloud
    ("POST", RAX + "/release", "remote_access.manage"),                  # not enrolled: nothing to tell the cloud
    ("POST", PCX, "planning_center.manage"),
    ("DELETE", PCX, "planning_center.manage"),
    ("POST", PCX + "/test", "planning_center.manage"),
    ("POST", PCX + "/service-types/refresh", "planning_center.manage"),   # reaches out to Planning Center
    ("POST", PCX + "/service-types", "planning_center.manage"),
    ("POST", PCX + "/notes", "planning_center.manage"),
    ("GET", PCX + "/live", "planning_center.view"),
    ("POST", PCX + "/plan", "planning_center.control"),                 # an operator's call during a service
    ("GET", PPX, "propresenter.view"),
    ("POST", PPX, "propresenter.manage"),
    ("POST", PPX + "/test", "propresenter.manage"),
    ("POST", PPX + "/discover", "propresenter.manage"),
    ("POST", PPX + "/stage", "propresenter.control"),
    ("DELETE", PPX + "/stage", "propresenter.control"),
    ("GET", PPX + "/slides", "propresenter.view"),
    ("GET", PPX + "/thumbnail/{uuid}/{index}", "propresenter.view"),
    ("POST", PPX + "/step", "propresenter.control"),
    ("POST", PPX + "/next-presentation", "propresenter.control"),
    ("POST", PPX + "/item", "propresenter.control"),
    ("POST", PPX + "/screens", "propresenter.control"),
    ("POST", PPX + "/slide", "propresenter.control"),
    ("POST", PPX + "/clear", "propresenter.control"),
    ("GET", PPX + "/playlists", "propresenter.view"),
    ("GET", PPX + "/playlists/{playlist}", "propresenter.view"),
    ("GET", PPX + "/playlists/{playlist}/diagnostics", "propresenter.view"),
    ("GET", PPX + "/playlists/{playlist}/items/{index}/cues", "propresenter.view"),
    ("GET", PPX + "/playlists/{playlist}/thumbnail/{index}/{cue}", "propresenter.view"),
    ("POST", PPX + "/playlists/{playlist}/trigger", "propresenter.control"),
    ("GET", PPX + "/macros", "propresenter.view"),                      # ?all=true needs .manage, checked below
    ("POST", PPX + "/macros/{macro}/trigger", "propresenter.control"),
    ("GET", PPX + "/looks", "propresenter.view"),
    ("POST", PPX + "/looks/{look}/trigger", "propresenter.control"),
    ("GET", PPX + "/timers", "propresenter.view"),
    ("POST", PPX + "/timers/{timer}/{op}", "propresenter.control"),
    ("GET", PPX + "/messages", "propresenter.view"),
    ("POST", PPX + "/messages/{message}/trigger", "propresenter.control"),
    ("POST", PPX + "/messages/{message}/clear", "propresenter.control"),
    ("POST", PPX + "/transport", "propresenter.control"),
    ("GET", PPX + "/lobby", "propresenter.view"),
    ("POST", PPX + "/lobby/start", "propresenter.control"),
    ("POST", PPX + "/lobby/resume", "propresenter.control"),
    ("POST", PPX + "/lobby/stop", "propresenter.control"),
    ("GET", PPX + "/live", "propresenter.view"),
    ("GET", PPX + "/live/image/{which}", "propresenter.view"),
    ("GET", PPX + "/link", "propresenter.view"),
    ("GET", PPX + "/link/presentations", "propresenter.view"),
    ("POST", PPX + "/link/refresh", "propresenter.manage"),             # reaches out to ProPresenter
    ("POST", PPX + "/link/mapping", "propresenter.manage"),
    ("POST", PPX + "/link/playlist", "propresenter.manage"),
):
    route(method, path, perm)


def expected_for(method, path):
    if (method, path) in E:
        return True, E[(method, path)]
    return False, None


def all_routes(routes):
    """Every route, including those of an included router (newer FastAPI keeps those nested)."""
    for rt in routes:
        if isinstance(rt, APIRoute):
            yield rt
        elif hasattr(rt, "original_router"):
            yield from all_routes(rt.original_router.routes)


found = []
for rt in all_routes(app.routes):
    if not (rt.path == "/api/admin" or rt.path.startswith("/api/admin/")):
        continue
    for method in sorted(rt.methods - {"HEAD", "OPTIONS"}):
        found.append((method, rt.path, rt))
check("the walk found the admin routes", len(found) >= 90, len(found))
missing = [f"{m} {p}" for m, p, _ in found if not expected_for(m, p)[0]]
check("EVERY /api/admin route has a permission entry in this test (add one with the route)", not missing, missing)
core_paths = {(m, p) for m, p, _ in found}
stale = [f"{m} {p}" for (m, p) in E if (m, p) not in core_paths]
check("…and every entry here is a route that exists", not stale, stale)

# The permission each route's dependency really asks for is the one in the table.
mismatch = []
for m, p, rt in found:
    ok, want = expected_for(m, p)
    asks = [d.call.permission for d in rt.dependant.dependencies if hasattr(d.call, "permission")]
    if ok and asks != [want]:
        mismatch.append((m, p, asks, want))
check("each route's dependency asks for the permission in the table", not mismatch, mismatch[:5])

# GETs that are safe to run for real (no hardware, no side effects), to see "allowed" really allowed.
SAFE_GET = {"/api/admin/me", "/api/admin/state", "/api/admin/status", "/api/admin/modules", "/api/admin/alerts",
            "/api/admin/panels", "/api/admin/panels/firmware", "/api/admin/schedules", "/api/admin/overlays",
            "/api/admin/recordings", "/api/admin/users", "/api/admin/trusted-devices",
            "/api/admin/planning-center/live", "/api/admin/propresenter", "/api/admin/propresenter/lobby",
            "/api/admin/propresenter/live", "/api/admin/propresenter/link"}


def concrete(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def send(client, method, path):
    if method == "GET":
        return client.get(path)
    return client.request(method, path, json={})


anon_c = anon()
bad = []
for m, p, _ in found:
    r = send(anon_c, m, concrete(p))
    if r.status_code != 401 or detail(r) != "Sign in required":
        bad.append((m, p, r.status_code))
check("signed out: every admin route is 401 'Sign in required'", not bad, bad[:5])

for role in ROLE_NAMES:
    cl = clients[role]
    wrong_denied, wrong_allowed = [], []
    for m, p, _ in found:
        _, perm = expected_for(m, p)
        if perm is not None and RANK[role] < RANK[MIN_ROLE[perm]]:
            r = send(cl, m, concrete(p))
            if r.status_code != 403 or detail(r) != "Not allowed":
                wrong_denied.append((m, p, r.status_code))
        elif m == "GET" and p in SAFE_GET:
            r = send(cl, m, concrete(p))
            if r.status_code in (401, 403):
                wrong_allowed.append((m, p, r.status_code))
    check(f"{role}: every route above the role answers 403 'Not allowed'", not wrong_denied, wrong_denied[:5])
    check(f"{role}: the safe routes the role may use answer", not wrong_allowed, wrong_allowed[:5])

# Which module routes exist at all, and what each role may do with them.
modroutes = [(m, p) for m, p, _ in found if p.startswith(("/api/admin/propresenter", "/api/admin/planning-center"))]
check("module admin routes were walked", len(modroutes) > 40, len(modroutes))
not_through = lambda r: r.status_code not in (401, 403)           # got past the permission check
eq("an operator may use ProPresenter control", not_through(op_c.post("/api/admin/propresenter/step", json={})), True)
eq("an operator may use the stage message", not_through(op_c.post("/api/admin/propresenter/stage", json={})), True)
eq("an operator may pick today's plan", not_through(op_c.post("/api/admin/planning-center/plan", json={})), True)
eq("an operator may read what is up", not_through(op_c.get("/api/admin/propresenter")), True)
eq("an operator may not change ProPresenter's setup", op_c.post("/api/admin/propresenter", json={}).status_code, 403)
eq("an operator may not test or link", (op_c.post("/api/admin/propresenter/test", json={}).status_code,
                                         op_c.post("/api/admin/propresenter/link/playlist", json={}).status_code), (403, 403))
eq("an operator may not change Planning Center's setup", op_c.post("/api/admin/planning-center/service-types", json={}).status_code, 403)
eq("an operator may not see every macro", (op_c.get("/api/admin/propresenter/macros?all=true").status_code,
                                           not_through(op_c.get("/api/admin/propresenter/macros"))), (403, True))
eq("a viewer may read", (not_through(view_c.get("/api/admin/propresenter")), not_through(view_c.get("/api/admin/planning-center/live"))), (True, True))
eq("a viewer may not control", (view_c.post("/api/admin/propresenter/step", json={}).status_code,
                                 view_c.post("/api/admin/planning-center/plan", json={}).status_code), (403, 403))
eq("a viewer may not manage", (view_c.post("/api/admin/propresenter", json={}).status_code,
                                view_c.post("/api/admin/planning-center", json={}).status_code), (403, 403))
eq("an admin may do all three", (not_through(admin_c.post("/api/admin/planning-center/service-types", json={})),
                                  not_through(admin_c.post("/api/admin/propresenter/step", json={})),
                                  not_through(admin_c.get("/api/admin/propresenter/macros?all=true"))), (True, True, True))
eq("signed out is 401", anon_c.get("/api/admin/propresenter").status_code, 401)
config.save({"modules": {"propresenter": {"enabled": False}, "planning_center": {"enabled": False},
                         "remote_access": {"enabled": False}}})
eq("a module that is off is still a 404 for everyone", op_c.get("/api/admin/propresenter").status_code, 404)

# ------------------------------------------------------------------ the page routes
print("pages")
eq("/admin/flash signed out: the sign-in form", 'action="/admin/login"' in anon().get("/admin/flash").text, True)
for role in ("viewer", "operator"):
    eq(f"/admin/flash needs panels.manage: {role} is refused", clients[role].get("/admin/flash").status_code, 403)
for role in ("admin", "owner"):
    eq(f"/admin/flash: {role} may", clients[role].get("/admin/flash").status_code, 200)

# ------------------------------------------------------------------ POST /api/admin/config, field by field
print("config fields")
config.save({"openai_api_key": "", "default_font_px": 30, "recording": {"enabled": False, "keep_runs": 5}})
before = json.dumps(config.get(), sort_keys=True)
for role in ("viewer", "operator"):
    eq(f"{role} cannot change audio", clients[role].post("/api/admin/config", json={"audio_device": "x"}).status_code, 403)
eq("an operator cannot change appearance", op_c.post("/api/admin/config", json={"default_font_px": 44}).status_code, 403)
eq("an operator cannot change a blocklist", op_c.post("/api/admin/config", json={"blocklist": "x"}).status_code, 403)
eq("a viewer can post nothing at all, harmlessly", view_c.post("/api/admin/config", json={}).status_code, 200)
eq("a viewer cannot change anything", view_c.post("/api/admin/config", json={"history_lines": 9}).status_code, 403)
eq("nothing was saved by any of that", json.dumps(config.get(), sort_keys=True), before)
r = admin_c.post("/api/admin/config", json={"openai_api_key": "sk-admin-try"})
eq("an admin cannot set the OpenAI key", (r.status_code, detail(r)), (403, "Not allowed"))
check("…and it was not saved", config.get()["openai_api_key"] == "")
r = admin_c.post("/api/admin/config", json={"default_font_px": 41, "openai_api_key": "sk-admin-try"})
eq("a mixed body is refused whole", r.status_code, 403)
eq("…and the permitted half was not applied either", config.get()["default_font_px"], 30)
r = admin_c.post("/api/admin/config", json={"default_font_px": 41, "history_lines": 120, "recording": {"keep_runs": 7}})
eq("an admin changes appearance, history and recording", r.status_code, 200)
eq("…and it is saved", (config.get()["default_font_px"], config.get()["recording"]["keep_runs"]), (41, 7))
eq("an admin changes the blocklist", admin_c.post("/api/admin/config", json={"blocklist": "alpha\nbeta"}).status_code, 200)
eq("an admin changes the source language", admin_c.post("/api/admin/config", json={"source_language": "ENGLISH"}).status_code, 200)
r = owner_c.post("/api/admin/config", json={"openai_api_key": "sk-owner-9999"})
eq("an owner sets the key", r.status_code, 200)
eq("…and it is saved", config.get()["openai_api_key"], "sk-owner-9999")
check("…and never comes back whole", "sk-owner-9999" not in r.text)
cookie_before = owner_c.cookies.get("relay_admin")
token_before = config.get().get("admin_token")
r = owner_c.post("/api/admin/config", json={"admin_token": "a-new-token-123"})
eq("the admin_token field is gone: nothing is applied", r.status_code, 200)
check("…the stored token did not change", config.get().get("admin_token") == token_before)
check("…and no cookie was reissued", "set-cookie" not in r.headers and owner_c.cookies.get("relay_admin") == cookie_before)
eq("…and the session still works", owner_c.get("/api/admin/me").status_code, 200)
state = view_c.get("/api/admin/state")
eq("a viewer may read the redacted state", state.status_code, 200)
check("…without the key or the token", "sk-owner-9999" not in state.text and SETUP_TOKEN not in state.text)
config.save({"openai_api_key": ""})

# ------------------------------------------------------------------ the users API
print("users API")
r = admin_c.get("/api/admin/users")
body = r.json()
eq("the users list has the contract's shape", sorted(body), ["permissions", "roles", "users"])
eq("roles", body["roles"], [{"id": "member", "label": "Member", "rank": 1}, {"id": "viewer", "label": "Viewer", "rank": 2},
                            {"id": "operator", "label": "Operator", "rank": 3}, {"id": "admin", "label": "Admin", "rank": 4},
                            {"id": "owner", "label": "Owner", "rank": 5}])
eq("permissions carry id, label, group, min_role", sorted(body["permissions"][0]), ["group", "id", "label", "min_role"])
row = next(u for u in body["users"] if u["username"] == "owner")
eq("a user row", sorted(row), ["created", "disabled", "id", "last_login", "must_change_password", "name", "pin_set",
                              "role", "username"])
check("timestamps are ISO 8601 strings", isinstance(row["created"], str) and "T" in row["created"], row)
check("an admin sees owners in the list", any(u["role"] == "owner" for u in body["users"]))
for role in ("viewer", "operator"):
    eq(f"{role} cannot list users", clients[role].get("/api/admin/users").status_code, 403)
LEAK = ("password_hash", "pin_hash", "token_hash", "scrypt$")

r = admin_c.post("/api/admin/users", json={"username": "Newbie", "name": "New Bie", "role": "viewer", "password": "temporary one"})
eq("an admin creates a viewer", r.status_code, 200)
made = r.json()["user"]
eq("…the response is the user, with a pending password change",
   (sorted(made), made["username"], made["role"], made["must_change_password"]),
   (["disabled", "id", "must_change_password", "name", "pin_set", "role", "username"], "newbie", "viewer", True))
check("…no hashes in it", not any(k in r.text for k in LEAK))
r = admin_c.post("/api/admin/users", json={"username": "boss2", "name": "Boss", "role": "owner", "password": "temporary one"})
eq("an admin cannot create an owner", (r.status_code, detail(r)), (403, MSG["role_too_high"]))
r = admin_c.post("/api/admin/users", json={"username": "newbie", "name": "Dup", "role": "viewer", "password": "temporary one"})
eq("a taken username is a 409", (r.status_code, detail(r)), (409, MSG["username_taken"]))
r = admin_c.post("/api/admin/users", json={"username": "ab", "name": "Short", "role": "viewer", "password": "temporary one"})
eq("a bad username is a 400", (r.status_code, detail(r)), (400, MSG["bad_username"]))
r = admin_c.post("/api/admin/users", json={"username": "okname", "name": "Ok", "role": "king", "password": "temporary one"})
eq("a bad role is a 400", (r.status_code, detail(r)), (400, MSG["bad_role"]))
r = admin_c.post("/api/admin/users", json={"username": "okname", "name": "Ok", "role": "viewer", "password": "short"})
eq("a short password is a 400", (r.status_code, detail(r)), (400, MSG["bad_password"]))
r = admin_c.post("/api/admin/users", json={"username": 5, "name": ["x"], "role": {}, "password": None})
eq("values of the wrong type are a 400, not a 500", r.status_code, 400)
eq("an operator cannot create users", op_c.post("/api/admin/users", json={}).status_code, 403)

# The new person signs in with the temporary password and must change it.
nb = anon()
eq("the temporary password signs in", login(nb, "newbie", "temporary one").status_code, 303)
page = nb.get("/admin")
check("the admin page is the change-password form", 'action="/admin/password"' in page.text and 'name="current"' in page.text
      and 'autocomplete="new-password"' in page.text and 'name="confirm"' in page.text, page.text[:200])
check("…and not the admin page", '<h1 class="brand">' not in page.text)
for path in ("/api/admin/status", "/api/admin/state", "/api/admin/me/pin", "/api/admin/modules"):
    r = nb.get(path) if path != "/api/admin/me/pin" else nb.post(path, json={})
    eq(f"pending change: {path} is 403 password_change_required", (r.status_code, detail(r)), (403, "password_change_required"))
r = nb.get("/api/admin/me")
eq("…but /me still works", r.status_code, 200)
eq("…and says a change is pending", r.json()["user"]["must_change_password"], True)
nb2 = anon()
login(nb2, "newbie", "temporary one")                           # a second browser
r = nb.post("/admin/password", data={"current": "wrong wrong", "new": "my new password", "confirm": "my new password"})
eq("the form: a wrong current password", (r.status_code, MSG["wrong_password"] in r.text), (403, True))
check("…echoes no password", "my new password" not in r.text and "wrong wrong" not in r.text)
r = nb.post("/admin/password", data={"current": "temporary one", "new": "my new password", "confirm": "typo typo typo"})
eq("the form: new and confirm differ", (r.status_code, M.WEB_MESSAGES["mismatch"] in r.text), (400, True))
r = nb.post("/admin/password", data={"current": "temporary one", "new": "temporary one", "confirm": "temporary one"})
eq("the form: the same password again", (r.status_code, MSG["same_password"] in r.text), (400, True))
r = nb.post("/admin/password", data={"current": "temporary one", "new": "short", "confirm": "short"})
eq("the form: too short", (r.status_code, MSG["bad_password"] in r.text), (400, True))
fresh()
r = nb.post("/admin/password", data={"current": "temporary one", "new": "my new password", "confirm": "my new password"},
            follow_redirects=False)
eq("the form: a good change redirects to /admin", (r.status_code, r.headers["location"]), (303, "/admin"))
check("…and now the admin page opens", '<h1 class="brand">' in nb.get("/admin").text)
eq("…and the API", nb.get("/api/admin/status").status_code, 200)
eq("…and /me says nothing is pending", nb.get("/api/admin/me").json()["user"]["must_change_password"], False)
eq("their other browser was signed out by the change", nb2.get("/api/admin/me").status_code, 401)
eq("the old password no longer signs in", login(anon(), "newbie", "temporary one").status_code, 401)
eq("signed out, the password form posts go to the sign-in",
   anon().post("/admin/password", data={"current": "a", "new": "b", "confirm": "b"}, follow_redirects=False).headers["location"], "/admin")

# The JSON password change, for a user who must change it.
admin_c.post("/api/admin/users", json={"username": "second", "name": "Second", "role": "operator", "password": "temporary two"})
sc = anon()
login(sc, "second", "temporary two")
r = sc.post("/api/admin/me/password", json={"current": "bad bad bad", "new": "another password"})
eq("JSON: wrong current password", (r.status_code, detail(r)), (403, MSG["wrong_password"]))
r = sc.post("/api/admin/me/password", json={"current": "temporary two", "new": "another password"})
eq("JSON: change works while pending", (r.status_code, r.json()), (200, {"ok": True}))
eq("JSON: and the same session carries on", sc.get("/api/admin/status").status_code, 200)

# Rank rules.
owner_id = users.get_by_username("owner").id
admin_id = users.get_by_username("admin").id
op_id = users.get_by_username("operator").id
view_id = users.get_by_username("viewer").id
r = admin_c.put(f"/api/admin/users/{owner_id}", json={"name": "Hacked"})
eq("an admin cannot change an owner", (r.status_code, detail(r)), (403, MSG["account_above_you"]))
r = admin_c.post(f"/api/admin/users/{owner_id}/password", json={"password": "hacked hacked"})
eq("an admin cannot reset an owner's password", (r.status_code, detail(r)), (403, MSG["account_above_you"]))
eq("…and the owner still signs in with the old one", login(anon(), "owner").status_code, 303)
r = admin_c.delete(f"/api/admin/users/{owner_id}")
eq("an admin cannot delete an owner", (r.status_code, detail(r)), (403, MSG["account_above_you"]))
r = admin_c.post(f"/api/admin/users/{owner_id}/signout")
eq("an admin cannot sign an owner out", (r.status_code, detail(r)), (403, MSG["account_above_you"]))
r = admin_c.put(f"/api/admin/users/{op_id}", json={"role": "owner"})
eq("an admin cannot promote to owner", (r.status_code, detail(r)), (403, MSG["role_too_high"]))
r = admin_c.put(f"/api/admin/users/{admin_id}", json={"disabled": True})
eq("nobody turns themself off", (r.status_code, detail(r)), (403, MSG["self_action"]))
r = admin_c.delete(f"/api/admin/users/{admin_id}")
eq("nobody deletes themself", (r.status_code, detail(r)), (403, MSG["self_action"]))
r = admin_c.put(f"/api/admin/users/{op_id}", json={"disabled": "yes"})
eq("a non-boolean 'disabled' is a 400", (r.status_code, detail(r)), (400, M.WEB_MESSAGES["bad_request"]))
eq("an unknown user is a 404", (admin_c.put("/api/admin/users/99999", json={"name": "x"}).status_code,
                                admin_c.delete("/api/admin/users/99999").status_code), (404, 404))
r = admin_c.put(f"/api/admin/users/{view_id}", json={"name": "Renamed Viewer", "role": "operator"})
eq("an admin renames and promotes a viewer to operator",
   (r.status_code, r.json()["user"]["name"], r.json()["user"]["role"]), (200, "Renamed Viewer", "operator"))
check("…the viewer client's next request sees the new role", view_c.get("/api/admin/schedules").status_code == 200
      and view_c.post("/api/admin/targets-no-such", json={}).status_code == 404)
admin_c.put(f"/api/admin/users/{view_id}", json={"name": "Viewer", "role": "viewer"})
eq("…and back down again at once", view_c.get("/api/admin/recordings").status_code, 403)

# Last owner.
r = owner_c.put(f"/api/admin/users/{owner_id}", json={"role": "admin"})
eq("the last owner cannot be demoted", (r.status_code, detail(r)), (409, MSG["last_owner"]))
r = owner_c.put(f"/api/admin/users/{owner_id}", json={"disabled": True})
eq("…nor turned off (by themself)", (r.status_code, detail(r)), (403, MSG["self_action"]))
r = owner_c.post("/api/admin/users", json={"username": "owner2", "name": "Owner Two", "role": "owner", "password": "temporary one"})
eq("an owner creates an owner", r.status_code, 200)
owner2_id = r.json()["user"]["id"]
r = owner_c.put(f"/api/admin/users/{owner2_id}", json={"role": "admin"})
eq("with two, one can be demoted", r.status_code, 200)
r = admin_c.put(f"/api/admin/users/{owner2_id}", json={"role": "owner"})
eq("…and an admin cannot put them back", r.status_code, 403)
owner_c.put(f"/api/admin/users/{owner2_id}", json={"role": "owner"})
r = owner_c.delete(f"/api/admin/users/{owner2_id}")
eq("an owner deletes another owner while one other remains", r.status_code, 200)
r = owner_c.put(f"/api/admin/users/{owner_id}", json={"role": "admin"})
eq("…and then the one left is protected again", r.status_code, 409)

# Reset, signout, disable: the other person's next request.
tmp_c = _db.client(app, "operator", username="tempop")
r = admin_c.post(f"/api/admin/users/{users.get_by_username('tempop').id}/signout")
eq("an admin signs an operator out", (r.status_code, r.json()), (200, {"ok": True}))
eq("…their next request is 401", tmp_c.get("/api/admin/me").status_code, 401)
tmp_c = _db.client(app, "operator", username="tempop")
tid = users.get_by_username("tempop").id
r = admin_c.post(f"/api/admin/users/{tid}/password", json={"password": "reset reset 1"})
eq("an admin resets their password", (r.status_code, r.json()), (200, {"ok": True}))
eq("…which ends their sessions", tmp_c.get("/api/admin/me").status_code, 401)
t2 = anon()
login(t2, "tempop", "reset reset 1")
eq("…and the new one is temporary", t2.get("/api/admin/me").json()["user"]["must_change_password"], True)
tmp_c = _db.client(app, "operator", username="tempop")
r = admin_c.put(f"/api/admin/users/{tid}", json={"disabled": True})
eq("an admin turns an operator off", (r.status_code, r.json()["user"]["disabled"]), (200, True))
eq("…their next request is 401", tmp_c.get("/api/admin/me").status_code, 401)
eq("…and they cannot sign in", login(anon(), "tempop").status_code, 403)
admin_c.put(f"/api/admin/users/{tid}", json={"disabled": False})
tmp_c = _db.client(app, "operator", username="tempop")
r = admin_c.delete(f"/api/admin/users/{tid}")
eq("an admin deletes an operator", r.status_code, 200)
eq("…their session is gone", tmp_c.get("/api/admin/me").status_code, 401)

# Self-service PIN and password.
me_c = _db.client(app, "operator", username="selfserve")
r = me_c.post("/api/admin/me/pin", json={"password": "bad bad bad", "pin": "1234"})
eq("a PIN needs the right password", (r.status_code, detail(r)), (403, MSG["wrong_password"]))
r = me_c.post("/api/admin/me/pin", json={"password": PW, "pin": "12"})
eq("a PIN is 4 to 8 digits", (r.status_code, detail(r)), (400, MSG["bad_pin"]))
r = me_c.post("/api/admin/me/pin", json={"password": PW, "pin": "4321"})
eq("a PIN is set", (r.status_code, r.json()), (200, {"ok": True, "pin_set": True}))
eq("…and /me says so", me_c.get("/api/admin/me").json()["pin_set"], True)
check("…and the PIN is stored hashed only", db.one("select pin_hash from users where username='selfserve'")["pin_hash"].startswith("scrypt$"))
r = me_c.delete("/api/admin/me/pin")
eq("a PIN is cleared", (r.status_code, r.json()["pin_set"]), (200, False))
other = anon()
login(other, "selfserve")
r = me_c.post("/api/admin/me/password", json={"current": PW, "new": "brand new password"})
eq("changing your own password", r.status_code, 200)
eq("…keeps this browser signed in", me_c.get("/api/admin/me").status_code, 200)
eq("…and signs the other one out", other.get("/api/admin/me").status_code, 401)
eq("…the new password works", login(anon(), "selfserve", "brand new password").status_code, 303)

# Nothing sensitive in any of it.
for label, resp in (("users", owner_c.get("/api/admin/users")), ("me", owner_c.get("/api/admin/me")),
                    ("state", owner_c.get("/api/admin/state")), ("devices", owner_c.get("/api/admin/trusted-devices"))):
    check(f"no hashes in the {label} response", not any(k in resp.text for k in LEAK), resp.text[:100])

# ------------------------------------------------------------------ trusted devices and PINs
print("trusted devices")
eq("an operator cannot trust a device", op_c.post("/api/admin/trusted-devices", json={"name": "x"}).status_code, 403)
eq("…nor list them", op_c.get("/api/admin/trusted-devices").status_code, 403)
r = admin_c.post("/api/admin/trusted-devices", json={"name": ""})
eq("a device needs a name", (r.status_code, detail(r)), (400, MSG["bad_device_name"]))
r = admin_c.post("/api/admin/trusted-devices", json={"name": "Booth iPad"})
eq("an admin trusts this browser", r.status_code, 200)
dev = r.json()["device"]
eq("…the device row", (sorted(dev), dev["name"]), (["created", "created_by", "id", "last_used", "name"], "Booth iPad"))
attrs = cookie_attrs(r, "relay_device")
check("…sets the relay_device cookie, HttpOnly SameSite=Strict", "httponly" in attrs and "samesite=strict" in attrs, attrs)
eq("…for two years", max_age(attrs), 2 * 365 * 24 * 3600)
check("…not Secure over plain http", "secure" not in attrs)
check("…the token is not stored", admin_c.cookies.get("relay_device") not in
      {row["token_hash"] for row in db.query("select token_hash from trusted_devices")})
r = admin_c.get("/api/admin/trusted-devices")
eq("the list has the contract's shape", (sorted(r.json()), sorted(r.json()["devices"][0])),
   (["devices", "this"], ["created", "created_by", "id", "last_used", "name"]))
eq("…and says which is this browser", r.json()["this"], dev["id"])
eq("…another browser has none", owner_c.get("/api/admin/trusted-devices").json()["this"], None)
eq("/me knows the device", admin_c.get("/api/admin/me").json()["device"], {"trusted": True, "name": "Booth iPad"})
r = _db.client(app, "admin", username="admin").post("/api/admin/trusted-devices", json={"name": "Secure one"},
                                                  headers={"x-forwarded-proto": "https"})
check("a device cookie from a LAN client is not Secure however it claims", "secure" not in cookie_attrs(r, "relay_device"))
r = _db.client(app, "admin", username="admin", client=("127.0.0.1", 5)).post(
    "/api/admin/trusted-devices", json={"name": "TLS one"}, headers={"x-forwarded-proto": "https"})
check("…and through the TLS front end it is", "secure" in cookie_attrs(r, "relay_device"))
for d in admin_c.get("/api/admin/trusted-devices").json()["devices"]:
    if d["name"] != "Booth iPad":
        admin_c.delete(f"/api/admin/trusted-devices/{d['id']}")

# A PIN for the operator, then sign in with it from the trusted browser.
fresh()
pin_c = _db.client(app, "operator", username="pinuser")
pin_c.post("/api/admin/me/pin", json={"password": PW, "pin": "2468"})
nopin_c = _db.client(app, "viewer", username="nopin")
users_with_pins = users.pin_users()
eq("pin_users lists only people with a PIN", [u["name"] for u in users_with_pins], ["Pinuser"])

trusted_token = admin_c.cookies.get("relay_device")


def browser(trusted=True):
    b = anon()
    if trusted:
        b.cookies.set("relay_device", trusted_token)
    return b


b = browser(False)
r = b.get("/admin")
check("an untrusted browser gets the password form, never a PIN picker", 'action="/admin/login"' in r.text
      and "/admin/login/pin" not in r.text)
r = b.post("/admin/login/pin", data={"user_id": str(users.get_by_username("pinuser").id), "pin": "2468"})
eq("a PIN does not sign in from an untrusted browser", (r.status_code, MSG["no_device"] in r.text), (401, True))
check("…and lands on the password form", 'action="/admin/login"' in r.text)
b = browser()
r = b.get("/admin")
check("a trusted browser gets the PIN picker", 'action="/admin/login/pin"' in r.text, r.text[:300])
check("…listing only users with a PIN", "Pinuser" in r.text and "Nopin" not in r.text and "Owner" not in r.text)
check("…a numeric PIN field with no autofill", 'inputmode="numeric"' in r.text and 'name="pin"' in r.text
      and 'autocomplete="off"' in r.text and "one-time-code" not in r.text)
check("…and a way back to the password", 'href="/admin?pw=1"' in r.text)
r = b.get("/admin?pw=1")
check("…which shows the password form", 'action="/admin/login"' in r.text and 'href="/admin"' in r.text)
uid = str(users.get_by_username("pinuser").id)
r = b.post("/admin/login/pin", data={"user_id": uid, "pin": "0000"})
eq("a wrong PIN is refused", (r.status_code, MSG["wrong_pin"] in r.text), (401, True))
check("…and the picker keeps the user selected", f'value="{uid}" selected' in r.text)
r = b.post("/admin/login/pin", data={"user_id": "abc", "pin": "2468"})
eq("a junk user id is just a wrong PIN", r.status_code, 401)
fresh()
r = b.post("/admin/login/pin", data={"user_id": uid, "pin": "2468"}, follow_redirects=False)
eq("the right PIN signs in", (r.status_code, r.headers["location"]), (303, "/admin"))
attrs = cookie_attrs(r, "relay_admin")
eq("…for 12 hours", max_age(attrs), 12 * 3600)
check("…HttpOnly, SameSite=Strict", "httponly" in attrs and "samesite=strict" in attrs)
eq("…as that user", b.get("/api/admin/me").json()["user"]["username"], "pinuser")
eq("…with the operator's permissions", b.get("/api/admin/users").status_code, 403)
eq("…and not more", b.post("/api/admin/start").status_code != 403, True)

# Five wrong PINs clear it.
b2 = browser()
codes = []
for _ in range(5):
    codes.append(b2.post("/admin/login/pin", data={"user_id": uid, "pin": "9999"}).status_code)
eq("five wrong PINs are all 401", codes, [401] * 5)
check("…the fifth says the PIN is cleared", MSG["pin_cleared"] in b2.post("/admin/login/pin", data={"user_id": uid, "pin": "9999"}).text
      or users.get_by_username("pinuser").pin_set is False)
eq("…the PIN is gone", users.get_by_username("pinuser").pin_set, False)
eq("…and the PIN session with it", b.get("/api/admin/me").status_code, 401)
fresh()
check("…and the picker no longer lists them", "Pinuser" not in browser().get("/admin").text)

# Revoking a device ends its PIN sessions.
pin_c = _db.client(app, "operator", username="pinuser")
pin_c.post("/api/admin/me/pin", json={"password": PW, "pin": "2468"})
b = browser()
eq("PIN sign-in again", b.post("/admin/login/pin", data={"user_id": uid, "pin": "2468"}, follow_redirects=False).status_code, 303)
eq("…works", b.get("/api/admin/me").status_code, 200)
r = op_c.delete(f"/api/admin/trusted-devices/{dev['id']}")
eq("an operator cannot revoke a device", r.status_code, 403)
r = admin_c.delete(f"/api/admin/trusted-devices/{dev['id']}")
eq("an admin revokes it", (r.status_code, r.json()), (200, {"ok": True}))
eq("…its PIN session is dead on the next request", b.get("/api/admin/me").status_code, 401)
eq("…password sessions are not", pin_c.get("/api/admin/me").status_code, 200)
r = browser().post("/admin/login/pin", data={"user_id": uid, "pin": "2468"})
eq("…and the browser's PIN no longer signs in", (r.status_code, MSG["no_device"] in r.text), (401, True))
eq("revoking it again is a 404", admin_c.delete(f"/api/admin/trusted-devices/{dev['id']}").status_code, 404)
eq("an admin's password reset clears the PIN",
   (admin_c.post(f"/api/admin/users/{uid}/password", json={"password": "reset reset 2"}).status_code,
    users.get_by_username("pinuser").pin_set), (200, False))
fresh()

# ------------------------------------------------------------------ the viewer port reaches none of it
print("viewer port")
v = anon(VIEWER, cookies=dict(owner_c.cookies))
v.cookies.set("relay_device", trusted_token)
paths = [("GET", "/admin"), ("GET", "/admin?pw=1"), ("GET", "/admin/flash"), ("POST", "/admin/login"),
         ("POST", "/admin/login/pin"), ("POST", "/admin/setup"), ("POST", "/admin/password"), ("POST", "/admin/logout"),
         ("GET", "/api/admin/me"), ("GET", "/api/admin/users"), ("POST", "/api/admin/users"),
         ("GET", "/api/admin/trusted-devices"), ("POST", "/api/admin/trusted-devices"),
         ("POST", "/api/admin/me/password"), ("POST", "/api/admin/me/pin"), ("DELETE", "/api/admin/me/pin"),
         ("GET", "/api/admin/state"), ("POST", "/api/admin/config")]
bad = [(m, p, v.request(m, p, json={} if m != "GET" else None).status_code) for m, p in paths
       if v.request(m, p, json={} if m != "GET" else None).status_code != 404]
check("with an owner's cookie, every admin page and route is 404 on the viewer port", not bad, bad)
bad = [(m, p) for m, p, _ in found if send(v, m, concrete(p)).status_code != 404]
check("…and so is every /api/admin route", not bad, bad[:5])
eq("signing in on the viewer port is impossible", login(anon(VIEWER), "owner").status_code, 404)

# ------------------------------------------------------------------ module permissions and the nav
print("module permissions")


class Fake(Module):
    manifest = Manifest(id="fake", name="Fake", version="1.0.0", summary="Made up for this test.", settings_key="fake_s")
    permissions = (users.Permission("fake.view", "See it", "Fake", "viewer"),
                   users.Permission("fake.manage", "Change it", "Fake", "admin"))
    admin_pages = (modmod.AdminPage(id="fakeview", title="Fake", template="x.html", group="Output"),
                   modmod.AdminPage(id="fakeset", title="Fake settings", template="y.html", group="Settings",
                                    permission="fake.manage"))
    admin_scripts = ("fake.js",)


fake = Fake()
problems = modmod.validate(fake, "fake")
check("a module with permissions validates (apart from its missing files)", all("template" in p or "script" in p for p in problems), problems)
Fake.permissions = (users.Permission("other.view", "x", "x", "viewer"),)
check("a permission outside the module's id is rejected", any("must start with 'fake.'" in p for p in modmod.validate(Fake(), "fake")))
Fake.permissions = (users.Permission("fake.view", "See it", "Fake", "viewer"),
                    users.Permission("fake.manage", "Change it", "Fake", "admin"))
Fake.admin_pages = (modmod.AdminPage(id="fakeview", title="Fake", template="x.html", group="Output", permission="fake.nope"),)
check("a page needing an undeclared permission is rejected", any("fake.nope" in p for p in modmod.validate(Fake(), "fake")))
Fake.admin_pages = fake.admin_pages = (
    modmod.AdminPage(id="fakeview", title="Fake", template="x.html", group="Output"),
    modmod.AdminPage(id="fakeset", title="Fake settings", template="y.html", group="Settings", permission="fake.manage"))
users.register(Fake.permissions)
eq("registered with users", {"fake.view", "fake.manage"} <= {p.id for p in users.catalogue()}, True)
eq("…at the right ranks", (users.User(0, "a", "A", "viewer", False, False, False).can("fake.view"),
                          users.User(0, "a", "A", "viewer", False, False, False).can("fake.manage"),
                          users.User(0, "a", "A", "admin", False, False, False).can("fake.manage")), (True, False, True))

saved_modules, saved_enabled = registry._modules, registry.enabled
registry._modules = {**saved_modules, "fake": fake}
registry.enabled = lambda mid: mid == "fake"
try:
    def U(role):
        return users.User(0, role, role, role, False, False, False)
    eq("viewer: only the page it may see",
       {g: [p["id"] for p in ps] for g, ps in registry.admin_nav(U("viewer")).items()}, {"Output": ["fakeview"]})
    eq("admin: both pages", sorted(p["id"] for ps in registry.admin_nav(U("admin")).values() for p in ps), ["fakeset", "fakeview"])
    eq("no user: everything (for code without one)", sorted(p["id"] for ps in registry.admin_nav().values() for p in ps),
       ["fakeset", "fakeview"])
    eq("viewer: the module's script, since a page is visible", registry.admin_scripts(U("viewer")), ["fake/fake.js"])
    Fake.admin_pages = fake.admin_pages = (modmod.AdminPage(id="fakeset", title="Fake settings", template="y.html",
                                                            group="Settings", permission="fake.manage"),)
    eq("viewer: no visible page, no script", registry.admin_scripts(U("viewer")), [])
    eq("admin: the script", registry.admin_scripts(U("admin")), ["fake/fake.js"])
finally:
    registry._modules, registry.enabled = saved_modules, saved_enabled

# the real modules' declarations
for mid, want in (("planning_center", {"planning_center.view": "viewer", "planning_center.control": "operator",
                                       "planning_center.manage": "admin"}),
                  ("propresenter", {"propresenter.view": "viewer", "propresenter.control": "operator",
                                    "propresenter.manage": "admin"}),
                  ("remote_access", {"remote_access.view": "viewer", "remote_access.manage": "admin"})):
    got = {p.id: p.min_role for p in registry.get(mid).permissions}
    eq(f"{mid} declares its permissions", got, want)

# `require` for modules answers like core's
mod_probe = {}
import fastapi  # noqa: E402

probe = fastapi.FastAPI()
modmod.permission_hook = M.require


@probe.get("/p", dependencies=[fastapi.Depends(modmod.require("planning_center.manage"))])
def _probe():
    return {"ok": True}


probe_client = TestClient(probe)
eq("modules.require: signed out", probe_client.get("/p").status_code, 401)
probe_client.cookies.set("relay_admin", op_c.cookies.get("relay_admin"))
eq("modules.require: an operator lacks .manage", probe_client.get("/p").status_code, 403)
probe_client.cookies.set("relay_admin", admin_c.cookies.get("relay_admin"))
eq("modules.require: an admin has it", probe_client.get("/p").status_code, 200)

# ------------------------------------------------------------------ nothing leaks into the database either
print("what is stored")
check("no password or PIN in the clear anywhere in the users table",
      not any(PW in str(v) or "2468" in str(v) for row in db.query("select * from users") for v in row.values()))
check("session tokens are hashes", all(len(r_["token_hash"]) == 64 for r_ in db.query("select token_hash from sessions")))

# ------------------------------------------------------------------ the backup schedule
print("backups")
calls = []
real_due, real_backup = db.backup_due, db.backup
db.backup_due = lambda *a, **k: True
db.backup = lambda *a, **k: calls.append(1)
M._backup_if_due()
eq("a backup runs when one is due", len(calls), 1)
db.backup_due = lambda *a, **k: False
M._backup_if_due()
eq("…and not when none is", len(calls), 1)


async def loop_survives():
    n = []

    def boom():
        n.append(1)
        raise RuntimeError("backup exploded")
    saved = (M._backup_if_due, M.BACKUP_FIRST_DELAY_S, M.BACKUP_EVERY_S)
    M._backup_if_due, M.BACKUP_FIRST_DELAY_S, M.BACKUP_EVERY_S = boom, 0.01, 0.01
    logging.disable(logging.CRITICAL)           # the failure is logged with a traceback; keep the output clean
    try:
        task = asyncio.create_task(M._backup_loop())
        await asyncio.sleep(0.2)
        alive = not task.done()
        task.cancel()
        return len(n), alive
    finally:
        logging.disable(logging.NOTSET)
        M._backup_if_due, M.BACKUP_FIRST_DELAY_S, M.BACKUP_EVERY_S = saved


tries, alive = asyncio.run(loop_survives())
check("the backup loop keeps going after a failure", tries >= 2 and alive, (tries, alive))
db.backup_due, db.backup = real_due, real_backup

# start-up and shutdown
with TestClient(app, base_url=ADMIN) as lifespan_client:
    task = getattr(app.state, "backup_task", None)
    check("start-up schedules the backup task", task is not None and not task.done())
    eq("…and the database is up", lifespan_client.get("/admin").status_code, 200)
check("shutdown cancels it", app.state.backup_task.cancelled() or app.state.backup_task.done())
eq("the database reopens on demand after shutdown closed it", db.available(), True)

print()
if fails:
    print(f"{len(fails)} FAILED")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all auth checks passed")
