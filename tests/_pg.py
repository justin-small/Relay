"""A throwaway Postgres for tests. Import this BEFORE app.main, then call start():

    sys.path.insert(0, str(Path(__file__).resolve().parent)); import _pg; _pg.start()

start() sets RELAY_DATABASE_URL, in order of preference:
  1. RELAY_TEST_DATABASE_URL: a server you already run; a fresh uniquely named database is created on it and
     dropped at exit;
  2. a private cluster built with initdb in a short temp dir, on a unix socket only, fsync off, stopped and
     deleted at exit (about 2 s);
  3. neither: prints how to get Postgres and exits with status 2.

Each test process gets its own database, so test files can run one after another (or at once).
"""
from __future__ import annotations

import atexit
import glob
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile

_started = False
_cleanups: list = []

_BIN_DIRS = ["/usr/lib/postgresql/*/bin", "/opt/homebrew/bin", "/opt/homebrew/opt/postgresql@*/bin",
             "/usr/local/bin"]


def _find(tool: str) -> str | None:
    found = shutil.which(tool)
    if found:
        return found
    for pattern in _BIN_DIRS:
        for d in sorted(glob.glob(pattern), reverse=True):       # newest version first
            p = os.path.join(d, tool)
            if os.access(p, os.X_OK):
                return p
    return None


def _cleanup() -> None:
    try:
        from app import db
        db.close()
    except Exception:
        pass
    while _cleanups:
        try:
            _cleanups.pop()()
        except Exception:
            pass


def _no_postgres() -> None:
    print("tests need PostgreSQL: install it (macOS: brew install postgresql@17; Debian/Ubuntu: apt install "
          "postgresql) or point RELAY_TEST_DATABASE_URL at a server you can create databases on.")
    sys.exit(2)


def _use_server(base_url: str) -> str:
    import psycopg
    from psycopg.conninfo import make_conninfo
    name = f"relay_test_{os.getpid()}_{secrets.token_hex(3)}"
    admin = psycopg.connect(base_url, autocommit=True, connect_timeout=5)
    admin.execute(f'create database "{name}"')
    admin.close()

    def drop():
        from app import db
        db.close()
        with psycopg.connect(base_url, autocommit=True, connect_timeout=5) as c:
            c.execute(f'drop database if exists "{name}" with (force)')
    _cleanups.append(drop)
    return make_conninfo(base_url, dbname=name)


def _own_cluster() -> str:
    initdb, pg_ctl = _find("initdb"), _find("pg_ctl")
    if not initdb or not pg_ctl:
        _no_postgres()
    # Short path under /tmp: unix socket paths top out near 100 characters ($TMPDIR on macOS is too long).
    root = tempfile.mkdtemp(prefix="rpg", dir="/tmp")
    data = os.path.join(root, "d")
    # LC_ALL=C: on macOS a stray locale makes postgres refuse to start ("became multithreaded during startup").
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE,
             "env": {**os.environ, "LC_ALL": "C"}}
    r = subprocess.run([initdb, "-D", data, "-U", "relay", "-A", "trust", "-E", "UTF8", "--no-locale", "-N"],
                       **quiet)
    if r.returncode != 0:
        shutil.rmtree(root, ignore_errors=True)
        sys.exit("initdb failed: " + r.stderr.decode(errors="replace")[-400:])
    opts = ("-c listen_addresses= -c unix_socket_directories=" + root + " -c fsync=off -c synchronous_commit=off"
            " -c full_page_writes=off -c shared_buffers=16MB -c max_connections=40 -c log_min_messages=fatal")

    def stop():
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "-w", "-t", "20", "stop"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env={**os.environ, "LC_ALL": "C"})
        shutil.rmtree(root, ignore_errors=True)
    _cleanups.append(stop)
    r = subprocess.run([pg_ctl, "-D", data, "-o", opts, "-l", os.path.join(root, "log"), "-w", "-t", "30",
                        "start"], **quiet)
    if r.returncode != 0:
        log = ""
        try:
            log = open(os.path.join(root, "log"), errors="replace").read()[-400:]
        except OSError:
            pass
        _cleanup()
        sys.exit("postgres did not start: " + r.stderr.decode(errors="replace")[-300:] + log)
    import psycopg
    with psycopg.connect(f"host={root} user=relay dbname=postgres", autocommit=True) as c:
        c.execute("create database relay")
    return f"postgresql://relay@/relay?host={root}"


def start() -> str:
    """Give this process a throwaway database and set RELAY_DATABASE_URL. Safe to call twice."""
    global _started
    if _started:
        return os.environ["RELAY_DATABASE_URL"]
    base = (os.environ.get("RELAY_TEST_DATABASE_URL") or "").strip()
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("tests need psycopg: .venv/bin/pip install -r requirements.txt")
        sys.exit(2)
    atexit.register(_cleanup)
    # atexit does not run on SIGTERM; turn it into a normal exit so the cluster is not left running.
    if signal.getsignal(signal.SIGTERM) in (signal.SIG_DFL, None):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    url = _use_server(base) if base else _own_cluster()
    os.environ["RELAY_DATABASE_URL"] = url
    _started = True
    return url


TABLES = "sessions, trusted_devices, users"


def reset() -> None:
    """Empty the user-system tables (ids restart) so a test begins clean. Applies migrations if needed."""
    from app import db
    if not db.init():
        raise RuntimeError("test database is not available")
    db.execute(f"truncate {TABLES} restart identity cascade")
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
