"""Remote crew's tables (#277): who may connect from outside, and the credentials that came of it. Its sessions are rows of `intercom_sessions` with via = 'remote' (#529).

Synchronous: call through `intercom.store.run`. Texts raised are fixed sentences.
"""
from __future__ import annotations

import threading
from pathlib import Path

from ... import db, users
from ..intercom import devices, store as istore

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_ready = False
_lock = threading.Lock()


def ready() -> None:
    global _ready
    if _ready:
        return
    istore.ready()                       # its tables first: ours point at them
    with _lock:
        if not _ready:
            db.migrate("intercom_remote", MIGRATIONS)
            _ready = True


def forget() -> None:
    global _ready
    _ready = False


def allowed(user_id: int) -> bool:
    ready()
    return db.one("select 1 as x from intercom_remote_users where user_id = ?", (user_id,)) is not None


def set_allowed(user_id: int, on: bool, actor_id: int | None = None) -> None:
    """Mark a user remote allowed, or take it away (which also signs their remote phones out)."""
    ready()
    if users.get(user_id) is None:
        raise devices.Refused("That user does not exist.", 404)
    with db.transaction() as tx:
        if on:
            tx.execute("insert into intercom_remote_users (user_id, allowed_at, allowed_by) values (?, ?, ?)"
                       " on conflict (user_id) do nothing", (user_id, db.now(), actor_id))
            istore.log_event(tx, "remote_allowed", user_id=user_id, actor_id=actor_id)
        else:
            tx.execute("delete from intercom_remote_users where user_id = ?", (user_id,))
            istore.log_event(tx, "remote_disallowed", user_id=user_id, actor_id=actor_id)


def people() -> list[dict]:
    """Every user with whether they may connect remotely and how many remote phones they have signed in."""
    ready()
    return db.query(
        "select u.id, u.name, u.username, u.role, u.disabled, (r.user_id is not null) as allowed,"
        " (select count(*) from intercom_remote_devices rd join intercom_devices d on d.id = rd.device_id"
        "   where d.user_id = u.id and d.revoked_at is null) as phones"
        " from users u left join intercom_remote_users r on r.user_id = u.id order by lower(u.name), u.id")


def is_remote_device(device_id: int) -> bool:
    ready()
    return db.one("select 1 as x from intercom_remote_devices where device_id = ?", (device_id,)) is not None


def sign_in(username, password, *, name: str = "", user_agent: str = "") -> tuple[str, dict, users.User]:
    """Check a Relay login made through the remote address and make this phone's remote credential.

    Refused with one sentence for a bad login, a disabled account, or a user not marked remote allowed: the same
    "no" for all three, so the page tells a guesser nothing."""
    ready()
    no = devices.Refused("That username and password are not allowed to connect from outside.", 403)
    try:
        user = users.authenticate(username, password)
    except users.Refused:
        raise no from None
    if not allowed(user.id) or not user.can("intercom.use"):
        raise no
    credential, row = devices.create(user.id, name=name, user_agent=user_agent, platform="web")
    with db.transaction() as tx:
        tx.execute("insert into intercom_remote_devices (device_id) values (?)", (row["id"],))
        istore.log_event(tx, "remote_signed_in", user_id=user.id, device_id=row["id"])
    return credential, row, user


def open_session(device_id: int, user_id: int) -> int:
    ready()
    with db.transaction() as tx:
        row = tx.one("insert into intercom_sessions (device_id, user_id, via, started_at) values (?, ?, 'remote', ?)"
                     " returning id", (device_id, user_id, db.now()))
        istore.log_event(tx, "remote_session_started", user_id=user_id, device_id=device_id)
        return row["id"]


def close_session(session_id: int, device_id: int, user_id: int, reason: str) -> None:
    ready()
    with db.transaction() as tx:
        tx.execute("update intercom_sessions set ended_at = ?, end_reason = ? where id = ? and via = 'remote'",
                   (db.now(), reason, session_id))
        istore.log_event(tx, "remote_session_ended", user_id=user_id, device_id=device_id,
                         detail={"reason": reason})


def sessions(limit: int = 50) -> list[dict]:
    ready()
    return db.query(
        "select s.id, s.user_id, u.name as user_name, s.started_at, s.ended_at, s.end_reason"
        " from intercom_sessions s join users u on u.id = s.user_id where s.via = 'remote' order by s.id desc limit ?",
        (max(1, min(limit, 200)),))


def revoke_user_phones(user_id: int, actor_id: int | None = None) -> list[int]:
    """Sign out every remote phone of a user; returns the device ids so their sockets can be cut."""
    ready()
    ids = [r["device_id"] for r in db.query(
        "select rd.device_id from intercom_remote_devices rd join intercom_devices d on d.id = rd.device_id"
        " where d.user_id = ? and d.revoked_at is null", (user_id,))]
    for device_id in ids:
        devices.revoke(device_id, actor_id=actor_id)
    return ids
