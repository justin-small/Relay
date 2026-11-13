"""The database: one SQLite file beside config.json (relay.db), through the standard library.

There is no server to install, start or supervise, and copying the state folder is a complete backup.
Everything here is blocking: callers on the event loop use asyncio.to_thread or a sync FastAPI dependency.

One connection serves the whole process, behind one lock, so there is one writer at a time (Relay runs one
worker by design, see run.py) and a transaction is exclusive in process; `BEGIN IMMEDIATE` makes it exclusive
against any other process too. The file is opened in WAL mode with foreign keys on and a busy timeout.

The app must start and serve viewers with the database unusable (a corrupt file, a read-only disk), so nothing
here raises at import or in `init()`. A database that cannot be opened shows up as `Unavailable` on the call
that needed it, and is re-probed later (after a short backoff, so a broken database does not cost an open
attempt on every request).

A module that wants tables of its own calls `migrate("<module id>", <its migrations dir>)`; that, `query`,
`one`, `execute` and `transaction` are the whole module-facing API. Timestamps are UTC ISO text
("2026-11-12T09:30:00Z"): `now()` and `later()` make them, and they compare correctly as text.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from . import paths

log = logging.getLogger("relay.db")

ROOT = Path(__file__).resolve().parent.parent
CORE_MIGRATIONS = Path(__file__).resolve().parent / "migrations"

MIN_SQLITE = (3, 35, 0)      # UPDATE ... RETURNING
BUSY_TIMEOUT_MS = 5000
BACKOFF = 5.0                # seconds after a failure during which calls fail at once instead of re-opening
KEEP_BACKUPS = 7
BACKUP_EVERY = timedelta(hours=24)
_BACKUP_NAME = re.compile(r"^relay-(\d{8}-\d{6})\.db$")
_NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*$")
_MIGRATION = re.compile(r"^\d{4}_[a-z0-9_]+\.sql$")
# OperationalError texts that mean the file or disk, not a bad statement.
_FILE_TROUBLE = ("unable to open", "disk i/o", "readonly", "read-only", "locked", "full", "malformed",
                 "not a database", "disk image", "corrupt", "cannot start a transaction")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_path = ""
_down_until = 0.0            # monotonic time before which a failed database is not opened again


class Unavailable(Exception):
    """The database cannot be opened or used. The text is fixed and safe to log."""


# ------------------------------------------------------------------ time

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now() -> str:
    return _iso(datetime.now(timezone.utc))


def later(seconds: float) -> str:
    """The timestamp `seconds` from now (negative for the past)."""
    return _iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


# ------------------------------------------------------------------ connection

def path() -> Path:
    env = (os.environ.get("RELAY_DATABASE") or "").strip()
    if env:
        return Path(env)
    return paths.state_dir() / "relay.db"


def configured() -> bool:
    return True              # there is always a default place for the file


def _is_file_trouble(exc: BaseException) -> bool:
    if isinstance(exc, (sqlite3.IntegrityError, sqlite3.ProgrammingError, sqlite3.DataError,
                        sqlite3.NotSupportedError)):
        return False
    if isinstance(exc, sqlite3.OperationalError):
        text = str(exc).lower()
        return any(t in text for t in _FILE_TROUBLE)
    return isinstance(exc, (sqlite3.DatabaseError, OSError))     # corruption ("not a database") and the disk


def _close_conn() -> None:
    global _conn
    conn, _conn = _conn, None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def _mark_down(exc: BaseException | None = None) -> None:
    global _down_until
    with _lock:
        _down_until = time.monotonic() + BACKOFF
        _close_conn()
    if exc is not None:
        log.warning("database unavailable: %s", str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__)


def _dict_row(cur: sqlite3.Cursor, row: tuple) -> dict:
    return {d[0]: v for d, v in zip(cur.description, row)}


def _open(p: Path) -> sqlite3.Connection:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        log.error("SQLite %s is too old: Relay needs %s or newer", sqlite3.sqlite_version,
                  ".".join(map(str, MIN_SQLITE)))
        raise Unavailable("The database is unavailable.")
    os.makedirs(p.parent, exist_ok=True)
    if not p.exists():
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT, 0o600))      # 0600 from the first byte
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass                                                        # not every file system has modes
    conn = sqlite3.connect(p, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
    try:
        conn.row_factory = _dict_row
        conn.execute(f"pragma busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("pragma foreign_keys = on")
        mode = conn.execute("pragma journal_mode = wal").fetchone()["journal_mode"]
        if mode != "wal":
            log.warning("database is not in WAL mode (got %s); this file system may not support it", mode)
        conn.execute("pragma synchronous = normal")                 # durable and corruption-proof under WAL
        conn.execute("select count(*) from sqlite_master").fetchone()   # fails now, not later, on a bad file
    except BaseException:
        conn.close()
        raise
    return conn


def _get_conn() -> sqlite3.Connection:
    """The open connection, opening it first if needed. Raises Unavailable (fast while backing off)."""
    global _conn, _conn_path
    p = path()
    with _lock:
        if _conn is not None and _conn_path == str(p):
            return _conn
        _close_conn()
        if time.monotonic() < _down_until:
            raise Unavailable("The database is unavailable.")
        try:
            _conn = _open(p)
        except Unavailable:
            _mark_down()
            raise
        except Exception as e:
            _mark_down(e)
            raise Unavailable("The database is unavailable.") from None
        _conn_path = str(p)
        return _conn


def available() -> bool:
    """True once the file has opened; after a failure it re-probes (at most once per backoff)."""
    try:
        _get_conn()
        return True
    except Unavailable:
        return False


def init() -> bool:
    """Open the file and apply the core migrations. Never raises; returns available()."""
    try:
        migrate("core", CORE_MIGRATIONS)
    except Unavailable:
        return False
    except Exception as e:
        log.error("core migrations failed: %s", e)
        _mark_down()
        return False
    return available()


def close() -> None:
    """Close the connection and forget any failure (so the next call opens afresh)."""
    global _down_until
    with _lock:
        _close_conn()
        _down_until = 0.0


@contextmanager
def _guard() -> Iterator[None]:
    try:
        yield
    except Unavailable:
        raise
    except Exception as e:
        if _is_file_trouble(e):
            _mark_down(e)
            raise Unavailable("The database is unavailable.") from None
        raise


def _rows(cur: sqlite3.Cursor) -> list[dict]:
    return cur.fetchall() if cur.description else []


def query(sql: str, params: Any = ()) -> list[dict]:
    with _guard(), _lock:
        return _rows(_get_conn().execute(sql, params))


def one(sql: str, params: Any = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Any = ()) -> int:
    with _guard(), _lock:
        return _get_conn().execute(sql, params).rowcount


class Tx:
    """What `transaction()` yields: the same three calls, all inside one transaction."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def query(self, sql: str, params: Any = ()) -> list[dict]:
        return _rows(self._conn.execute(sql, params))

    def one(self, sql: str, params: Any = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Any = ()) -> int:
        return self._conn.execute(sql, params).rowcount


@contextmanager
def transaction() -> Iterator[Tx]:
    """Commit on a clean exit, roll back on any exception.

    The write lock is taken when the transaction begins (BEGIN IMMEDIATE) and held in process too, so two
    transactions never interleave: a rule that reads and then writes (the last Owner) cannot be raced."""
    with _guard(), _lock:
        conn = _get_conn()
        conn.execute("begin immediate")
        try:
            yield Tx(conn)
        except BaseException:
            try:
                conn.execute("rollback")
            except sqlite3.Error:
                pass
            raise
        else:
            conn.execute("commit")


def _statements(sql: str) -> Iterator[str]:
    """Split a migration into statements, so they run inside the caller's transaction (executescript would
    commit it)."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf
            buf = ""
    if buf.strip() and not all(l.strip().startswith("--") or not l.strip() for l in buf.splitlines()):
        yield buf


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
        tx.execute(
            "create table if not exists schema_migrations ("
            " namespace text not null, version text not null,"
            " applied_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),"
            " primary key (namespace, version))"
        )
        done = {r["version"] for r in tx.query("select version from schema_migrations where namespace = ?",
                                               (namespace,))}
        for p in files:
            if p.stem in done:
                continue
            for stmt in _statements(p.read_text(encoding="utf-8")):
                tx.execute(stmt)
            tx.execute("insert into schema_migrations (namespace, version) values (?, ?)", (namespace, p.stem))
            log.info("applied migration %s/%s", namespace, p.stem)


# ------------------------------------------------------------------ backups

def backups_dir() -> Path:
    env = (os.environ.get("RELAY_DB_BACKUPS") or "").strip()
    if env:
        return Path(env)
    return paths.state_dir() / "backups"


def _dumps(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if _BACKUP_NAME.match(p.name))


def backup(now: datetime | None = None) -> Path | None:
    """Copy the database into the backups dir with SQLite's online backup (atomically, 0600), then keep only
    the newest KEEP_BACKUPS. The copy is one self-contained file: restoring is copying it over relay.db.

    Returns the new file, or None (with a warning logged) when the database is unavailable or the copy fails.
    Never raises for those.
    """
    now = now or datetime.now(timezone.utc)
    directory = backups_dir()
    final = directory / f"relay-{now.astimezone(timezone.utc):%Y%m%d-%H%M%S}.db"
    tmp = directory / f".{final.name}.tmp"
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        # The tmp name does not match the backup pattern, so a half-written file is never listed or restored.
        tmp.unlink(missing_ok=True)
        os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
        dst = sqlite3.connect(tmp)
        try:
            with _guard(), _lock:
                _get_conn().backup(dst)
            dst.execute("pragma journal_mode = delete")      # a single file, no -wal beside it
        finally:
            dst.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except Unavailable:
        log.warning("database backup skipped: the database is unavailable")
        _drop(tmp)
        return None
    except (OSError, sqlite3.Error) as e:
        log.warning("database backup failed: %s", e)
        _drop(tmp)
        return None
    for old in _dumps(directory)[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError as e:
            log.warning("could not remove old backup %s: %s", old.name, e)
    log.info("database backup written: %s", final.name)
    return final


def _drop(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass


def backup_due(now: datetime | None = None) -> bool:
    """True when no backup was taken in the last 24 hours."""
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
