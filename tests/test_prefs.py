"""Per-user preferences (#173): stored per user, scope and name in the user database; a module keeps its own
under its id; the signed-in user's API; limits; nothing in the audit log; and gone when the user is deleted.

Runs against a real throwaway SQLite file (tests/_db.py). Offline: no OpenAI, no hardware.
Run: .venv/bin/python tests/test_prefs.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"   # switching every module on must not fetch LiveKit
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import audit, config, db, prefs, users  # noqa: E402
from app import main as M  # noqa: E402
from app.main import app  # noqa: E402

users.SCRYPT_N = 2 ** 10
ADMIN = "http://relay.local:8001"
PW = "correct horse"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


def invalid(label, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except prefs.Invalid as e:
        check(label + " (a fixed sentence)", str(e).endswith("."), str(e))
        return
    check(label, False, "no refusal")


config.ensure_file()
config.save({"port": 8000, "admin_port": 8001,
             "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt")})
db.init()

# ------------------------------------------------------------------ the store
print("the store")
a = users.create("alice", "Alice", "operator", PW, temporary=False)
b = users.create("bob", "Bob", "viewer", PW, temporary=False)

eq("an unset preference is the default", prefs.get(a.id, "default_page", "/admin"), "/admin")
prefs.set(a.id, "default_page", "/screen")
prefs.set(a.id, "layout", {"columns": 2, "dense": True, "order": ["status", "panels"]})
eq("a set preference reads back", prefs.get(a.id, "default_page"), "/screen")
eq("…JSON values keep their shape", prefs.get(a.id, "layout"),
   {"columns": 2, "dense": True, "order": ["status", "panels"]})
prefs.set(a.id, "default_page", "/home")
eq("setting again replaces", prefs.get(a.id, "default_page"), "/home")
eq("…one row, not two", db.one("select count(*) as n from user_prefs where user_id = ? and name = ?",
                              (a.id, "default_page"))["n"], 1)
eq("each person has their own", prefs.get(b.id, "default_page"), None)

prefs.set(a.id, "layout", "compact", scope="mixer")
eq("a module's preference sits under its id, apart from core's",
   (prefs.get(a.id, "layout", scope="mixer"), prefs.get(a.id, "layout")["columns"]), ("compact", 2))
eq("scope_prefs is one scope", prefs.scope_prefs(a.id, "mixer"), {"layout": "compact"})
eq("all_prefs is every scope", sorted(prefs.all_prefs(a.id)), ["core", "mixer"])

prefs.set(a.id, "language", "es")
prefs.set(a.id, "language", None)
eq("setting None removes it", prefs.get(a.id, "language", "x"), "x")
check("delete says whether there was one", prefs.delete(a.id, "layout", scope="mixer")
      and not prefs.delete(a.id, "layout", scope="mixer"))
prefs.set(a.id, "zz", 1, scope="mixer")
eq("clear(scope) leaves the other scopes", (prefs.clear(a.id, "mixer"), "core" in prefs.all_prefs(a.id)), (1, True))

invalid("a bad name is refused", prefs.set, a.id, "Bad Name", 1)
invalid("a bad scope is refused", prefs.set, a.id, "x", 1, scope="../core")
invalid("a value that is not JSON is refused", prefs.set, a.id, "x", {1, 2})
invalid("NaN is not JSON", prefs.set, a.id, "x", float("nan"))
invalid("a value over the size limit is refused", prefs.set, a.id, "x", "y" * prefs.MAX_VALUE)
invalid("a user that does not exist has no preferences to set", prefs.set, 999999, "x", 1)

c0 = db.one("select count(*) as n from user_prefs where user_id = ?", (b.id,))["n"]
for i in range(prefs.MAX_PER_USER - c0):
    prefs.set(b.id, f"p{i}", i)
invalid("a person can keep only so many", prefs.set, b.id, "one_more", 1)
prefs.set(b.id, "p0", "changed")
eq("…but can still change one they have", prefs.get(b.id, "p0"), "changed")

users.delete(b.id, actor=None)
eq("deleting the user deletes their preferences",
   db.one("select count(*) as n from user_prefs where user_id = ?", (b.id,))["n"], 0)

# ------------------------------------------------------------------ the API
print("the signed-in user's API")
op_c = _db.client(app, "operator", base_url=ADMIN)
op = users.get_by_username("operator")
db.execute("delete from audit_log")

eq("starts empty", op_c.get("/api/admin/me/prefs").json(), {"prefs": {}})
r = op_c.put("/api/admin/me/prefs/core/default_page", json={"value": "/screen"})
eq("PUT sets one", (r.status_code, r.json()["value"]), (200, "/screen"))
op_c.put("/api/admin/me/prefs/core/layout", json={"value": {"columns": 3}})
eq("GET lists them by scope", op_c.get("/api/admin/me/prefs").json()["prefs"],
   {"core": {"default_page": "/screen", "layout": {"columns": 3}}})
eq("…and /me carries them, so a page has them as it loads",
   op_c.get("/api/admin/me").json()["prefs"]["core"]["default_page"], "/screen")
eq("they are stored against the person", prefs.get(op.id, "default_page"), "/screen")

eq("a body without a value is a 400", op_c.put("/api/admin/me/prefs/core/x", json={}).status_code, 400)
eq("a bad name is a 400", op_c.put("/api/admin/me/prefs/core/Bad%20Name", json={"value": 1}).status_code, 400)
eq("a scope that is neither core nor a module is a 404",
   op_c.put("/api/admin/me/prefs/nosuchmodule/x", json={"value": 1}).status_code, 404)
mid = M.module_registry.all()[0].manifest.id
eq("a module's scope is accepted", op_c.put(f"/api/admin/me/prefs/{mid}/x", json={"value": 1}).status_code, 200)

r = op_c.delete("/api/admin/me/prefs/core/layout")
eq("DELETE removes one", (r.status_code, r.json()["deleted"], prefs.get(op.id, "layout")), (200, True, None))
eq("…and says when there was none", op_c.delete("/api/admin/me/prefs/core/layout").json()["deleted"], False)

check("none of it is in the audit log", not [e for e in audit.search()["entries"] if e["actor_id"] == op.id],
      audit.search()["entries"])

eq("signed out cannot", TestClient(app, base_url=ADMIN).get("/api/admin/me/prefs").status_code, 401)
eq("a member (no operator panel) still has their own",
   _db.client(app, "member", base_url=ADMIN).put("/api/admin/me/prefs/core/language",
                                                 json={"value": "es"}).status_code, 200)

admin_c = _db.client(app, "admin", base_url=ADMIN)
secret = admin_c.post("/api/admin/me/tokens", json={"name": "Deck", "days": 30}).json()["secret"]
t = TestClient(app, base_url=ADMIN, headers={"Authorization": "Bearer " + secret})
eq("an API token reads and writes its own user's",
   (t.put("/api/admin/me/prefs/core/layout", json={"value": "wide"}).status_code,
    prefs.get(users.get_by_username("admin").id, "layout")), (200, "wide"))

if fails:
    print(f"\n{len(fails)} FAILED")
    for f in fails:
        print("  - " + f)
    sys.exit(1)
print("\nall preference checks passed")
