"""A throwaway SQLite file for tests. Import this BEFORE app.main, then call start():

    sys.path.insert(0, str(Path(__file__).resolve().parent)); import _db; _db.start()

start() points RELAY_DATABASE at a fresh file in a temp dir (removed at exit). Each test process gets its own
file, so test files can run one after another (or at once). Nothing needs installing.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

_started = False
_root = ""


def _cleanup() -> None:
    try:
        from app import db
        db.close()
    except Exception:
        pass
    shutil.rmtree(_root, ignore_errors=True)


def start() -> str:
    """Give this process a throwaway database and set RELAY_DATABASE. Safe to call twice."""
    global _started, _root
    if _started:
        return os.environ["RELAY_DATABASE"]
    _root = tempfile.mkdtemp(prefix="relay-db-")
    atexit.register(_cleanup)
    os.environ["RELAY_DATABASE"] = os.path.join(_root, "relay.db")
    _started = True
    return os.environ["RELAY_DATABASE"]


TABLES = ("sessions", "trusted_devices", "users")


def reset() -> None:
    """Empty the user-system tables (ids restart) so a test begins clean. Applies migrations if needed."""
    from app import db
    if not db.init():
        raise RuntimeError("test database is not available")
    with db.transaction() as tx:
        for t in TABLES:
            tx.execute(f"delete from {t}")
        tx.execute("delete from sqlite_sequence where name in (%s)" % ", ".join("?" * len(TABLES)), TABLES)
    main = sys.modules.get("app.main")
    if main is not None:               # the web layer remembers that accounts exist; they do not now
        main.forget_users_cache()


def owner(username: str = "owner", password: str = "correct horse"):
    """Create an Owner through app.users and return the User."""
    from app import users
    return users.create(username, username.title(), "owner", password, temporary=False)


def client(app, role: str = "owner", base_url: str = "http://relay.local:8001", username: str | None = None,
           password: str = "correct horse", **kwargs):
    """A TestClient signed in (through POST /admin/login) as a user of `role`, created if need be.

    The username defaults to the role name. A user that exists with another role is moved to `role`, and a
    pending password change is cleared, so the same helper can be called again with the same name."""
    from fastapi.testclient import TestClient

    from app import users
    if users.SCRYPT_N > 2 ** 10:           # fast hashes for tests; stored hashes carry their own cost
        users.SCRYPT_N = 2 ** 10
    username = username or role
    have = users.get_by_username(username)
    if have is None:
        users.create(username, username.title(), role, password, temporary=False)
    else:
        if have.role != role or have.disabled:
            users.update(have.id, role=role, disabled=False, actor=None)
        try:
            users.authenticate(username, password)
            if users.get(have.id).must_change_password:
                raise users.Refused("bad_login")
        except users.Refused:              # a changed or temporary password: put it back, ending old sessions
            users.set_password(have.id, password, temporary=False)
    c = TestClient(app, base_url=base_url, **kwargs)
    r = c.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False)
    if r.status_code != 303:
        raise RuntimeError(f"test sign-in as {username} failed: {r.status_code}")
    return c
