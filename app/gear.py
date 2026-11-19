"""Gear and racks (#547, part of the gear/ports/channels epic #539): one record per piece of equipment.

A piece of gear is a switch, an ATEM, a Dante stage box, this computer: one name, one rack, one place. The network map
and Blackmagic attach to it instead of keeping names, notes and racks of their own, so the same box is one row that every
page lists under one name. Tables: app/migrations/0011_gear_racks.sql. Phones are core devices (app/devices.py), not gear.

A ref names one: `gear:relay:<id>` and `rack:relay:<id>`. A rack is inside a core place (app/screens.py: Booth > Rack A);
gear sits in a rack, or straight in a place.

Each older feature knew a box by its own id. `adopt(keys, ...)` turns those ids into the gear, creating it the first time,
so a name, a note and a rack survive a new IP address. Keys are "mac:<mac>", "ip:<address>", "host:<name>" (for a box with
no MAC), "blackmagic:<id>", "dante:<name>" and "relay:this". Two keys for the same box join one record: adopting keys
that two records hold merges them. A record that has a MAC is known by it alone, so its "ip:" and "host:" keys go.

The name is what Relay shows. Until an operator types one it follows what the source reports (`named` is 0); after that it
stays, and nothing is sent to the device. Names that come from the gear itself are #543.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from . import db, screens

log = logging.getLogger("relay.gear")

GEAR = "gear"
RACK = "rack"
NAME_MAX = 60
NOTE_MAX = 300
RACK_MAX = 40
MAX_GEAR = 2000
MAX_RACKS = 200
KEY_RE = re.compile(r"^[a-z]{2,12}:[^\s]{1,90}$")

REFUSALS = {"name": "Give it a name.", "no_gear": "That gear does not exist.", "no_rack": "That rack does not exist.",
            "no_place": "That place does not exist.", "rack_taken": "That place already has a rack with that name.",
            "too_many": "That is as many as Relay keeps."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


# -- refs and keys ------------------------------------------------------------------------------------------
def ref(gear_id: int) -> str:
    return f"{GEAR}:relay:{gear_id}"


def rack_ref(rack_id: int) -> str:
    return f"{RACK}:relay:{rack_id}"


def _parse(text, head: str) -> int | None:
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != head or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


def gear_id(text) -> int | None:
    """The id in "gear:relay:5", or None for anything else."""
    return _parse(text, GEAR)


def rack_id(text) -> int | None:
    return _parse(text, RACK)


def net_keys(mac: str = "", ips: Iterable[str] = (), host: str = "") -> list[str]:
    """The keys of a box seen on the network: its MAC, its addresses and its host name. A box that has a MAC ends up known by
    it alone (the rest only join it to a record that was made from an address, before its MAC was seen)."""
    return (["mac:" + mac] if mac else []) + ["ip:" + i for i in ips if i] + (["host:" + host] if host else [])


def _name(raw, limit: int = NAME_MAX) -> str:
    return " ".join(str(raw or "").split())[:limit].strip()


# -- racks --------------------------------------------------------------------------------------------------
def _rack(row: dict) -> dict:
    return {**row, "ref": rack_ref(row["id"])}


def racks() -> list[dict]:
    """Every rack with its place's name and how much gear is in it, by place then rack name."""
    rows = db.query("select r.*, p.name place, (select count(*) from gear g where g.rack_id = r.id) gear"
                    " from racks r join places p on p.id = r.place_id order by r.position, p.name collate nocase, r.name collate nocase")
    return [{**_rack(r), "place_ref": screens.place_ref(r["place_id"])} for r in rows]


def rack_by_name(name, place: int | None = None) -> dict | None:
    """The rack with this name, in this place (an id) or, with no place given, the first place that has one."""
    text = _name(name, RACK_MAX)
    if not text:
        return None
    sql, args = "select * from racks where name = ? collate nocase", [text]
    if place is not None:
        sql, args = sql + " and place_id = ?", args + [place]
    row = db.one(sql + " order by id", tuple(args))
    return _rack(row) if row else None


def rack_add(place: int, name) -> dict:
    """A new rack in a place. A name the place already has returns that rack."""
    text = _name(name, RACK_MAX)
    if not text:
        raise Refused("name")
    if db.one("select id from places where id = ?", (place,)) is None:
        raise Refused("no_place")
    found = rack_by_name(text, place)
    if found:
        return found
    if db.one("select count(*) n from racks")["n"] >= MAX_RACKS:
        raise Refused("too_many")
    db.execute("insert into racks (place_id, name, created_at) values (?, ?, ?)", (place, text, db.now()))
    return rack_by_name(text, place)  # type: ignore[return-value]


def rack_rename(rid: int, name) -> None:
    text = _name(name, RACK_MAX)
    if not text:
        raise Refused("name")
    row = db.one("select place_id from racks where id = ?", (rid,))
    if row is None:
        raise Refused("no_rack")
    other = db.one("select id from racks where place_id = ? and name = ? collate nocase", (row["place_id"], text))
    if other and other["id"] != rid:
        raise Refused("rack_taken")
    db.execute("update racks set name = ? where id = ?", (text, rid))


def rack_set_position(rid: int, position: int) -> None:
    """Where the rack comes in the list on the Network page."""
    db.execute("update racks set position = ? where id = ?", (max(0, int(position)), rid))


def rack_delete(rid: int) -> None:
    """Remove a rack; its gear stays in the place, out of any rack."""
    if not db.execute("delete from racks where id = ?", (rid,)):
        raise Refused("no_rack")


def rack_in_place(name, place_name) -> dict:
    """The rack `name` in the place called `place_name`, each made when it is not there yet."""
    return rack_add(screens.place_add(place_name)["id"], name)


# -- gear ---------------------------------------------------------------------------------------------------
def _gear(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "named": bool(row["named"]), "ref": ref(row["id"]),
            "place_ref": screens.place_ref(row["place_id"]) if row["place_id"] else "",
            "rack_ref": rack_ref(row["rack_id"]) if row["rack_id"] else ""}


def get(gid: int) -> dict | None:
    return _gear(db.one("select * from gear where id = ?", (gid,)))


def by_key(key: str) -> dict | None:
    return _gear(db.one("select g.* from gear g join gear_keys k on k.gear_id = g.id where k.key = ?", (key,)))


def keys(gid: int) -> list[str]:
    return [r["key"] for r in db.query("select key from gear_keys where gear_id = ? order by key", (gid,))]


def by_keys() -> dict[str, dict]:
    """Every gear record by every key it has: {key: gear}. One read, for a page that looks up many boxes."""
    rows = {r["id"]: _gear(r) for r in db.query("select * from gear")}
    return {r["key"]: rows[r["gear_id"]] for r in db.query("select key, gear_id from gear_keys") if r["gear_id"] in rows}


def listing() -> list[dict]:
    """Every gear record with its place and rack names and its keys, by place, rack, position then name."""
    keyed: dict[int, list[str]] = {}
    for r in db.query("select key, gear_id from gear_keys order by key"):
        keyed.setdefault(r["gear_id"], []).append(r["key"])
    rows = db.query("select g.*, p.name place, r.name rack from gear g left join places p on p.id = g.place_id"
                    " left join racks r on r.id = g.rack_id order by (p.name is null), p.name collate nocase,"
                    " r.name collate nocase, g.position, g.name collate nocase, g.id")
    return [{**_gear(r), "place": r["place"] or "", "rack": r["rack"] or "", "keys": keyed.get(r["id"], [])}  # type: ignore[dict-item]
            for r in rows]


def in_rack(rid: int) -> list[dict]:
    """The gear in a rack, top to bottom."""
    return [_gear(r) for r in db.query("select * from gear where rack_id = ? order by position, name collate nocase, id",
                                       (rid,))]  # type: ignore[misc]


def _clean_keys(raw: Iterable[str]) -> list[str]:
    out: list[str] = []
    for k in raw:
        if isinstance(k, str) and KEY_RE.match(k) and k not in out:
            out.append(k)
    return out


def _tidy_keys(tx, gid: int) -> None:
    """A record that has a MAC is known by it alone."""
    if tx.one("select 1 x from gear_keys where gear_id = ? and key like 'mac:%'", (gid,)):
        tx.execute("delete from gear_keys where gear_id = ? and (key like 'ip:%' or key like 'host:%')", (gid,))


def _merge(tx, into: int, other: int) -> None:
    """`other` is the same box as `into`: it keeps its keys, and fills what `into` has blank."""
    a, b = tx.one("select * from gear where id = ?", (into,)), tx.one("select * from gear where id = ?", (other,))
    tx.execute("update gear_keys set gear_id = ? where gear_id = ?", (into, other))
    keep = a if (a["named"] or not b["named"]) else b
    tx.execute("update gear set name = ?, named = ?, kind = ?, model = ?, drawing = ?, notes = ?,"
               " place_id = coalesce(place_id, ?), rack_id = coalesce(rack_id, ?), position = ?,"
               " last_seen = max(last_seen, ?) where id = ?",
               (keep["name"], keep["named"], a["kind"] or b["kind"], a["model"] or b["model"],
                a["drawing"] or b["drawing"], a["notes"] or b["notes"], b["place_id"], b["rack_id"],
                a["position"] if a["rack_id"] else b["position"], b["last_seen"], into))
    tx.execute("delete from gear where id = ?", (other,))


def adopt(keys_: Iterable[str], *, name: str = "", kind: str = "", model: str = "", heard: bool = True) -> dict:
    """The gear these keys stand for, made the first time they are seen and marked as heard from now.

    Every key is the same box: keys that two records hold merge them. `name` is what the source calls it: it names a new
    record, and follows the source until an operator names it. `kind` and `model` fill what is blank.
    `heard=False` only records the keys (a device saved but not reachable): "last seen" stays."""
    wanted = _clean_keys(keys_)
    if not wanted:
        raise ValueError("gear needs at least one key")
    text, now = _name(name), db.now()
    with db.transaction() as tx:
        ids = sorted({r["gear_id"] for k in wanted for r in tx.query("select gear_id from gear_keys where key = ?", (k,))})
        if not ids:
            if tx.one("select count(*) n from gear")["n"] >= MAX_GEAR:
                raise Refused("too_many")
            shown = text or wanted[0].split(":", 1)[1][:NAME_MAX]
            tx.execute("insert into gear (name, kind, model, created_at) values (?, ?, ?, ?)", (shown, kind, model, now))
            ids = [tx.one("select last_insert_rowid() id")["id"]]
        for other in ids[1:]:
            _merge(tx, ids[0], other)
        gid = ids[0]
        for k in wanted:
            tx.execute("insert or ignore into gear_keys (key, gear_id) values (?, ?)", (k, gid))
        _tidy_keys(tx, gid)
        tx.execute("update gear set name = case when named = 0 and ? != '' then ? else name end,"
                   " kind = case when kind = '' then ? else kind end, model = case when model = '' then ? else model end,"
                   " last_seen = case when ? then ? else last_seen end where id = ?",
                   (text, text, kind, model[:60], heard, now, gid))
        return _gear(tx.one("select * from gear where id = ?", (gid,)))  # type: ignore[return-value]


def rename(gid: int, name) -> None:
    """An operator names it. Empty text hands the name back to the source."""
    text = _name(name)
    row = get(gid)
    if row is None:
        raise Refused("no_gear")
    if text:
        db.execute("update gear set name = ?, named = 1 where id = ?", (text, gid))
    else:
        db.execute("update gear set named = 0 where id = ?", (gid,))


def set_notes(gid: int, notes) -> None:
    text = " ".join(str(notes or "").split())[:NOTE_MAX]
    if not db.execute("update gear set notes = ? where id = ?", (text, gid)):
        raise Refused("no_gear")


def set_details(gid: int, *, kind: str | None = None, model: str | None = None, drawing: str | None = None) -> None:
    """What a module knows about its own box. A part left None is kept."""
    row = get(gid)
    if row is None:
        raise Refused("no_gear")
    db.execute("update gear set kind = ?, model = ?, drawing = ? where id = ?",
               (row["kind"] if kind is None else kind[:20], row["model"] if model is None else model[:60],
                row["drawing"] if drawing is None else drawing[:40], gid))


def set_place(gid: int, place) -> None:
    """Put the gear straight in a place (a place id), or in none with None. It leaves any rack in another place."""
    if place is not None and db.one("select id from places where id = ?", (place,)) is None:
        raise Refused("no_place")
    if not db.execute("update gear set place_id = ?, rack_id = case when rack_id in"
                      " (select id from racks where place_id = ?) then rack_id else null end where id = ?",
                      (place, place, gid)):
        raise Refused("no_gear")


def set_rack(gid: int, rack, position: int | None = None) -> None:
    """Put the gear in a rack (a rack id), which is also its place, or out of any rack with None."""
    if rack is None:
        if not db.execute("update gear set rack_id = null where id = ?", (gid,)):
            raise Refused("no_gear")
        return
    row = db.one("select place_id from racks where id = ?", (rack,))
    if row is None:
        raise Refused("no_rack")
    if not db.execute("update gear set rack_id = ?, place_id = ?, position = coalesce(?, position) where id = ?",
                      (rack, row["place_id"], position, gid)):
        raise Refused("no_gear")


def next_position(rack) -> int:
    """The position after the last gear in this rack (an id), or in no rack with None."""
    row = db.one("select coalesce(max(position), -1) + 1 n from gear where rack_id is ?", (rack,))
    return row["n"] if row else 0


def set_position(gid: int, position: int) -> None:
    db.execute("update gear set position = ? where id = ?", (max(0, int(position)), gid))


def delete(gid: int) -> None:
    if not db.execute("delete from gear where id = ?", (gid,)):
        raise Refused("no_gear")


def forget_key(key: str) -> None:
    """An older feature dropped this id (a Blackmagic device was deleted); the gear stays."""
    db.execute("delete from gear_keys where key = ?", (key,))
