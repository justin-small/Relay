"""Devices (#528, part of the shared-concepts epic #521): one record per phone or computer, owned by a user.

A device is what a person carries. What it can do is a *part*, and every part stays in the table of the feature that
owns it, with a `device_id` that points here (and, but for sign-in trust, goes with the device): sign-in trust (`trusted_devices`, core), the intercom credential
(`intercom_devices`), Wi-Fi check-in (`crew_devices`, a MAC address or hostname) and web push (`crew_push_subs`).
Tables: app/migrations/0009_devices.sql. A ref names one: `device:relay:<id>`.

A module that holds a part registers it in `attach()` with `register(kind, label, table, revoke, describe)` and takes it
back in `detach()`:

* `table` is the module's own table; it has a `device_id` that references `devices (id) on delete cascade`, so the part
  goes when the device does, even while the module is off;
* `revoke(device_id)` ends the part's live side (the intercom's open connection); the rows themselves go with the device;
* `describe(device_id)` is a list of short sentences for what the part holds on that device, empty for none.

`for_part` is how a module gets the device a new part belongs to: the one it was told (`device_id`), else a device of
the same user with the same, non-empty user agent that has no part of this kind yet, else a new one. Parts that cannot
be told apart (no user agent) are separate devices, and `merge` joins two of one user's.

`revoke` forgets a device: every registered part ends, then the device row goes and takes every part row with it,
which also ends the PIN sessions that came from its sign-in part. Core never imports a module: it holds this registry
the way `users.signed_out_hooks` holds the account hooks.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from . import db

log = logging.getLogger("relay.devices")

DEVICE = "device"
NAME_MAX = 60
UA_MAX = 200
MAX_PER_USER = 50
PLATFORMS = ("web", "ios", "android")
SIGNIN = "signin"
SIGNIN_TABLE = "trusted_devices"
_TABLE = re.compile(r"^[a-z][a-z0-9_]{0,60}$")

REFUSALS = {"name": "Give the phone a name.", "no_device": "That phone does not exist.",
            "merge_self": "Choose another phone to join it with.",
            "merge_user": "Only one person's phones can be joined together.",
            "too_many": "That is as many phones as Relay keeps for one person."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


@dataclass(frozen=True)
class Part:
    kind: str
    label: str
    table: str
    revoke: Callable[[int], None]
    describe: Callable[[int], list[str]]


_parts: dict[str, Part] = {}


def register(kind: str, label: str, table: str, revoke: Callable[[int], None],
             describe: Callable[[int], list[str]]) -> None:
    """A module offers a part of a device (in `attach`). A second registration of the same kind replaces the first."""
    if not _TABLE.match(table or "") or not kind or kind == SIGNIN:
        raise ValueError("bad device part")
    _parts[kind] = Part(kind, label, table, revoke, describe)


def unregister(kind: str) -> None:
    """Take a part back (in `detach`). Harmless when it was never registered."""
    _parts.pop(kind, None)


def parts() -> list[Part]:
    return list(_parts.values())


# -- refs ---------------------------------------------------------------------------------------------------
def ref(device_id: int) -> str:
    return f"{DEVICE}:relay:{device_id}"


def device_id(text) -> int | None:
    """The id in "device:relay:5", or None for anything else."""
    if not isinstance(text, str):
        return None
    bits = text.split(":")
    if len(bits) != 3 or bits[0] != DEVICE or bits[1] != "relay" or not bits[2].isdigit():
        return None
    return int(bits[2])


def _name(raw) -> str:
    return " ".join(str(raw or "").split())[:NAME_MAX].strip()


def agent(raw) -> str:
    """A browser's user agent, tidied to what is stored."""
    return " ".join(str(raw or "").split())[:UA_MAX]


def default_name(user_agent: str) -> str:
    ua = (user_agent or "").lower()
    for needle, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android phone")):
        if needle in ua:
            return name
    return "Browser"


def platform_of(user_agent: str, given: str = "") -> str:
    if given in ("ios", "android"):
        return given
    ua = (user_agent or "").lower()
    if "iphone" in ua or "ipad" in ua:
        return "ios"
    return "android" if "android" in ua else "web"


# -- making and finding -------------------------------------------------------------------------------------
def create(user_id: int, *, name: str = "", platform: str = "", user_agent: str = "", q=db) -> int:
    """A new device for `user_id`; returns its id. `q` is `db` or a transaction."""
    ua = agent(user_agent)
    if q.one("select count(*) n from devices where user_id = ?", (user_id,))["n"] >= MAX_PER_USER:
        _drop_oldest_empty(q, user_id)
    return q.one("insert into devices (user_id, name, platform, user_agent, last_seen, created_at)"
                 " values (?, ?, ?, ?, ?, ?) returning id",
                 (user_id, _name(name) or default_name(ua), platform_of(ua, platform), ua, db.now(), db.now()))["id"]


def part_tables(q=db) -> list[str]:
    """Every table that holds a part of a device: each table with a foreign key to `devices`, found in the database
    itself, so a module that is switched off at the moment still counts (its rows must not be lost by a merge)."""
    out = []
    for t in q.query("select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"):
        if any(fk["table"] == "devices" for fk in q.query(f'pragma foreign_key_list("{t["name"]}")')):
            out.append(t["name"])
    return out


def _parts_of(q, device_id: int) -> int:
    """How many parts a device holds, in any module."""
    # nosec B608: `t` is a table name read from sqlite_master (part_tables), never text from a request
    return sum(q.one(f'select count(*) n from "{t}" where device_id = ?', (device_id,))["n"]  # nosec B608
               for t in part_tables(q))


def _drop_oldest_empty(q, user_id: int) -> None:
    """A person at the cap loses their oldest device with no part, if there is one; otherwise the cap holds."""
    for row in q.query("select id from devices where user_id = ? order by coalesce(last_seen, created_at), id", (user_id,)):
        if _parts_of(q, row["id"]) == 0:
            q.execute("delete from devices where id = ?", (row["id"],))
            return
    raise Refused("too_many")


def for_part(user_id: int, kind: str, table: str, *, device: int | None = None, name: str = "", platform: str = "",
             user_agent: str = "", q=db) -> int:
    """The device a new part of `kind` (kept in `table`) belongs to, made when none fits. `device` is one the caller was
    told about (a phone that says which it is); it is used when it is this user's and has no part of this kind."""
    if not _TABLE.match(table or ""):
        raise ValueError("bad device part table")
    ua = agent(user_agent)

    def free(row_id: int) -> bool:
        # `table` passed the _TABLE pattern above and is the calling module's own table name
        return q.one(f"select 1 x from {table} where device_id = ?", (row_id,)) is None  # nosec B608

    if device is not None:
        mine = q.one("select id from devices where id = ? and user_id = ?", (device, user_id))
        if mine and free(mine["id"]):
            return mine["id"]
    if ua:
        for row in q.query("select id from devices where user_id = ? and user_agent = ? order by id", (user_id, ua)):
            if free(row["id"]):
                return row["id"]
    return create(user_id, name=name, platform=platform, user_agent=ua, q=q)


def seen(device_id: int | None, when: str | None = None, q=db) -> None:
    """Note that a part of a device was just used."""
    if device_id is not None:
        q.execute("update devices set last_seen = ? where id = ?", (when or db.now(), device_id))


# -- reading ------------------------------------------------------------------------------------------------
def _describe(row: dict) -> list[dict]:
    out = []
    n = db.one("select count(*) n from trusted_devices where device_id = ?", (row["id"],))
    if n and n["n"]:
        out.append({"kind": SIGNIN, "label": "Sign-in", "detail": ["Signs in with a PIN"]})
    for p in _parts.values():
        try:
            lines = [str(t) for t in p.describe(row["id"])]
        except Exception:      # a module's description must not hide the others
            log.exception("describing device part %s", p.kind)
            lines = []
        if lines:
            out.append({"kind": p.kind, "label": p.label, "detail": lines})
    return out


def listing(user_id: int | None = None) -> list[dict]:
    """Every device (or one user's) with its owner and the parts it holds:
    {"id", "ref", "user_id", "user", "name", "platform", "last_seen", "created", "parts": [{"kind", "label", "detail"}]}."""
    sql = ("select d.id, d.user_id, u.name as user, d.name, d.platform, d.last_seen, d.created_at as created"
           " from devices d join users u on u.id = d.user_id")
    rows = db.query(sql + (" where d.user_id = ?" if user_id is not None else "")
                    + " order by lower(u.name), coalesce(d.last_seen, d.created_at) desc, d.id",
                    (user_id,) if user_id is not None else ())
    return [{**r, "ref": ref(r["id"]), "parts": _describe(r)} for r in rows]


def get(device: int, user_id: int | None = None) -> dict | None:
    sql, args = "select * from devices where id = ?", [device]
    if user_id is not None:
        sql, args = sql + " and user_id = ?", args + [user_id]
    return db.one(sql, tuple(args))


# -- changing -----------------------------------------------------------------------------------------------
def rename(device: int, name, *, user_id: int | None = None) -> None:
    text = _name(name)
    if not text:
        raise Refused("name")
    sql, args = "update devices set name = ? where id = ?", [text, device]
    if user_id is not None:
        sql, args = sql + " and user_id = ?", args + [user_id]
    if not db.execute(sql, tuple(args)):
        raise Refused("no_device")


def revoke(device: int, *, user_id: int | None = None) -> dict | None:
    """Forget a phone: every part ends, then the device and every part row go. Returns the device (with the kinds of
    part it had), or None when there is no such device (or it is not `user_id`'s)."""
    row = get(device, user_id)
    if row is None:
        return None
    kinds = [p["kind"] for p in _describe(row)]
    for p in parts():
        try:
            p.revoke(device)
        except Exception:      # one module failing must not leave the rest of the phone signed in
            log.exception("revoking device part %s", p.kind)
    with db.transaction() as tx:
        tx.execute("delete from trusted_devices where device_id = ?", (device,))     # sign-in trust outlives a person
        tx.execute("delete from devices where id = ?", (device,))
    return {**row, "parts": kinds}


def merge(keep: int, other: int, *, user_id: int | None = None) -> None:
    """Join `other` into `keep`: its parts become the keeper's and `other` goes. Both must be one person's."""
    if keep == other:
        raise Refused("merge_self")
    with db.transaction() as tx:
        a, b = (tx.one("select * from devices where id = ?", (i,)) for i in (keep, other))
        if a is None or b is None or (user_id is not None and (a["user_id"] != user_id or b["user_id"] != user_id)):
            raise Refused("no_device")
        if a["user_id"] != b["user_id"]:
            raise Refused("merge_user")
        for table in part_tables(tx):
            tx.execute(f'update "{table}" set device_id = ? where device_id = ?', (keep, other))  # nosec B608 - names from sqlite_master
        tx.execute("update devices set user_agent = case when user_agent = '' then ? else user_agent end,"
                   " last_seen = nullif(max(coalesce(last_seen, ''), ?), '') where id = ?",
                   (b["user_agent"], b["last_seen"] or "", keep))
        tx.execute("delete from devices where id = ?", (other,))
