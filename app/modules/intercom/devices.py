"""Signed-in phones and their device credentials (#273).

A phone is one `intercom_devices` row per user per phone. Its credential is 32 random bytes (base64url) shown once,
kept on the phone and stored here only as a SHA-256 (the credential is random, so a fast hash is enough). It
rotates after 30 days of use: the phone gets a new one over the socket and the old hash stays valid for 10 minutes
(`previous_hash`), so a reply that never arrived cannot lock it out. A phone unused for 30 days expires.
Synchronous: call through `store.run`. Texts raised are fixed sentences.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import Any

from ... import db, users
from . import store

ROTATE_DAYS = 30
EXPIRE_DAYS = 30
GRACE_S = 600
NAME_MAX = 60
UA_MAX = 200
PLATFORMS = ("web", "ios", "android")
DAY = 86400


class Refused(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.message = text
        self.status = status


def _hash(credential: str) -> str:
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


def _new_credential() -> str:
    return secrets.token_urlsafe(32)


def default_name(user_agent: str) -> str:
    ua = user_agent.lower()
    for needle, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android phone")):
        if needle in ua:
            return name
    return "Browser"


def create(user_id: int, *, name: str = "", user_agent: str = "", platform: str = "web") -> tuple[str, dict]:
    """Make a device for `user_id`. Returns (credential, row); the credential is not kept and not shown again."""
    store.ready()
    user_agent = " ".join(str(user_agent or "").split())[:UA_MAX]
    name = " ".join(str(name or "").split())[:NAME_MAX] or default_name(user_agent)
    platform = platform if platform in PLATFORMS else "web"
    credential = _new_credential()
    with db.transaction() as tx:
        row = tx.one("insert into intercom_devices (user_id, name, platform, user_agent, credential_hash, issued_at,"
                     " rotates_at, last_seen_at) values (?, ?, ?, ?, ?, ?, ?, ?) returning *",
                     (user_id, name, platform, user_agent, _hash(credential), db.now(),
                      db.later(ROTATE_DAYS * DAY), db.now()))
        store.log_event(tx, "device_signed_in", user_id=user_id, device_id=row["id"], detail={"platform": platform})
    return credential, row


def authenticate(credential: Any) -> tuple[dict, users.User, str | None] | None:
    """The device, its user and a new credential if this connect rotated it; None for anything not valid.

    Not valid: unknown, revoked, unused for 30 days, or the user is turned off. A credential that was replaced less
    than 10 minutes ago still works, and is replaced again so the phone ends up holding a live one."""
    if not isinstance(credential, str) or not 20 <= len(credential) <= 200:
        return None
    store.ready()
    h = _hash(credential)
    now = db.now()
    with db.transaction() as tx:
        row = tx.one("select * from intercom_devices where revoked_at is null and (credential_hash = ? or"
                     " (previous_hash = ? and previous_valid_until > ?))", (h, h, now))
        if row is None:
            return None
        user = users.get(row["user_id"])
        if user is None or user.disabled:
            return None
        if (row["last_seen_at"] or row["issued_at"]) < db.later(-EXPIRE_DAYS * DAY):
            tx.execute("update intercom_devices set revoked_at = ? where id = ?", (now, row["id"]))
            store.log_event(tx, "device_expired", user_id=row["user_id"], device_id=row["id"])
            return None
        fresh = None
        if row["credential_hash"] != h or (row["rotates_at"] and row["rotates_at"] <= now):
            fresh = _new_credential()
            tx.execute("update intercom_devices set credential_hash = ?, previous_hash = ?, previous_valid_until = ?,"
                       " rotates_at = ?, last_seen_at = ? where id = ?",
                       (_hash(fresh), h, db.later(GRACE_S), db.later(ROTATE_DAYS * DAY), now, row["id"]))
            store.log_event(tx, "device_rotated", user_id=row["user_id"], device_id=row["id"])
        else:
            tx.execute("update intercom_devices set last_seen_at = ? where id = ?", (now, row["id"]))
        return tx.one("select * from intercom_devices where id = ?", (row["id"],)), user, fresh


def rename(device_id: int, name: Any, *, user_id: int | None = None) -> None:
    """Rename a phone; `user_id` limits it to that user's own."""
    store.ready()
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise Refused("Give the phone a name.")
    sql, args = "update intercom_devices set name = ? where id = ? and revoked_at is null", [name, device_id]
    if user_id is not None:
        sql, args = sql + " and user_id = ?", args + [user_id]
    if db.execute(sql, tuple(args)) == 0:
        raise Refused("That phone is not signed in any more.", 404)


def revoke(device_id: int, *, user_id: int | None = None, actor_id: int | None = None) -> dict | None:
    """Sign a phone out. Returns its row, or None when it was not signed in (or is not `user_id`'s)."""
    store.ready()
    with db.transaction() as tx:
        sql, args = "select * from intercom_devices where id = ? and revoked_at is null", [device_id]
        if user_id is not None:
            sql, args = sql + " and user_id = ?", args + [user_id]
        row = tx.one(sql, tuple(args))
        if row is None:
            return None
        tx.execute("update intercom_devices set revoked_at = ? where id = ?", (db.now(), device_id))
        store.log_event(tx, "device_revoked", user_id=row["user_id"], device_id=device_id, actor_id=actor_id)
        return row


def revoke_user(user_id: int, *, actor_id: int | None = None, reason: str = "") -> list[int]:
    """Sign every phone of a user out. Returns the device ids."""
    store.ready()
    with db.transaction() as tx:
        rows = tx.query("select id from intercom_devices where user_id = ? and revoked_at is null", (user_id,))
        for r in rows:
            tx.execute("update intercom_devices set revoked_at = ? where id = ?", (db.now(), r["id"]))
            store.log_event(tx, "device_revoked", user_id=user_id, device_id=r["id"], actor_id=actor_id,
                            detail={"reason": reason} if reason else None)
        return [r["id"] for r in rows]


def revoke_all(actor_id: int | None = None) -> list[tuple[int, int]]:
    """Sign every phone out. Returns (device id, user id) pairs."""
    store.ready()
    with db.transaction() as tx:
        rows = tx.query("select id, user_id from intercom_devices where revoked_at is null")
        for r in rows:
            tx.execute("update intercom_devices set revoked_at = ? where id = ?", (db.now(), r["id"]))
            store.log_event(tx, "device_revoked", user_id=r["user_id"], device_id=r["id"], actor_id=actor_id)
        return [(r["id"], r["user_id"]) for r in rows]


def listing(user_id: int | None = None) -> list[dict]:
    """Phones still signed in, with their user's name. Credentials and hashes never leave this file."""
    store.ready()
    sql = ("select d.id, d.user_id, u.name as user_name, d.name, d.platform, d.issued_at, d.last_seen_at, d.rotates_at"
           " from intercom_devices d join users u on u.id = d.user_id where d.revoked_at is null")
    args: tuple = ()
    if user_id is not None:
        sql, args = sql + " and d.user_id = ?", (user_id,)
    return db.query(sql + " order by d.last_seen_at desc, d.id", args)


def phone_config(user_id: int) -> list[dict]:
    """What a phone is told it may use: the user's channels with their rights and their own choices."""
    store.ready()
    return db.query(
        "select c.id, c.name, c.colour, c.mode, m.can_talk, m.can_listen, m.priority,"
        " coalesce(u.talk_selected, 0) as talk_selected, coalesce(u.listen_selected, 0) as listen_selected,"
        " coalesce(u.volume, 100) as volume"
        " from intercom_channel_members m join intercom_channels c on c.id = m.channel_id"
        " left join intercom_user_choices u on u.channel_id = c.id and u.user_id = m.user_id"
        " where m.user_id = ? order by c.sort, c.id", (user_id,))


def membership(user_id: int, channel_id: int) -> dict | None:
    store.ready()
    return db.one("select can_talk, can_listen, priority from intercom_channel_members"
                  " where user_id = ? and channel_id = ?", (user_id, channel_id))


def set_choice(user_id: int, channel_id: int, *, talk_selected: Any, listen_selected: Any, volume: Any) -> None:
    """Remember what the user picked on a channel, so it follows them to another phone."""
    store.ready()
    if membership(user_id, channel_id) is None:
        raise Refused("You are not on that channel.", 403)
    if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
        raise Refused("Volume is a whole number from 0 to 100.")
    db.execute("insert into intercom_user_choices (user_id, channel_id, talk_selected, listen_selected, volume)"
               " values (?, ?, ?, ?, ?) on conflict (user_id, channel_id) do update set"
               " talk_selected = excluded.talk_selected, listen_selected = excluded.listen_selected,"
               " volume = excluded.volume",
               (user_id, channel_id, int(bool(talk_selected)), int(bool(listen_selected)), volume))
