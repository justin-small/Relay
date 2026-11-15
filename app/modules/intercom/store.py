"""Intercom data in relay.db (#272): channels, members and the connection log.

The database is synchronous sqlite3 (app/db.py), so code on the event loop calls these through `run`, which
hands the call to a worker thread: one connection setup, one migration system and one backup path, and the loop
never blocks on the disk. Every change to channels or members runs in one transaction together with its
`intercom_events` row. Calls raise db.Unavailable while the database is down, and `Invalid` for a request the
module refuses (its text is a fixed sentence the admin page may show).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ... import db

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
MODES = ("preempt", "party", "queue")
NAME_MAX = 60
EVENTS_KEEP_DAYS = 30

_ready = False
_lock = threading.Lock()


class Invalid(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.message = text
        self.status = status


def ready() -> None:
    """Create the tables on first use (raises db.Unavailable while the database is down)."""
    global _ready
    if _ready:
        return
    with _lock:
        if not _ready:
            db.migrate("intercom", MIGRATIONS)
            _ready = True


def forget() -> None:
    """Tests: check the tables again on next use."""
    global _ready
    _ready = False


async def run(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a store call off the event loop."""
    return await asyncio.to_thread(fn, *args)


def _name(v: Any) -> str:
    text = " ".join(str(v or "").split())
    if not text:
        raise Invalid("Give the channel a name.")
    return text[:NAME_MAX]


def _int(v: Any, low: int, high: int, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise Invalid(f"{what} must be a whole number from {low} to {high}.")
    return v


def log_event(tx: db.Tx, kind: str, *, user_id: int | None = None, device_id: int | None = None,
              channel_id: int | None = None, actor_id: int | None = None, detail: dict | None = None) -> None:
    """Add a row to the connection and permission log, in the caller's transaction. `detail` is plain values."""
    tx.execute("insert into intercom_events (at, kind, user_id, device_id, channel_id, actor_id, detail)"
               " values (?, ?, ?, ?, ?, ?, ?)",
               (db.now(), kind, user_id, device_id, channel_id, actor_id, json.dumps(detail or {})))


def events(limit: int = 100) -> list[dict]:
    ready()
    return db.query("select * from intercom_events order by id desc limit ?", (max(1, min(limit, 500)),))


def prune_events(days: int = EVENTS_KEEP_DAYS, now: datetime | None = None) -> int:
    ready()
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.execute("delete from intercom_events where at < ?", (cutoff,))


# ------------------------------------------------------------------ channels and members

def channels() -> list[dict]:
    ready()
    return db.query("select * from intercom_channels order by sort, id")


def create_channel(name: Any, *, mode: str = "preempt", max_talkers: int = 1, latch_seconds: int = 0,
                   colour: str = "", actor_id: int | None = None) -> dict:
    ready()
    name = _name(name)
    if mode not in MODES:
        raise Invalid("Pick Pre-empt, Party line or One at a time.")
    max_talkers = _int(max_talkers, 1, 16, "Talkers")
    latch_seconds = _int(latch_seconds, 0, 86400, "Latch limit")
    try:
        with db.transaction() as tx:
            at = db.now()
            tx.execute("insert into intercom_channels (name, colour, sort, mode, max_talkers, latch_seconds,"
                       " created_at, updated_at) values (?, ?, (select coalesce(max(sort), 0) + 1"
                       " from intercom_channels), ?, ?, ?, ?, ?)",
                       (name, str(colour or "")[:20], mode, max_talkers, latch_seconds, at, at))
            row = tx.one("select * from intercom_channels where name = ?", (name,))
            log_event(tx, "channel_created", channel_id=row["id"], actor_id=actor_id, detail={"name": name})
            return row
    except sqlite3.IntegrityError:
        raise Invalid("A channel with that name already exists.", 409) from None


def delete_channel(channel_id: int, actor_id: int | None = None) -> bool:
    """Delete a channel with its members and choices. The log row stays, with no channel."""
    ready()
    with db.transaction() as tx:
        row = tx.one("select name from intercom_channels where id = ?", (channel_id,))
        if row is None:
            return False
        tx.execute("delete from intercom_channels where id = ?", (channel_id,))
        log_event(tx, "channel_deleted", actor_id=actor_id, detail={"name": row["name"]})
        return True


def set_member(channel_id: int, user_id: int, *, can_talk: bool = True, can_listen: bool = True, priority: int = 5,
               actor_id: int | None = None) -> None:
    """Add a user to a channel, or change what they may do there."""
    ready()
    priority = _int(priority, 1, 9, "Priority")
    if not (can_talk or can_listen):
        raise Invalid("A member must be able to talk or listen. Remove them instead.")
    try:
        with db.transaction() as tx:
            tx.execute(
                "insert into intercom_channel_members (channel_id, user_id, can_talk, can_listen, priority,"
                " added_by, added_at) values (?, ?, ?, ?, ?, ?, ?)"
                " on conflict (channel_id, user_id) do update set can_talk = excluded.can_talk,"
                " can_listen = excluded.can_listen, priority = excluded.priority",
                (channel_id, user_id, int(bool(can_talk)), int(bool(can_listen)), priority, actor_id, db.now()))
            log_event(tx, "member_set", user_id=user_id, channel_id=channel_id, actor_id=actor_id,
                      detail={"talk": bool(can_talk), "listen": bool(can_listen), "priority": priority})
    except sqlite3.IntegrityError:
        raise Invalid("That channel or user does not exist.", 404) from None


def remove_member(channel_id: int, user_id: int, actor_id: int | None = None) -> bool:
    ready()
    with db.transaction() as tx:
        gone = tx.execute("delete from intercom_channel_members where channel_id = ? and user_id = ?",
                          (channel_id, user_id))
        tx.execute("delete from intercom_user_choices where channel_id = ? and user_id = ?", (channel_id, user_id))
        if gone:
            log_event(tx, "member_removed", user_id=user_id, channel_id=channel_id, actor_id=actor_id)
        return bool(gone)


def members(channel_id: int) -> list[dict]:
    ready()
    return db.query("select * from intercom_channel_members where channel_id = ? order by priority, user_id",
                    (channel_id,))
