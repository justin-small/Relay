"""The database: one Postgres, reached over psycopg 3 with a small pool.

Postgres runs beside the app (a unix socket inside the container, see RELAY_DATABASE_URL). Everything here is
blocking: callers on the event loop use asyncio.to_thread or a sync FastAPI dependency.

The app must start and serve viewers with the database down, so nothing here raises at import or in `init()`.
A missing or unreachable database shows up as `Unavailable` on the call that needed it, and is re-probed later
(after a short backoff, so a dead database does not add a connect timeout to every request).

A module that wants tables of its own calls `migrate("<module id>", <its migrations dir>)`; that, `query`,
`one`, `execute` and `transaction` are the whole module-facing API.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg import errors as pgerrors
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout

log = logging.getLogger("relay.db")

ROOT = Path(__file__).resolve().parent.parent
CORE_MIGRATIONS = Path(__file__).resolve().parent / "migrations"

CONNECT_TIMEOUT = 3          # seconds to reach the server
POOL_TIMEOUT = 3             # seconds to wait for a pooled connection
BACKOFF = 5.0                # seconds after a failure during which calls fail at once instead of re-probing
KEEP_BACKUPS = 7
BACKUP_EVERY = timedelta(hours=24)
BACKUP_TIMEOUT = 600
_MIGRATE_LOCK_KEY = 727274   # advisory lock so two processes never migrate at once
_BACKUP_NAME = re.compile(r"^relay-(\d{8}-\d{6})\.dump$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_MIGRATION = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")

_lock = threading.RLock()
_pool: ConnectionPool | None = None
_pool_url = ""
_down_until = 0.0            # monotonic time before which a failed database is not probed again


class Unavailable(Exception):
    """The database is not configured or cannot be reached. The text is fixed and safe to log."""


def _url() -> str:
    return (os.environ.get("RELAY_DATABASE_URL") or "").strip()


def configured() -> bool:
    return bool(_url())


def _scrub(text: object) -> str:
    """A log-safe string: the conninfo's password never appears, whatever libpq echoes back."""
    s = str(text)
    url = _url()
    try:
        pw = conninfo_to_dict(url).get("password") if url else None
    except Exception:
        pw = None
    if pw:
        s = s.replace(str(pw), "***")
    return s


def _is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (PoolTimeout, PoolClosed, psycopg.InterfaceError)):
        return True
    if isinstance(exc, pgerrors.TransactionRollback):
        return False       # a deadlock or serialization failure: the server is fine
    if isinstance(exc, psycopg.OperationalError):
        state = getattr(exc, "sqlstate", None)
        return state is None or state.startswith(("08", "57P", "53"))
    return False


def _discard_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.close(timeout=1)
        except Exception:
            pass


def _mark_down(exc: BaseException | None = None) -> None:
    global _down_until
    with _lock:
        _down_until = time.monotonic() + BACKOFF
        _discard_pool()
    if exc is not None:
        log.warning("database unavailable: %s", _scrub(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__)


def _get_pool() -> ConnectionPool:
    """The open pool, connecting first if needed. Raises Unavailable (fast while backing off)."""
    global _pool, _pool_url
    url = _url()
    if not url:
        raise Unavailable("The database is not configured.")
    with _lock:
        if _pool is not None and _pool_url == url:
            return _pool
        if _pool is not None:
            _discard_pool()
        if time.monotonic() < _down_until:
            raise Unavailable("The database is unavailable.")
        pool = None
        try:
            # A single plain connect first: a dead server fails here in milliseconds, where an opening pool
            # would sit out its whole wait.
            with psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT):
                pass
            pool = ConnectionPool(
                url, min_size=1, max_size=4, open=False, timeout=POOL_TIMEOUT,
                kwargs={"autocommit": True, "row_factory": dict_row, "connect_timeout": CONNECT_TIMEOUT},
                check=ConnectionPool.check_connection,
            )
            pool.open(wait=True, timeout=CONNECT_TIMEOUT + 2)
        except Exception as e:
            if pool is not None:
                try:
                    pool.close(timeout=1)
                except Exception:
                    pass
            _mark_down(e)
            raise Unavailable("The database is unavailable.") from None
        _pool, _pool_url = pool, url
        return pool


def available() -> bool:
    """True once a connection has worked; after a failure it re-probes (at most once per backoff)."""
    try:
        _get_pool()
        return True
    except Unavailable:
        return False


def init() -> bool:
    """Open the pool and apply the core migrations. Never raises; returns available()."""
    if not configured():
        log.info("database not configured (RELAY_DATABASE_URL is unset)")
        return False
    try:
        migrate("core", CORE_MIGRATIONS)
    except Unavailable:
        return False
    except Exception as e:
        log.error("core migrations failed: %s", _scrub(e))
        _mark_down()
        return False
    return available()


def close() -> None:
    """Close the pool and forget any failure (so the next call connects afresh)."""
    global _down_until
    with _lock:
        _discard_pool()
        _down_until = 0.0


@contextmanager
def _guard() -> Iterator[None]:
    try:
        yield
    except Unavailable:
        raise
    except Exception as e:
        if _is_connection_error(e):
            _mark_down(e)
            raise Unavailable("The database is unavailable.") from None
        raise


def _rows(cur) -> list[dict]:
    return cur.fetchall() if cur.description else []


def query(sql: str, params: Any = ()) -> list[dict]:
    with _guard(), _get_pool().connection() as conn:
        return _rows(conn.execute(sql, params or None))


def one(sql: str, params: Any = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = ()) -> int:
    with _guard(), _get_pool().connection() as conn:
        return conn.execute(sql, params or None).rowcount


class Tx:
    """What `transaction()` yields: the same three calls, all inside one transaction."""

    def __init__(self, conn):
        self._conn = conn

    def query(self, sql: str, params: Any = ()) -> list[dict]:
        return _rows(self._conn.execute(sql, params or None))

    def one(self, sql: str, params: Any = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Any = ()) -> int:
        return self._conn.execute(sql, params or None).rowcount


@contextmanager
def transaction() -> Iterator[Tx]:
    """Commit on a clean exit, roll back on any exception."""
    with _guard(), _get_pool().connection() as conn, conn.transaction():
        yield Tx(conn)


def migrate(namespace: str, directory: Path) -> None:
    """Apply NNNN_name.sql files from `directory` in order, once each, all in one transaction.

    Applied versions are recorded in schema_migrations under `namespace`, so a module's tables never collide
    with core's. A failing file rolls the whole run back.
    """
    if not _NAMESPACE.match(namespace or ""):
        raise ValueError("bad migration namespace")
    directory = Path(directory)
    files = sorted(p for p in directory.glob("*.sql") if _MIGRATION.match(p.name)) if directory.is_dir() else []
    with transaction() as tx:
        tx.execute("select pg_advisory_xact_lock(%s)", (_MIGRATE_LOCK_KEY,))
        tx.execute(
            "create table if not exists schema_migrations ("
            " namespace text not null, version text not null,"
            " applied_at timestamptz not null default now(),"
            " primary key (namespace, version))"
        )
        done = {r["version"] for r in tx.query("select version from schema_migrations where namespace = %s",
                                               (namespace,))}
        for path in files:
            if path.stem in done:
                continue
            tx.execute(path.read_text(encoding="utf-8"))
            tx.execute("insert into schema_migrations (namespace, version) values (%s, %s)", (namespace, path.stem))
            log.info("applied migration %s/%s", namespace, path.stem)


# ------------------------------------------------------------------ backups

def backups_dir() -> Path:
    env = (os.environ.get("RELAY_DB_BACKUPS") or "").strip()
    if env:
        return Path(env)
    return Path(os.environ.get("RELAY_CONFIG") or ROOT / "config.json").parent / "backups"


def _dumps(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if _BACKUP_NAME.match(p.name))


def backup(now: datetime | None = None) -> Path | None:
    """pg_dump -Fc into the backups dir (atomically, 0600), then keep only the newest KEEP_BACKUPS.

    Returns the new file, or None (with a warning logged) when there is nothing to dump or pg_dump is missing
    or fails. Never raises for those.
    """
    url = _url()
    if not url:
        return None
    exe = shutil.which("pg_dump")
    if not exe:
        log.warning("database backup skipped: pg_dump was not found on PATH")
        return None
    now = now or datetime.now(timezone.utc)
    directory = backups_dir()
    final = directory / f"relay-{now.astimezone(timezone.utc):%Y%m%d-%H%M%S}.dump"
    tmp = directory / f".{final.name}.tmp"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        # The tmp name does not match the dump pattern, so a half-written file is never listed or restored.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        res = subprocess.run([exe, "-Fc", "--dbname", url, "-f", str(tmp)], capture_output=True, text=True,
                             timeout=BACKUP_TIMEOUT, stdin=subprocess.DEVNULL)
        if res.returncode != 0:
            last = (_scrub(res.stderr).strip().splitlines() or ["no error text"])[-1][:200]
            log.warning("database backup failed (pg_dump exit %s): %s", res.returncode, last)
            tmp.unlink(missing_ok=True)
            return None
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("database backup failed: %s", _scrub(e))
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    for old in _dumps(directory)[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError as e:
            log.warning("could not remove old backup %s: %s", old.name, e)
    log.info("database backup written: %s", final.name)
    return final


def backup_due(now: datetime | None = None) -> bool:
    """True when the database is configured and no dump was taken in the last 24 hours."""
    if not configured():
        return False
    dumps = _dumps(backups_dir())
    if not dumps:
        return True
    now = now or datetime.now(timezone.utc)
    m = _BACKUP_NAME.match(dumps[-1].name)
    try:
        taken = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        taken = datetime.fromtimestamp(dumps[-1].stat().st_mtime, timezone.utc)
    return now - taken >= BACKUP_EVERY
