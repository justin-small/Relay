"""Users, roles, sessions, trusted devices and the database layer, against a real throwaway Postgres.

Run: .venv/bin/python tests/test_users.py   (needs Postgres: see tests/_pg.py; RELAY_TEST_DATABASE_URL works too)
"""
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

WORK = tempfile.mkdtemp(prefix="relay-users-")
os.environ["RELAY_CONFIG"] = os.path.join(WORK, "config.json")
os.environ["RELAY_DB_BACKUPS"] = os.path.join(WORK, "backups")

import _pg  # noqa: E402

_pg.start()

from app import db, users  # noqa: E402
from app.users import Permission, Refused  # noqa: E402

users.SCRYPT_N = 2 ** 10      # fast hashing for the suite; test_hash_format checks the stored parameters

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


def refused(label, key, fn, *a, **kw):
    """fn must raise Refused with this key."""
    try:
        fn(*a, **kw)
    except Refused as e:
        eq(label, e.key, key)
        check(f"{label} has a fixed sentence", e.key in users.MESSAGES and users.MESSAGES[e.key].endswith("."))
        return
    except Exception as e:  # noqa: BLE001
        check(label, False, f"raised {type(e).__name__}: {e}")
        return
    check(label, False, "no exception")


def mk(username, role, password="password one", actor=None, **kw):
    return users.create(username, username.title(), role, password, temporary=kw.pop("temporary", False),
                        actor=actor, **kw)


def age(token, seconds):
    """Move a session's expiry `seconds` into the past (negative: future) without sleeping."""
    db.execute("update sessions set expires_at = now() - make_interval(secs => %s) where token_hash = %s",
               (seconds, users._hash_token(token)))


# ------------------------------------------------------------------ database layer
print("\ndatabase: migrations")
_pg.reset()
check("init() is true with a database", db.init())
check("available() is true", db.available())
rows = db.query("select namespace, version from schema_migrations order by version")
eq("core 0001_users applied once", rows, [{"namespace": "core", "version": "0001_users"}])
check("init() again is a no-op", db.init())
eq("still one migration row", db.one("select count(*) as n from schema_migrations")["n"], 1)
eq("migrate() twice is idempotent", (db.migrate("core", db.CORE_MIGRATIONS), db.migrate("core", db.CORE_MIGRATIONS)),
   (None, None))
eq("one migration row after re-run", db.one("select count(*) as n from schema_migrations")["n"], 1)

mdir = Path(WORK) / "mig_a"
mdir.mkdir()
(mdir / "0001_first.sql").write_text("create table modx_things (id int primary key, label text);\n"
                                     "insert into modx_things values (1, '100% real');\n")
(mdir / "0002_second.sql").write_text("alter table modx_things add column extra int default 7;\n")
(mdir / "notes.txt").write_text("ignored")
db.migrate("modx", mdir)
eq("second namespace applies its files in order", db.query("select * from modx_things"),
   [{"id": 1, "label": "100% real", "extra": 7}])
eq("versions recorded under that namespace",
   [r["version"] for r in db.query("select version from schema_migrations where namespace = 'modx' order by version")],
   ["0001_first", "0002_second"])
db.migrate("modx", mdir)
eq("second namespace is idempotent", db.one("select count(*) as n from modx_things")["n"], 1)
(mdir / "0003_third.sql").write_text("alter table modx_things add column more int default 1;\n")
db.migrate("modx", mdir)
check("a new file is picked up later", "more" in db.one("select * from modx_things"))
eq("core namespace untouched", db.one("select count(*) as n from schema_migrations where namespace = 'core'")["n"], 1)

bad = Path(WORK) / "mig_bad"
bad.mkdir()
(bad / "0001_ok.sql").write_text("create table modbad_t (id int);\n")
(bad / "0002_boom.sql").write_text("select no_such_function();\n")
try:
    db.migrate("modbad", bad)
    check("a failing migration raises", False)
except Exception as e:
    check("a failing migration raises", not isinstance(e, db.Unavailable))
eq("a failing migration rolls the whole run back",
   db.one("select to_regclass('modbad_t') as t")["t"], None)
eq("and records nothing", db.one("select count(*) as n from schema_migrations where namespace = 'modbad'")["n"], 0)
try:
    db.migrate("Bad Name", mdir)
    check("bad namespace rejected", False)
except ValueError:
    check("bad namespace rejected", True)

print("\ndatabase: query helpers")
eq("query returns dict rows", db.query("select 1 as a, 'x' as b"), [{"a": 1, "b": "x"}])
eq("one returns None for no row", db.one("select 1 where false"), None)
eq("execute returns the rowcount", db.execute("update modx_things set label = %s", ("y",)), 1)
eq("percent signs pass when there are no params", db.query("select '100%' as p"), [{"p": "100%"}])
try:
    with db.transaction() as tx:
        tx.execute("insert into modx_things (id) values (2)")
        eq("transaction sees its own writes", tx.one("select count(*) as n from modx_things")["n"], 2)
        raise RuntimeError("abort")
except RuntimeError:
    pass
eq("transaction rolls back on error", db.one("select count(*) as n from modx_things")["n"], 1)
with db.transaction() as tx:
    tx.execute("insert into modx_things (id) values (3)")
eq("transaction commits on success", db.one("select count(*) as n from modx_things")["n"], 2)

print("\ndatabase: not configured / unreachable")
real_url = os.environ["RELAY_DATABASE_URL"]
os.environ.pop("RELAY_DATABASE_URL")
db.close()
check("configured() is false without the variable", not db.configured())
check("available() is false", not db.available())
eq("init() returns False and does not raise", db.init(), False)
for label, fn in (("query", lambda: db.query("select 1")), ("one", lambda: db.one("select 1")),
                  ("execute", lambda: db.execute("select 1")), ("transaction", lambda: db.transaction().__enter__()),
                  ("migrate", lambda: db.migrate("core", db.CORE_MIGRATIONS)), ("users.count", users.count)):
    try:
        fn()
        check(f"{label} raises Unavailable when not configured", False)
    except db.Unavailable:
        check(f"{label} raises Unavailable when not configured", True)
eq("backup() is None when not configured", db.backup(), None)
check("backup_due() is false when not configured", not db.backup_due())

out = subprocess.run(
    [sys.executable, "-c", "from app import db, users; print(db.init(), db.available())"],
    cwd=ROOT, env={k: v for k, v in os.environ.items() if k != "RELAY_DATABASE_URL"},
    capture_output=True, text=True, timeout=60)
eq("import + init() with no database does not raise", (out.returncode, out.stdout.strip()), (0, "False False"))

secret = "hunter2-very-secret"
os.environ["RELAY_DATABASE_URL"] = f"postgresql://relay:{secret}@/relay?host=/nonexistent-relay-pg-dir"
db.close()
records = []


class Grab(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


h = Grab()
logging.getLogger("relay").addHandler(h)
logging.getLogger("relay").setLevel(logging.DEBUG)
t0 = time.monotonic()
eq("init() returns False for an unreachable server", db.init(), False)
check("an unreachable socket fails fast", time.monotonic() - t0 < 4, time.monotonic() - t0)
t0 = time.monotonic()
for _ in range(20):
    try:
        db.query("select 1")
    except db.Unavailable:
        pass
check("backoff: 20 calls on a down database cost no connects", time.monotonic() - t0 < 0.5, time.monotonic() - t0)
check("something was logged", bool(records))
check("the password never reaches a log line", not any(secret in r for r in records), records)
try:
    db.query("select 1")
except db.Unavailable as e:
    check("Unavailable text carries no conninfo", secret not in str(e) and "nonexistent" not in str(e))
if shutil.which("pg_dump"):
    eq("backup() is None when pg_dump cannot connect", db.backup(), None)
    bd = Path(os.environ["RELAY_DB_BACKUPS"])
    check("a failed dump leaves no file behind", not bd.exists() or not list(bd.iterdir()))
check("the password never reaches a log line (backup)", not any(secret in r for r in records), records)
logging.getLogger("relay").removeHandler(h)

os.environ["RELAY_DATABASE_URL"] = real_url
db._down_until = 0.0         # as if the backoff had elapsed: the next call re-probes without close()
check("re-probes after the backoff and recovers", db.available())
eq("calls work again", db.query("select 1 as a"), [{"a": 1}])

# ------------------------------------------------------------------ permissions
print("\npermissions")
CATALOGUE = {
    "session.view": "viewer", "session.control": "operator", "audio.manage": "admin", "targets.manage": "operator",
    "panels.view": "viewer", "panels.manage": "admin", "alerts.view": "viewer", "alerts.manage": "admin",
    "overlays.view": "viewer", "overlays.manage": "operator", "recordings.view": "operator",
    "recordings.manage": "admin", "schedules.view": "viewer", "schedules.manage": "admin",
    "blocklist.manage": "admin", "settings.view": "viewer", "settings.manage": "admin",
    "credentials.manage": "owner", "modules.view": "viewer", "modules.manage": "admin", "users.view": "admin",
    "users.manage": "admin",
}
eq("the core catalogue is exactly the contract's table",
   {p.id: p.min_role for p in users.catalogue()}, CATALOGUE)
check("every permission has a label and group", all(p.label and p.group for p in users.catalogue()))
eq("roles are ranked viewer < operator < admin < owner", users.ROLES, ("viewer", "operator", "admin", "owner"))
rank = {r: i + 1 for i, r in enumerate(users.ROLES)}
for role in users.ROLES:
    want = frozenset(pid for pid, mn in CATALOGUE.items() if rank[mn] <= rank[role])
    eq(f"role_permissions({role}) matches the table", users.role_permissions(role), want)
eq("an unknown role has no permissions", users.role_permissions("nobody"), frozenset())
u = users.User(1, "x", "X", "operator", False, False, False)
check("User.can follows the role", u.can("session.control") and not u.can("audio.manage") and not u.can("nope"))

users.register([Permission("mixer.view", "See the mixer", "Mixer", "viewer"),
                Permission("mixer.manage", "Change the mixer", "Mixer", "admin"),
                Permission("mixer.control", "Drive the mixer", "Mixer", "operator")])
eq("module permissions reach the roles",
   (users.role_permissions("viewer") >= {"mixer.view"}, "mixer.control" in users.role_permissions("viewer"),
    "mixer.control" in users.role_permissions("operator"), "mixer.manage" in users.role_permissions("operator"),
    "mixer.manage" in users.role_permissions("admin")), (True, False, True, False, True))
check("they appear in the catalogue", {"mixer.view", "mixer.manage", "mixer.control"} <= {p.id for p in users.catalogue()})
for label, perms in (
    ("a duplicate id with different content", [Permission("mixer.view", "Other", "Mixer", "admin")]),
    ("a duplicate of a core id", [Permission("settings.view", "x", "x", "owner")]),
    ("a twice-declared id in one call", [Permission("tally.view", "a", "g", "viewer"),
                                         Permission("tally.view", "b", "g", "viewer")]),
    ("an unknown role", [Permission("tally.view", "a", "g", "boss")]),
    ("an id without a module prefix", [Permission("tally", "a", "g", "viewer")]),
    ("an id with capitals", [Permission("Tally.View", "a", "g", "viewer")]),
):
    try:
        users.register(perms)
        check(f"register rejects {label}", False)
    except ValueError:
        check(f"register rejects {label}", True)
check("a rejected batch registers none of it", "tally.view" not in {p.id for p in users.catalogue()})
before = len(users.catalogue())
users.register([Permission("mixer.view", "See the mixer", "Mixer", "viewer")])
eq("re-registering an identical permission is a no-op", len(users.catalogue()), before)

# ------------------------------------------------------------------ hashing and validation
print("\nhashing")
users.SCRYPT_N = 2 ** 15
h1, h2 = users.hash_secret("correct horse"), users.hash_secret("correct horse")
parts = h1.split("$")
eq("hash format scrypt$N$r$p$salt$hash with the parameters", parts[:4], ["scrypt", "32768", "8", "1"])
check("two hashes of one password differ (random salt)", h1 != h2)
check("both verify", users.verify_secret("correct horse", h1) and users.verify_secret("correct horse", h2))
check("a wrong password does not", not users.verify_secret("correct horsf", h1))
check("the plain password is not in the hash", "correct horse" not in h1)
users.SCRYPT_N = 2 ** 10
check("a hash made with older parameters still verifies", users.verify_secret("correct horse", h1))
check("garbage hashes verify nothing", not any(users.verify_secret("x", g) for g in
                                               ("", None, "x", "scrypt$1$1$1$a$b", "scrypt$3$8$1$AA==$AA==",
                                                "md5$1$2$3$4$5", "scrypt$99999999$8$1$AA==$AA==")))

print("\nvalidation")
_pg.reset()
for bad_name in ("ab", "x" * 33, "has space", "UPPER!", "a/b", "", None, "tab\tx", "é" * 4):
    refused(f"username {bad_name!r} refused", "bad_username", users.create, bad_name, "N", "viewer", "password one")
a = users.create("  Mixed.Case_1-x  ", "  Pat Smith  ", "viewer", "password one")
eq("username is lowercased and trimmed, name trimmed", (a.username, a.name), ("mixed.case_1-x", "Pat Smith"))
eq("a new account is temporary by default", a.must_change_password, True)
refused("duplicate username (case-insensitive)", "username_taken", users.create, "MIXED.case_1-x", "N", "viewer",
        "password one")
for bad_n in ("", "   ", "x" * 81, None, "bell\x07"):
    refused(f"name {bad_n!r} refused", "bad_name", users.create, "okname", bad_n, "viewer", "password one")
users.create("n" * 3, "x" * 80, "viewer", "password one")
check("3-char username and 80-char name are fine", True)
for bad_p in ("short", "x" * 7, "x" * 201, None, 12345678):
    refused(f"password {str(bad_p)[:12]!r} refused", "bad_password", users.create, "okname", "N", "viewer", bad_p)
users.create("okname", "N", "viewer", "x" * 8)
users.create("okname2", "N", "viewer", "x" * 200)
check("8 and 200 char passwords are fine", True)
refused("unknown role refused", "bad_role", users.create, "okname3", "N", "boss", "password one")
refused("role None refused", "bad_role", users.create, "okname3", "N", None, "password one")
o = mk("pinman", "viewer")
for bad_pin in ("123", "123456789", "12ab", "", "١٢٣٤", None, 1234):
    refused(f"PIN {bad_pin!r} refused", "bad_pin", users.set_pin, o, "password one", bad_pin)
users.set_pin(o, "password one", "1234")
users.set_pin(o, "password one", "12345678")
check("4 and 8 digit PINs are fine", users.get(o.id).pin_set)
refused("PIN needs the right password", "wrong_password", users.set_pin, o, "nope nope", "1234")

# ------------------------------------------------------------------ authentication
print("\nauthenticate")
_pg.reset()
own = _pg.owner("owner", "correct horse")
eq("owner() makes an enabled owner", (own.role, own.disabled, own.must_change_password), ("owner", False, False))
eq("count()", users.count(), 1)
au = users.authenticate("owner", "correct horse")
eq("authenticate succeeds", au.id, own.id)
au = users.authenticate("  OWNER ", "correct horse")
check("username is case- and space-insensitive at sign-in", au.id == own.id)
check("last_login is stamped", users.list_users()[0]["last_login"] is not None)
refused("wrong password", "bad_login", users.authenticate, "owner", "correct horsf")
refused("unknown user is the same key", "bad_login", users.authenticate, "ghost", "correct horse")
refused("garbage username is the same key", "bad_login", users.authenticate, "!!", "correct horse")
refused("None password is the same key", "bad_login", users.authenticate, "owner", None)
refused("overlong password is the same key", "bad_login", users.authenticate, "owner", "x" * 5000)
dis = mk("sleepy", "viewer")
users.update(dis.id, disabled=True, actor=own)
refused("disabled user with the right password", "disabled", users.authenticate, "sleepy", "password one")
refused("disabled user with a wrong password reveals nothing", "bad_login", users.authenticate, "sleepy", "nope nope")
temp = users.create("newbie", "New B", "viewer", "temp pass 1", actor=own)
eq("authenticate returns must_change_password", users.authenticate("newbie", "temp pass 1").must_change_password, True)
check("password_hash is never on a User", not hasattr(au, "password_hash"))
row = [r for r in users.list_users() if r["username"] == "owner"][0]
eq("list_users keys", sorted(row), sorted(["id", "username", "name", "role", "disabled", "must_change_password",
                                           "pin_set", "last_login", "created"]))

# ------------------------------------------------------------------ actor rules
print("\nactor rules")
_pg.reset()
own = _pg.owner()
own2 = mk("owner2", "owner", actor=own)
adm = mk("admin1", "admin", actor=own)
adm2 = mk("admin2", "admin", actor=adm)
eq("admin may create an admin", adm2.role, "admin")
op = mk("op1", "operator", actor=adm)
vw = mk("view1", "viewer", actor=adm)
refused("admin cannot create an owner", "role_too_high", users.create, "owner3", "O", "owner", "password one", actor=adm)
refused("operator cannot create an admin", "role_too_high", users.create, "adm3", "O", "admin", "password one", actor=op)
refused("admin cannot grant owner by update", "role_too_high", users.update, vw.id, role="owner", actor=adm)
eq("admin can grant up to admin", users.update(vw.id, role="admin", actor=adm).role, "admin")
users.update(vw.id, role="viewer", actor=adm)
refused("admin cannot rename an owner", "account_above_you", users.update, own.id, name="Hacked", actor=adm)
refused("admin cannot disable an owner", "account_above_you", users.update, own.id, disabled=True, actor=adm)
refused("admin cannot demote an owner", "account_above_you", users.update, own.id, role="viewer", actor=adm)
refused("admin cannot delete an owner", "account_above_you", users.delete, own.id, actor=adm)
refused("admin cannot reset an owner's password", "account_above_you", users.set_password, own.id, "new password 1",
        temporary=True, actor=adm)
refused("operator cannot touch an admin", "account_above_you", users.update, adm.id, name="X", actor=op)
check("the owner's account was left alone", users.get(own.id).name == own.name and not users.get(own.id).disabled)
eq("owner can promote to owner", users.update(adm2.id, role="owner", actor=own).role, "owner")
users.update(adm2.id, role="admin", actor=own)
eq("an admin may change a peer admin", users.update(adm2.id, name="Admin Two", actor=adm).name, "Admin Two")
refused("update of a missing user", "not_found", users.update, 99999, name="x", actor=own)
refused("delete of a missing user", "not_found", users.delete, 99999, actor=own)
refused("reset of a missing user", "not_found", users.set_password, 99999, "new password 1", temporary=True, actor=own)
refused("bad role on update", "bad_role", users.update, vw.id, role="god", actor=own)
refused("bad name on update", "bad_name", users.update, vw.id, name="", actor=own)
refused("no self-disable", "self_action", users.update, adm.id, disabled=True, actor=adm)
refused("no self-delete", "self_action", users.delete, adm.id, actor=adm)
refused("an owner cannot disable themself either", "self_action", users.update, own2.id, disabled=True, actor=own2)
eq("self-demotion is allowed when another owner remains", users.update(own2.id, role="admin", actor=own2).role, "admin")
refused("self-promotion above own role is refused", "role_too_high", users.update, own2.id, role="owner",
        actor=users.get(own2.id))       # the actor is the fresh admin, as the web layer loads it per request
users.update(own2.id, role="owner", actor=own)

print("\nlast owner")
users.update(own2.id, disabled=True, actor=own)       # own is now the only enabled owner
refused("cannot demote the last enabled owner", "last_owner", users.update, own.id, role="admin", actor=own)
refused("cannot disable the last enabled owner (as another owner)", "last_owner", users.update, own.id,
        disabled=True, actor=own2)
refused("cannot delete the last enabled owner", "last_owner", users.delete, own.id, actor=own2)
refused("the system actor is bound by it too", "last_owner", users.update, own.id, role="viewer", actor=None)
users.delete(own2.id, actor=own)
check("a disabled owner may be deleted", users.get(own2.id) is None)
own2 = mk("owner2", "owner", actor=own)
users.delete(own.id, actor=own2)
check("deleting an owner works while another is enabled", users.get(own.id) is None)
own = own2
eq("renaming the last owner is fine", users.update(own.id, name="Boss", actor=own).name, "Boss")

# ------------------------------------------------------------------ passwords and sessions
print("\npasswords and sessions")
_pg.reset()
own = _pg.owner()
usr = mk("pat", "operator", "first password")
t_a, age_a = users.start_session(usr, via="password")
t_b, _ = users.start_session(usr, via="password")
t_o, _ = users.start_session(own, via="password")
eq("password sessions last 30 days", age_a, 30 * 24 * 3600)
check("tokens are random and long", t_a != t_b and len(t_a) >= 40)
check("only the sha256 is stored, not the token",
      db.one("select count(*) as n from sessions where token_hash = %s", (t_a,))["n"] == 0
      and db.one("select count(*) as n from sessions where token_hash = %s", (users._hash_token(t_a),))["n"] == 1)
eq("session_user resolves a token", users.session_user(t_a).id, usr.id)
check("garbage and empty tokens resolve to nobody",
      users.session_user("nope") is None and users.session_user("") is None and users.session_user(None) is None)
refused("own password change needs the current one", "wrong_password", users.change_own_password, usr, "nope nope",
        "second password")
refused("own password change checks the new one", "bad_password", users.change_own_password, usr, "first password", "x")
refused("a new password must differ", "same_password", users.change_own_password, usr, "first password",
        "first password")
users.change_own_password(usr, "first password", "second password", keep_token=t_a)
check("change_own_password keeps the current session", users.session_user(t_a) is not None)
check("and ends the user's other sessions", users.session_user(t_b) is None)
check("without touching anybody else's", users.session_user(t_o) is not None)
refused("old password no longer works", "bad_login", users.authenticate, "pat", "first password")
eq("new password works", users.authenticate("pat", "second password").id, usr.id)
t_c, _ = users.start_session(usr, via="password")
users.change_own_password(usr, "second password", "third password")
check("without keep_token every session of the user ends", users.session_user(t_a) is None and users.session_user(t_c) is None)

users.change_own_password(usr, "third password", "fourth password")
tmpu = users.create("tmpuser", "Tmp", "viewer", "temp pass 1", actor=own)
check("an admin-created account must change its password", tmpu.must_change_password)
users.change_own_password(tmpu, "temp pass 1", "chosen pass 1")
check("changing it clears the flag", not users.get(tmpu.id).must_change_password)

t_a, _ = users.start_session(usr, via="password")
users.set_pin(usr, "fourth password", "4321")
check("PIN is set", users.get(usr.id).pin_set)
users.set_password(usr.id, "reset password 1", temporary=True, actor=own)
got = users.get(usr.id)
check("reset sets must_change_password", got.must_change_password)
check("reset ends all the user's sessions", users.session_user(t_a) is None)
check("reset clears the PIN", not got.pin_set)
eq("the reset password works", users.authenticate("pat", "reset password 1").must_change_password, True)
users.set_password(usr.id, "plain password 1", temporary=False, actor=own)
check("a non-temporary set clears the flag", not users.get(usr.id).must_change_password)
refused("reset checks the password", "bad_password", users.set_password, usr.id, "x", temporary=True, actor=own)

print("\nsession lifetime")
t_p, age_p = users.start_session(usr, via="password")
eq("max_age for password", age_p, 2592000)
exp = db.one("select expires_at - now() as d from sessions where token_hash = %s", (users._hash_token(t_p),))["d"]
check("30 days stored", timedelta(days=29, hours=23) < exp <= timedelta(days=30), exp)
dev_tok, dev = users.trust_device("Booth iPad", own)
t_n, age_n = users.start_session(usr, via="pin", device_id=dev["id"])
eq("max_age for pin", age_n, 12 * 3600)
exp = db.one("select expires_at - now() as d from sessions where token_hash = %s", (users._hash_token(t_n),))["d"]
check("12 hours stored", timedelta(hours=11, minutes=59) < exp <= timedelta(hours=12), exp)
try:
    users.start_session(usr, via="pin")
    check("a pin session needs a device", False)
except ValueError:
    check("a pin session needs a device", True)
try:
    users.start_session(usr, via="cookie")
    check("an unknown via is a programming error", False)
except ValueError:
    check("an unknown via is a programming error", True)
age(t_p, -(29 * 24 * 3600))
check("password session alive at 29 days", users.session_user(t_p) is not None)
age(t_p, 1)
check("password session expired", users.session_user(t_p) is None)
age(t_n, 11 * 3600)
age(t_n, -(3600))          # one hour left
check("pin session alive with time left", users.session_user(t_n) is not None)
age(t_n, 1)
check("pin session expired", users.session_user(t_n) is None)
t_x, _ = users.start_session(usr, via="password")
eq("expired rows are swept when a session starts",
   db.one("select count(*) as n from sessions where token_hash = %s", (users._hash_token(t_p),))["n"], 0)

print("\nsession ending")
t_s, _ = users.start_session(usr, via="password")
stolen = t_s
users.end_session(t_s)
check("end_session ends it", users.session_user(t_s) is None)
check("a stolen copy of the token is useless afterwards", users.session_user(stolen) is None)
users.end_session("never existed")
users.end_session("")
check("ending an unknown token is harmless", True)
t1, _ = users.start_session(usr, via="password")
t2, _ = users.start_session(usr, via="password")
t3, _ = users.start_session(own, via="password")
users.end_sessions(usr.id, except_token=t1)
check("end_sessions keeps the exception", users.session_user(t1) is not None and users.session_user(t2) is None)
users.end_sessions(usr.id)
check("end_sessions ends the rest", users.session_user(t1) is None)
check("and only that user's", users.session_user(t3) is not None)

print("\ndisable and delete end sessions")
v = mk("victim", "viewer")
tv, _ = users.start_session(v, via="password")
users.update(v.id, disabled=True, actor=own)
check("disabling ends their sessions at once", users.session_user(tv) is None)
eq("the session rows are gone, not merely hidden",
   db.one("select count(*) as n from sessions where user_id = %s", (v.id,))["n"], 0)
users.update(v.id, disabled=False, actor=own)
check("enabling does not resurrect them", users.session_user(tv) is None)
tv, _ = users.start_session(v, via="password")
db.execute("update users set disabled = true where id = %s", (v.id,))      # disabled behind users.py's back
check("session_user also refuses a disabled user", users.session_user(tv) is None)
db.execute("update users set disabled = false where id = %s", (v.id,))
users.delete(v.id, actor=own)
check("deleting a user ends their sessions", users.session_user(tv) is None)
eq("and cascades the rows", db.one("select count(*) as n from sessions where user_id = %s", (v.id,))["n"], 0)

# ------------------------------------------------------------------ trusted devices and PINs
print("\ntrusted devices")
_pg.reset()
own = _pg.owner()
pat = mk("pat", "operator")
users.set_pin(pat, "password one", "2468")
eq("no devices at first", users.list_devices(), [])
tok, dev = users.trust_device("  Booth iPad ", own)
eq("trust_device returns the row", (dev["name"], dev["created_by"]), ("Booth iPad", own.name))
check("row keys", {"id", "name", "created", "created_by", "last_used"} <= set(dev))
check("only the token's sha256 is stored",
      db.one("select count(*) as n from trusted_devices where token_hash = %s", (tok,))["n"] == 0)
refused("a device needs a name", "bad_device_name", users.trust_device, "  ", own)
refused("a device name has a limit", "bad_device_name", users.trust_device, "x" * 81, own)
eq("device_for resolves the token", users.device_for(tok), {"id": dev["id"], "name": "Booth iPad"})
check("device_for notes the use", users.list_devices()[0]["last_used"] is not None)
check("device_for refuses unknown or empty tokens",
      users.device_for("nope") is None and users.device_for("") is None and users.device_for(None) is None)
eq("list_devices", [d["id"] for d in users.list_devices()], [dev["id"]])
eq("pin_users lists enabled users with a PIN", users.pin_users(), [{"id": pat.id, "name": pat.name}])

print("\nPIN sign-in")
refused("a PIN needs a device", "no_device", users.authenticate_pin, pat.id, "2468", None)
refused("a PIN needs a real device", "no_device", users.authenticate_pin, pat.id, "2468", 99999)
got = users.authenticate_pin(pat.id, "2468", dev["id"])
eq("PIN works from a trusted device", got.id, pat.id)
refused("wrong PIN", "wrong_pin", users.authenticate_pin, pat.id, "1111", dev["id"])
refused("non-string PIN is just wrong", "wrong_pin", users.authenticate_pin, pat.id, None, dev["id"])
eq("a good PIN resets the failure count", (users.authenticate_pin(pat.id, "2468", dev["id"]).id,
                                           db.one("select pin_failures as n from users where id = %s", (pat.id,))["n"]),
   (pat.id, 0))
refused("a user with no PIN", "wrong_pin", users.authenticate_pin, own.id, "2468", dev["id"])
refused("an unknown user", "wrong_pin", users.authenticate_pin, 99999, "2468", dev["id"])
for i in range(4):
    refused(f"wrong PIN #{i + 1} of 5", "wrong_pin", users.authenticate_pin, pat.id, "0000", dev["id"])
eq("four failures are counted", db.one("select pin_failures as n from users where id = %s", (pat.id,))["n"], 4)
ps, _ = users.start_session(pat, via="pin", device_id=dev["id"])
pw_sess, _ = users.start_session(pat, via="password")
refused("the 5th wrong PIN clears the PIN", "pin_cleared", users.authenticate_pin, pat.id, "0000", dev["id"])
check("the PIN is gone", not users.get(pat.id).pin_set)
eq("and the PIN picker no longer lists them", users.pin_users(), [])
refused("even the right PIN no longer works", "wrong_pin", users.authenticate_pin, pat.id, "2468", dev["id"])
check("PIN sessions end when the PIN is cleared, password ones stay",
      users.session_user(ps) is None and users.session_user(pw_sess) is not None)
users.set_pin(pat, "password one", "1357")
eq("setting a new PIN resets the count and works",
   (users.authenticate_pin(pat.id, "1357", dev["id"]).id, db.one("select pin_failures as n from users where id = %s",
                                                                 (pat.id,))["n"]), (pat.id, 0))
users.update(pat.id, disabled=True, actor=own)
refused("a disabled user cannot use a PIN", "disabled", users.authenticate_pin, pat.id, "1357", dev["id"])
eq("the PIN picker skips disabled users", users.pin_users(), [])
users.update(pat.id, disabled=False, actor=own)
users.clear_pin(pat.id)
check("clear_pin clears it", not users.get(pat.id).pin_set)

print("\nrevoking a device")
users.set_pin(pat, "password one", "1357")
tok2, dev2 = users.trust_device("Lobby", own)
s1, _ = users.start_session(pat, via="pin", device_id=dev["id"])
s2, _ = users.start_session(pat, via="pin", device_id=dev2["id"])
s3, _ = users.start_session(pat, via="password")
users.revoke_device(dev["id"])
check("revoking ends that device's PIN sessions", users.session_user(s1) is None)
check("other devices' and password sessions stay",
      users.session_user(s2) is not None and users.session_user(s3) is not None)
check("the device token stops resolving", users.device_for(tok) is None)
eq("the device is gone from the list", [d["id"] for d in users.list_devices()], [dev2["id"]])
refused("a PIN from a revoked device is refused", "no_device", users.authenticate_pin, pat.id, "1357", dev["id"])
refused("revoking twice", "not_found", users.revoke_device, dev["id"])
creator = mk("creator", "admin")
tok3, dev3 = users.trust_device("Made by creator", creator)
users.delete(creator.id, actor=own)
eq("a device outlives the admin who trusted it", [d["created_by"] for d in users.list_devices() if d["id"] == dev3["id"]],
   [None])

# ------------------------------------------------------------------ backups
print("\nbackups")
if not shutil.which("pg_dump") or not shutil.which("pg_restore"):
    print("  SKIP  !!! pg_dump / pg_restore not found on PATH: the backup checks did NOT run !!!")
else:
    bdir = Path(os.environ["RELAY_DB_BACKUPS"])
    shutil.rmtree(bdir, ignore_errors=True)
    check("a missing backups dir means a dump is due", db.backup_due())
    made = []
    for day in range(1, 10):
        made.append(db.backup(now=datetime(2026, 1, day, 12, 0, 0, tzinfo=timezone.utc)))
    check("every backup returned a path", all(isinstance(p, Path) for p in made), made)
    left = sorted(p.name for p in bdir.iterdir())
    eq("pruned to the newest 7", left, [f"relay-202601{d:02d}-120000.dump" for d in range(3, 10)])
    last = made[-1]
    eq("file mode is 0600", stat.S_IMODE(last.stat().st_mode), 0o600)
    eq("directory mode is 0700", stat.S_IMODE(bdir.stat().st_mode), 0o700)
    res = subprocess.run(["pg_restore", "--list", str(last)], capture_output=True, text=True)
    check("pg_restore --list reads the dump", res.returncode == 0, res.stderr)
    check("and it holds the user tables", "TABLE public users" in res.stdout and "schema_migrations" in res.stdout)
    check("no temp files are left", not [p for p in bdir.iterdir() if p.name.startswith(".")])
    check("an old newest dump means a backup is due", db.backup_due())
    now = datetime.now(timezone.utc)
    fresh = db.backup(now=now)
    check("a fresh dump is taken", fresh is not None and fresh.exists())
    check("then none is due", not db.backup_due())
    check("until 24 hours have passed", db.backup_due(now + timedelta(hours=24, minutes=1))
          and not db.backup_due(now + timedelta(hours=23)))
    eq("still only 7 files", len(list(bdir.glob("relay-*.dump"))), 7)
    path_was = os.environ["PATH"]
    os.environ["PATH"] = ""
    try:
        eq("backup() is None when pg_dump is missing", db.backup(), None)
    finally:
        os.environ["PATH"] = path_was
    eq("nothing new was written", len(list(bdir.glob("relay-*.dump"))), 7)

for d in ("modx_things",):
    db.execute(f"drop table if exists {d}")
shutil.rmtree(WORK, ignore_errors=True)
print()
if fails:
    print(f"{len(fails)} FAILED:")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all users tests passed")
