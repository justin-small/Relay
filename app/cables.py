"""Cables (#541, part of the gear/ports/channels epic #539): every cable between two ports, in one list.

A cable joins two core ports (app/ports.py) on any gear: ATEM to switch, Videohub to a TV, a switch port to the device on
it. A ref names one: `cable:relay:<id>`. Table: app/migrations/0013_cables.sql.

`by` says who said so. "operator" is a cable a person recorded: they can remove it. Any other value is the id of the module
that reports it (UniFi's uplinks): an operator may annotate it and nothing more, and it cannot be deleted here. A source
calls `sync(by, pairs)` with every cable it sees now; one it no longer reports is marked missing, not dropped, so its note
stays. A reported cable on a pair an operator already cabled is not made twice: the operator's cable stands.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from . import db

log = logging.getLogger("relay.cables")

CABLE = "cable"
OPERATOR = "operator"
NOTE_MAX = 300
MAX_CABLES = 5000

REFUSALS = {"no_cable": "That cable does not exist.", "no_port": "Pick a port for each end of the cable.",
            "same": "A cable joins two different ports.", "exists": "Those two ports are already cabled.",
            "too_many": "That is as many cables as Relay keeps.",
            "reported": "That cable is reported by a module. Add a note to it, or remove it where it is reported."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


def ref(cable_id: int) -> str:
    return f"{CABLE}:relay:{cable_id}"


def cable_id(text: Any) -> int | None:
    """The id in "cable:relay:5", or None for anything else."""
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != CABLE or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


def _note(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:NOTE_MAX].strip()


_SELECT = ("select c.*, pa.gear_id a_gear, pa.key a_key, pa.kind a_kind, pa.name a_name, pa.gear_name a_gear_name,"
           " pb.gear_id b_gear, pb.key b_key, pb.kind b_kind, pb.name b_name, pb.gear_name b_gear_name"
           " from cables c join ports pa on pa.id = c.port_a join ports pb on pb.id = c.port_b")


def _end(r: dict, side: str) -> dict:
    pid, gid = r["port_" + side], r[side + "_gear"]
    return {"port": pid, "ref": f"port:relay:{pid}", "gear": gid, "gear_ref": f"gear:relay:{gid}", "key": r[side + "_key"],
            "kind": r[side + "_kind"], "shown": r[side + "_name"] or r[side + "_gear_name"] or r[side + "_key"]}


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": r["id"], "ref": ref(r["id"]), "a": _end(r, "a"), "b": _end(r, "b"), "by": r["by"],
            "reported": r["by"] != OPERATOR, "note": r["note"], "missing": bool(r["missing"])}


def get(cid: int) -> dict | None:
    return _row(db.one(_SELECT + " where c.id = ?", (cid,)))


def by_ref(text: Any) -> dict | None:
    cid = cable_id(text)
    return get(cid) if cid is not None else None


def listing() -> list[dict]:
    """Every cable, oldest first."""
    return [_row(r) for r in db.query(_SELECT + " order by c.id")]  # type: ignore[misc]


def of_gears(ids: Iterable[int]) -> list[dict]:
    """The cables with an end on any of these pieces of gear."""
    wanted = sorted(set(ids))
    if not wanted:
        return []
    marks = ",".join("?" * len(wanted))
    return [_row(r) for r in db.query(_SELECT + f" where pa.gear_id in ({marks}) or pb.gear_id in ({marks}) order by c.id",  # nosec B608 - only "?" marks
                                      tuple(wanted) * 2)]  # type: ignore[misc]


def between(a: int, b: int) -> dict | None:
    """The cable joining these two ports, whichever way round they were entered."""
    return _row(db.one(_SELECT + " where min(c.port_a, c.port_b) = ? and max(c.port_a, c.port_b) = ?", tuple(sorted((a, b)))))


def _pair(a: Any, b: Any) -> tuple[int, int]:
    if not (isinstance(a, int) and isinstance(b, int)) or isinstance(a, bool) or isinstance(b, bool):
        raise Refused("no_port")
    if a == b:
        raise Refused("same")
    return (a, b)


_HAS = "select 1 x from cables where min(port_a, port_b) = ? and max(port_a, port_b) = ?"


def add(a: int, b: int, *, by: str = OPERATOR, note: Any = "") -> dict:
    """A cable between port ids `a` and `b`. Refused when they are the same port, a port is not there, or the two are
    already cabled."""
    a, b = _pair(a, b)
    lo, hi = sorted((a, b))
    with db.transaction() as tx:
        if tx.one("select count(*) n from ports where id in (?, ?)", (lo, hi))["n"] != 2:
            raise Refused("no_port")
        if tx.one(_HAS, (lo, hi)):
            raise Refused("exists")
        if tx.one("select count(*) n from cables")["n"] >= MAX_CABLES:
            raise Refused("too_many")
        tx.execute("insert into cables (port_a, port_b, by, note, created_at) values (?, ?, ?, ?, ?)",
                   (a, b, by or OPERATOR, _note(note), db.now()))
        cid = tx.one("select last_insert_rowid() id")["id"]
    return get(cid)  # type: ignore[return-value]


def set_note(cid: int, note: Any) -> None:
    """A note on any cable, reported or not."""
    if not db.execute("update cables set note = ? where id = ?", (_note(note), cid)):
        raise Refused("no_cable")


def remove(cid: int) -> None:
    """Remove a cable an operator recorded. A reported one stays: its source owns it."""
    row = get(cid)
    if row is None:
        raise Refused("no_cable")
    if row["reported"]:
        raise Refused("reported")
    db.execute("delete from cables where id = ?", (cid,))


def sync(by: str, reported: Iterable[tuple[int, int]]) -> list[dict]:
    """Every cable the source `by` reports now, as (port id, port id). A new one is made; one already there is kept (and
    comes back when it was missing); one this source had and no longer reports is marked missing. A pair an operator
    cabled is left as theirs. Returns the cables this source has."""
    now_pairs: dict[tuple[int, int], tuple[int, int]] = {}
    for a, b in reported:
        try:
            now_pairs[tuple(sorted(_pair(a, b)))] = (a, b)  # type: ignore[index]
        except Refused:
            continue
    with db.transaction() as tx:
        have = {tuple(sorted((r["port_a"], r["port_b"]))): r for r in tx.query("select * from cables where by = ?", (by,))}
        for key, (a, b) in sorted(now_pairs.items()):
            old = have.get(key)
            if old is not None:
                if old["missing"]:
                    tx.execute("update cables set missing = 0 where id = ?", (old["id"],))
                continue
            if tx.one(_HAS, key):
                continue                                        # an operator (or another source) already cabled it
            if tx.one("select count(*) n from ports where id in (?, ?)", key)["n"] != 2:
                continue
            if tx.one("select count(*) n from cables")["n"] >= MAX_CABLES:
                log.warning("cables: too many to keep another from %s", by)
                break
            tx.execute("insert into cables (port_a, port_b, by, created_at) values (?, ?, ?, ?)", (a, b, by, db.now()))
        for key, old in have.items():
            if key not in now_pairs and not old["missing"]:
                tx.execute("update cables set missing = 1 where id = ?", (old["id"],))
    return [c for c in listing() if c["by"] == by]
