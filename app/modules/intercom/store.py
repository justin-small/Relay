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

from ... import channels as core_channels, db

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
MODES = ("preempt", "party", "queue")
NAME_MAX = 60
EVENTS_KEEP_DAYS = 30

_ready = False
_lock = threading.Lock()

# Called as hook(user_id, channel_id) after a member's rights on a channel changed or went (user_id None: the
# channel itself went). The intercom's live side holds one, so a removed member leaves their room at once.
member_hooks: list[Callable[[int | None, int], None]] = []


# Called as hook(user_ids) after a change to what phones are shown (channels, order, members, the label):
# user_ids is a set of user ids, or None for every connected phone.
config_hooks: list[Callable[[set[int] | None], None]] = []


def _config_changed(users: set[int] | None) -> None:
    for hook in list(config_hooks):
        try:
            hook(users)
        except Exception:
            pass


def _changed(user_id: int | None, channel_id: int) -> None:
    for hook in list(member_hooks):
        try:
            hook(user_id, channel_id)
        except Exception:      # live cleanup must never undo a saved change
            pass


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


def events_since(since: str, limit: int = 5000) -> list[dict]:
    """The connection log from `since` (UTC ISO) on, newest first, with the names the page and the export show."""
    ready()
    return db.query(
        "select e.id, e.at, e.kind, e.detail, e.user_id, u.name as user, d.name as device, c.name as channel,"
        " a.name as actor from intercom_events e left join users u on u.id = e.user_id"
        " left join intercom_devices d on d.id = e.device_id left join intercom_channels c on c.id = e.channel_id"
        " left join users a on a.id = e.actor_id where e.at >= ? order by e.id desc limit ?",
        (since, max(1, min(limit, 50000))))


def prune_events(days: int = EVENTS_KEEP_DAYS, now: datetime | None = None) -> int:
    ready()
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.execute("delete from intercom_events where at < ?", (cutoff,))


# ------------------------------------------------------------------ channels and members

_reported: tuple = ()


def _report(rows: list[dict]) -> None:
    """Each intercom channel is a core channel of kind talk (#542), so a rundown or a console can pick it by ref. The
    intercom keeps its talk settings (mode, talkers, latch) on its own table; core keeps the name everyone shows."""
    global _reported
    sig = tuple((r["id"], r["name"]) for r in rows)
    if sig == _reported:
        return
    found = [{"key": f"ch{r['id']}", "kind": "talk", "direction": "source", "number": r["id"], "gear_name": r["name"]}
             for r in rows]
    if core_channels.report("intercom", found) is not None:
        _reported = sig


def channels() -> list[dict]:
    ready()
    rows = db.query("select * from intercom_channels order by sort, id")
    _report(rows)
    return rows


def channel(channel_id: int) -> dict | None:
    ready()
    return db.one("select * from intercom_channels where id = ?", (channel_id,))


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
    _changed(None, channel_id)
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
    _changed(user_id, channel_id)
    _config_changed({user_id})


def remove_member(channel_id: int, user_id: int, actor_id: int | None = None) -> bool:
    ready()
    with db.transaction() as tx:
        gone = tx.execute("delete from intercom_channel_members where channel_id = ? and user_id = ?",
                          (channel_id, user_id))
        tx.execute("delete from intercom_user_choices where channel_id = ? and user_id = ?", (channel_id, user_id))
        if gone:
            log_event(tx, "member_removed", user_id=user_id, channel_id=channel_id, actor_id=actor_id)
    if gone:
        _changed(user_id, channel_id)
        _config_changed({user_id})
    return bool(gone)


def members(channel_id: int) -> list[dict]:
    ready()
    return db.query("select * from intercom_channel_members where channel_id = ? order by priority, user_id",
                    (channel_id,))


# ------------------------------------------------------------------ the channels page (#275)

def channel_view(channel_id: int) -> dict | None:
    """A channel with its members (and their names), for the Edit panel. `version` is what a save must send back."""
    ready()
    ch = db.one("select * from intercom_channels where id = ?", (channel_id,))
    if ch is None:
        return None
    ch["members"] = db.query(
        "select m.user_id, u.name, u.username, m.can_talk, m.can_listen, m.priority from intercom_channel_members m"
        " join users u on u.id = m.user_id where m.channel_id = ? order by lower(u.name), u.id", (channel_id,))
    return ch


def channel_table() -> list[dict]:
    ready()
    return db.query(
        "select c.*, (select count(*) from intercom_channel_members m where m.channel_id = c.id) as members"
        " from intercom_channels c order by c.sort, c.id")


def _settings(name: Any, mode: Any, max_talkers: Any, latch_seconds: Any, colour: Any) -> tuple:
    if mode not in MODES:
        raise Invalid("Pick Pre-empt, Party line or One at a time.")
    return (_name(name), str(colour or "")[:20], mode, _int(max_talkers, 1, 16, "Talkers"),
            _int(latch_seconds, 0, 86400, "Latch limit"))


def new_channel(name: Any, *, colour: Any = "", mode: Any = "party", max_talkers: Any = None,
                latch_seconds: Any = 60, actor_id: int | None = None) -> dict:
    """A channel from the New channel form. Defaults: Party line, 8 talkers (1 for One at a time), 60 s latch."""
    if max_talkers is None:
        max_talkers = 1 if mode == "queue" else 8
    _settings(name, mode, max_talkers, latch_seconds, colour)
    row = create_channel(name, mode=mode, max_talkers=max_talkers, latch_seconds=latch_seconds,
                         colour=colour, actor_id=actor_id)
    _config_changed(set())
    return row


def update_channel(channel_id: int, version: Any, *, name: Any, colour: Any, mode: Any, max_talkers: Any,
                   latch_seconds: Any, members: Any, actor_id: int | None = None) -> dict:
    """Save a channel's settings and its whole member list in one transaction.

    `version` is the version the editor loaded; a different one means someone else saved meanwhile and nothing is
    written. One `permission_change` event is logged per member added, changed or removed."""
    ready()
    name, colour, mode, max_talkers, latch_seconds = _settings(name, mode, max_talkers, latch_seconds, colour)
    if not isinstance(members, list):
        raise Invalid("The member list is missing.")
    want: dict[int, tuple[int, int, int]] = {}
    for m in members:
        if not isinstance(m, dict) or isinstance(m.get("user_id"), bool) or not isinstance(m.get("user_id"), int):
            raise Invalid("A member is missing.")
        talk, listen = bool(m.get("can_talk")), bool(m.get("can_listen"))
        if not (talk or listen):
            raise Invalid("A member must be able to talk or listen. Remove them instead.")
        want[m["user_id"]] = (int(talk), int(listen), _int(m.get("priority", 5), 1, 9, "Priority"))
    touched: set[int] = set()
    try:
        with db.transaction() as tx:
            cur = tx.one("select * from intercom_channels where id = ?", (channel_id,))
            if cur is None:
                raise Invalid("That channel does not exist.", 404)
            if version != cur["version"]:
                raise Invalid("This channel was changed by someone else. Reload it and try again.", 409)
            tx.execute("update intercom_channels set name = ?, colour = ?, mode = ?, max_talkers = ?,"
                       " latch_seconds = ?, updated_at = ?, version = version + 1 where id = ?",
                       (name, colour, mode, max_talkers, latch_seconds, db.now(), channel_id))
            have = {r["user_id"]: (r["can_talk"], r["can_listen"], r["priority"]) for r in
                    tx.query("select user_id, can_talk, can_listen, priority from intercom_channel_members"
                             " where channel_id = ?", (channel_id,))}
            for uid in have.keys() - want.keys():
                tx.execute("delete from intercom_channel_members where channel_id = ? and user_id = ?",
                           (channel_id, uid))
                tx.execute("delete from intercom_user_choices where channel_id = ? and user_id = ?",
                           (channel_id, uid))
                log_event(tx, "permission_change", user_id=uid, channel_id=channel_id, actor_id=actor_id,
                          detail={"removed": True})
                touched.add(uid)
            for uid, (talk, listen, prio) in want.items():
                if have.get(uid) == (talk, listen, prio):
                    continue
                tx.execute(
                    "insert into intercom_channel_members (channel_id, user_id, can_talk, can_listen, priority,"
                    " added_by, added_at) values (?, ?, ?, ?, ?, ?, ?)"
                    " on conflict (channel_id, user_id) do update set can_talk = excluded.can_talk,"
                    " can_listen = excluded.can_listen, priority = excluded.priority",
                    (channel_id, uid, talk, listen, prio, actor_id, db.now()))
                log_event(tx, "permission_change", user_id=uid, channel_id=channel_id, actor_id=actor_id,
                          detail={"talk": bool(talk), "listen": bool(listen), "priority": prio,
                                  "added": uid not in have})
                touched.add(uid)
            settings_changed = (cur["name"], cur["colour"], cur["mode"], cur["max_talkers"],
                                cur["latch_seconds"]) != (name, colour, mode, max_talkers, latch_seconds)
            if settings_changed:
                log_event(tx, "channel_changed", channel_id=channel_id, actor_id=actor_id, detail={"name": name})
    except sqlite3.IntegrityError:
        raise Invalid("A channel with that name already exists, or a member no longer exists.", 409) from None
    for uid in touched:
        _changed(uid, channel_id)
    _config_changed(touched | {r["user_id"] for r in members_of(channel_id)} if settings_changed else touched)
    return channel_view(channel_id)  # type: ignore[return-value]


def members_of(channel_id: int) -> list[dict]:
    return db.query("select user_id from intercom_channel_members where channel_id = ?", (channel_id,))


def reorder_channels(ids: Any, actor_id: int | None = None) -> None:
    """Set the order phones show channels in. `ids` must be every channel, once."""
    ready()
    have = [r["id"] for r in db.query("select id from intercom_channels")]
    if not isinstance(ids, list) or sorted(ids) != sorted(have):
        raise Invalid("The channel list is out of date. Reload it.", 409)
    with db.transaction() as tx:
        for pos, cid in enumerate(ids, 1):
            tx.execute("update intercom_channels set sort = ? where id = ?", (pos, cid))
        log_event(tx, "channels_reordered", actor_id=actor_id)
    _config_changed(None)


def remove_channel(channel_id: int, actor_id: int | None = None) -> bool:
    """Delete a channel (its members and choices go with it) and tell the phones that were on it."""
    ready()
    users = {r["user_id"] for r in members_of(channel_id)}
    gone = delete_channel(channel_id, actor_id)
    if gone:
        _config_changed(users)
    return gone


def production() -> str:
    ready()
    return (db.one("select label from intercom_production where org_id = 1") or {}).get("label", "")


def set_production(label: Any, actor_id: int | None = None) -> str:
    ready()
    text = " ".join(str(label or "").split())[:80]
    with db.transaction() as tx:
        tx.execute("insert into intercom_production (org_id, label, set_by, set_at) values (1, ?, ?, ?)"
                   " on conflict (org_id) do update set label = excluded.label, set_by = excluded.set_by,"
                   " set_at = excluded.set_at", (text, actor_id, db.now()))
        log_event(tx, "production_label", actor_id=actor_id, detail={"label": text})
    _config_changed(None)
    return text
