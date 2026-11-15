"""The audit log (#172): sign-ins, failed sign-ins, user and role changes and every admin change, each tied to
who made it; the page's API and its permission; retention; and that writing it never breaks a request.

Runs against a real throwaway SQLite file (tests/_db.py). Offline: no OpenAI, no hardware.
Run: .venv/bin/python tests/test_audit.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import audit, config, db, users  # noqa: E402
from app import main as M  # noqa: E402
from app.main import app  # noqa: E402

users.SCRYPT_N = 2 ** 10
M.LOGIN_FAIL_DELAY_S = 0.0

ADMIN = "http://relay.local:8001"
PW = "correct horse"

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def entries(**kw):
    return audit.search(**kw)["entries"]


def last(action=None):
    rows = entries(kind=action) if action else entries()
    return rows[0] if rows else {}


def clear():
    db.execute("delete from audit_log")
    M._LOGIN_FAILS.clear()


config.ensure_file()
config.save({"port": 8000, "admin_port": 8001,
             "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt")})

owner_c = _db.client(app, "owner", base_url=ADMIN)
owner = users.get_by_username("owner")

# ------------------------------------------------------------------ sign-ins
print("sign-ins")
e = last("auth.password")
eq("a password sign-in is recorded against the person", (e.get("actor_id"), e.get("actor_username")),
   (owner.id, "owner"))
check("…with the address it came from", e.get("ip") == "testclient", e)

clear()
c = TestClient(app, base_url=ADMIN)
c.post("/admin/login", data={"username": "owner", "password": "wrong one"}, follow_redirects=False)
e = last("auth.failed")
eq("a wrong password is a failed sign-in against that account",
   (e.get("actor_id"), e.get("target"), e["detail"].get("reason")), (None, "owner", "bad_login"))
c.post("/admin/login", data={"username": "my secret pass", "password": "x"}, follow_redirects=False)
e = last("auth.failed")
eq("an unknown username is never written (it may be a password typed in the wrong box)",
   e.get("target"), "(unknown username)")
check("…anywhere in the entry", "secret" not in json.dumps(e), e)

clear()
for _ in range(M.LOGIN_MAX_FAILS + 2):
    c.post("/admin/login", data={"username": "owner", "password": "wrong"}, follow_redirects=False)
eq("the lockout is recorded once, not each refusal during it", len(entries(kind="auth.locked")), 1)
eq("…and the failures before it", len(entries(kind="auth.failed")), M.LOGIN_MAX_FAILS)
M._LOGIN_FAILS.clear()

users.update(owner.id, disabled=False, actor=None)
off = users.create("offuser", "Off User", "viewer", PW, temporary=False)
users.update(off.id, disabled=True, actor=None)
clear()
c.post("/admin/login", data={"username": "offuser", "password": PW}, follow_redirects=False)
eq("a turned-off account's sign-in is recorded as refused", (last()["action"], last()["detail"]["reason"]),
   ("auth.failed", "disabled"))

clear()
viewer_c = _db.client(app, "viewer", base_url=ADMIN)
viewer = users.get_by_username("viewer")
viewer_c.post("/admin/logout", follow_redirects=False)
e = last("auth.signout")
eq("signing out is recorded against the person", e.get("actor_id"), viewer.id)

# ------------------------------------------------------------------ user and role changes
print("user and role changes")
clear()
r = owner_c.post("/api/admin/users", json={"username": "newbie", "name": "New Bie", "role": "operator",
                                            "password": "temp pass 1"})
eq("adding a user succeeds", r.status_code, 200)
e = last()
eq("…and is recorded with who, whom and the role",
   (e["action"], e["actor_id"], e["target"], e["detail"]), ("user.create", owner.id, "newbie", {"role": "operator"}))
check("…never the temporary password", "temp pass" not in json.dumps(e), e)
nid = r.json()["user"]["id"]

owner_c.put(f"/api/admin/users/{nid}", json={"role": "admin"})
e = last()
eq("a role change records from and to", (e["action"], e["detail"]["changes"]), ("user.update", {"role": ["operator", "admin"]}))
check("…in words", "from Operator to Admin" in e["summary"], e["summary"])
owner_c.put(f"/api/admin/users/{nid}", json={"disabled": True})
eq("turning someone off is recorded", last()["detail"]["changes"], {"disabled": [False, True]})

owner_c.post(f"/api/admin/users/{nid}/password", json={"password": "another temp 1"})
eq("a password reset is recorded, not the password", (last()["action"], last()["target"]),
   ("user.password_reset", "newbie"))
check("…the password nowhere", "another temp" not in json.dumps(last()))
owner_c.delete(f"/api/admin/users/{nid}")
eq("deleting a user is recorded with their name", (last()["action"], last()["target"]), ("user.delete", "newbie"))

clear()
r = owner_c.delete(f"/api/admin/users/{owner.id}")
check("a refused change is refused", r.status_code >= 400, r.status_code)
eq("…and leaves no entry", entries(), [])

# ------------------------------------------------------------------ every admin change
print("every admin change")
clear()
r = owner_c.post("/api/admin/config", json={"history_lines": 66, "openai_api_key": "sk-very-secret"})
eq("a settings change succeeds", r.status_code, 200)
e = last()
eq("…is recorded with the fields' names", (e["action"], e["detail"]), ("settings.change",
                                                                        {"fields": ["history_lines", "openai_api_key"]}))
check("…never their values", "sk-very-secret" not in json.dumps(e), e)

clear()
r = owner_c.post("/api/admin/schedules", json={})
e = entries()
check("a route with no note of its own is recorded from the table when it succeeds, or not at all",
      (r.status_code < 400 and e and e[0]["action"] == "schedules.create") or (r.status_code >= 400 and not e),
      (r.status_code, e))

core_changes = sorted({rt.name for rt in app.routes if isinstance(rt, APIRoute)
                       and rt.path.startswith("/api/admin/") and rt.methods & {"POST", "PUT", "PATCH", "DELETE"}
                       and rt.endpoint.__module__ == "app.main"})
missing = [n for n in core_changes if n not in M.AUDIT_ROUTES and n not in M.AUDIT_SKIP]
check("every core admin route that changes something has an audit entry in main.AUDIT_ROUTES", not missing, missing)
check("…and every entry there is a route that exists", set(M.AUDIT_ROUTES) <= set(core_changes),
      sorted(set(M.AUDIT_ROUTES) - set(core_changes)))
check("…and every route skipped exists and is not also listed", M.AUDIT_SKIP <= set(core_changes)
      and not M.AUDIT_SKIP & set(M.AUDIT_ROUTES), sorted(M.AUDIT_SKIP))

# A module's route: nothing in core names it, and it is still recorded.
from fastapi import APIRouter, Depends, Request  # noqa: E402
router = APIRouter()


@router.post("/api/admin/zzfake/thing/{tid}", dependencies=[Depends(M.require("settings.manage"))])
def _fake(tid: str):
    return {"ok": True}


@router.post("/api/admin/zzfake/noted", dependencies=[Depends(M.require("settings.manage"))])
def _fake_noted(request: Request):
    audit.note(request, "zzfake.build", "Built the thing", target="Sunday", detail={"items": 3})
    return {"ok": True}


app.include_router(router)
clear()
owner_c.post("/api/admin/zzfake/thing/7")
e = last()
eq("a route nobody described is recorded as its method and path",
   (e["action"], e["summary"], e["actor_id"]), ("api.change", "POST /api/admin/zzfake/thing/7", owner.id))
owner_c.post("/api/admin/zzfake/noted")
e = last()
eq("a module's own note is what is recorded", (e["action"], e["summary"], e["target"], e["detail"]),
   ("zzfake.build", "Built the thing", "Sunday", {"items": 3}))
# A module route whose function shares a name with a skipped core route is still recorded (#287): the skip
# matches the core function itself, not its name.
twin = APIRouter()


def admin_me_pref_set():  # noqa: F811 -- the same name as the core route, on purpose
    return {"ok": True}


twin.add_api_route("/api/admin/zzfake/twin", admin_me_pref_set, methods=["POST"],
                   dependencies=[Depends(M.require("settings.manage"))])
app.include_router(twin)
clear()
r = owner_c.post("/api/admin/zzfake/twin")
eq("a module route named like a skipped core route succeeds", r.status_code, 200)
eq("…and is still recorded", [x["summary"] for x in entries()], ["POST /api/admin/zzfake/twin"])
clear()
owner_c.put("/api/admin/me/prefs/user/zz.layout", json={"value": 1})
eq("the core preference route itself is still left out", entries(), [])
before = len(entries())
owner_c.get("/api/admin/status")
eq("reading changes nothing in the log", len(entries()), before)

# A module's work with no request behind it.
check("record() with no actor is the system", audit.record("zzfake.sync", "Synced overnight"))
eq("…filed under nobody", (last()["actor_id"], last()["actor_name"]), (None, ""))
check("a bad action id is refused, not raised", audit.record("Not An Id", "x") is False)

# ------------------------------------------------------------------ API tokens
print("API tokens")
admin_c = _db.client(app, "admin", base_url=ADMIN)
admin = users.get_by_username("admin")
r = admin_c.post("/api/admin/me/tokens", json={"name": "Stream deck", "days": 30})
secret = r.json()["secret"]
eq("making a token is recorded with its name", (last()["action"], last()["target"]), ("token.create", "Stream deck"))
check("…not its secret", secret not in json.dumps(last()))
t = TestClient(app, base_url=ADMIN, headers={"Authorization": "Bearer " + secret})
clear()
t.post("/api/admin/config", json={"history_lines": 55})
e = last()
eq("a change made with a token names the person and the token", (e["actor_id"], e["token_name"]),
   (admin.id, "Stream deck"))
eq("a token cannot read the log", t.get("/api/admin/audit").status_code, 403)

# ------------------------------------------------------------------ the page's API
print("reading the log")
eq("a viewer cannot read the log", viewer_c.get("/api/admin/audit").status_code, 401)   # signed out above
op_c = _db.client(app, "operator", base_url=ADMIN)
eq("an operator cannot read it", op_c.get("/api/admin/audit").status_code, 403)
eq("an admin can", admin_c.get("/api/admin/audit").status_code, 200)
body = owner_c.get("/api/admin/audit").json()
check("it answers entries, people, kinds and how long they are kept",
      {"entries", "more", "people", "kinds", "keep_days"} <= set(body) and body["keep_days"] == 90, sorted(body))
check("…people by id and name", any(p["id"] == admin.id and p["username"] == "admin" for p in body["people"]),
      body["people"])
only = owner_c.get(f"/api/admin/audit?who={admin.id}").json()["entries"]
check("filter by person", only and all(x["actor_id"] == admin.id for x in only))
sysrows = owner_c.get("/api/admin/audit?who=system").json()["entries"]
check("filter to the system", all(x["actor_id"] is None for x in sysrows))
kind = owner_c.get("/api/admin/audit?kind=auth").json()["entries"]
check("filter by kind", kind and all(x["action"].startswith("auth.") for x in kind))
found = owner_c.get("/api/admin/audit?q=Stream%20d").json()["entries"]
check("search the text", found and all("Stream d" in (x["token_name"] + x["target"] + x["summary"]) for x in found))
eq("a search's wildcards are literal", owner_c.get("/api/admin/audit?q=%25%25").json()["entries"], [])
for i in range(5):
    audit.record("zzfake.page", f"entry {i}")
page = audit.search(kind="zzfake.page", limit=2)
nxt = audit.search(kind="zzfake.page", limit=2, before=page["entries"][-1]["id"])
eq("pages follow on, newest first", [x["summary"] for x in page["entries"] + nxt["entries"]],
   ["entry 4", "entry 3", "entry 2", "entry 1"])
check("…and say when there is more", page["more"] and nxt["more"])

# ------------------------------------------------------------------ retention
print("retention")
clear()
db.execute("insert into audit_log (at, action, summary) values (?, 'zzfake.old', 'old')", (db.later(-91 * 86400),))
db.execute("insert into audit_log (at, action, summary) values (?, 'zzfake.new', 'new')", (db.later(-89 * 86400),))
eq("entries older than 90 days go", audit.prune(), 1)
eq("…the rest stay", [x["action"] for x in entries()], ["zzfake.new"])
was = audit.MAX_ROWS
audit.MAX_ROWS = 3
for i in range(5):
    audit.record("zzfake.flood", f"f{i}")
audit.prune()
eq("beyond MAX_ROWS the oldest go, whatever their age", [x["summary"] for x in entries()], ["f4", "f3", "f2"])
audit.MAX_ROWS = was

# ------------------------------------------------------------------ never breaks what it records
print("the database is down")
good = os.environ["RELAY_DATABASE"]
broken = Path(os.environ["RELAY_CONFIG"]).parent / "broken.db"
broken.write_bytes(b"not a database " * 100)
os.environ["RELAY_DATABASE"] = str(broken)
db.close()
check("record() returns False instead of raising", audit.record("zzfake.down", "x") is False)
os.environ["RELAY_DATABASE"] = good
db.close()
check("…and works again once the file is back", audit.record("zzfake.up", "x"))

if fails:
    print(f"\n{len(fails)} FAILED")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nall audit checks passed")
