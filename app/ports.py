"""Ports (#540, part of the gear/ports/channels epic #539): every physical connector on a piece of gear, in one list.

A port is any connector: a switch's Ethernet or SFP cage, an ATEM's SDI input, a HyperDeck's XLR timecode, a mains inlet,
this computer's network adapter. It belongs to one piece of gear (app/gear.py) and is known there by `key`, the gear's own
name for it ("12" on a switch, "in1" on a Blackmagic device, "mac:aa:bb:.." for this computer's adapter). A ref names one:
`port:relay:<id>`. A TCP port number is not a port; it stays a field of a host address. Table:
app/migrations/0012_ports.sql.

Stored, refreshed from the source: whatever reports a gear's ports (the network map for a switch, the traffic routes for
this computer, the Blackmagic module for its connectors) calls `sync(gear_id, ports)` with every port it sees now. A port
it no longer reports is marked missing, not dropped, so its note stays. A note or a Relay-only name can be put on a port
before the source has listed it (`adopt(..., heard=False)`).

The network fields say only what the source reports: `link_up` None is "unknown", kept apart from False ("no link", "no
signal"). Nothing here probes a device.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from . import db

log = logging.getLogger("relay.ports")

PORT = "port"
KINDS = ("eth", "sfp", "sdi", "hdmi", "xlr", "trs", "usb", "iec", "dc", "other")
DIRECTIONS = ("in", "out", "both")
KIND_NAMES = {"eth": "Ethernet (RJ45)", "sfp": "SFP", "sdi": "SDI (BNC)", "hdmi": "HDMI", "xlr": "XLR",
              "trs": "Jack (TRS)", "usb": "USB", "iec": "Mains (IEC)", "dc": "DC power", "other": "Connector"}
NETWORK = ("link_up", "speed_mbps", "poe_w", "errors")
NAME_MAX = 60
NOTE_MAX = 300
MAX_PORTS = 20000
MAX_PER_GEAR = 512
KEY_RE = re.compile(r"^[^\x00-\x1f]{1,200}$")

REFUSALS = {"no_port": "That port does not exist.", "no_gear": "That gear does not exist.",
            "key": "That is not a port Relay can keep.", "too_many": "That is as many ports as Relay keeps.",
            "present": "That port is still reported; only a missing one can be forgotten."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


# -- refs ---------------------------------------------------------------------------------------------------
def ref(port_id: int) -> str:
    return f"{PORT}:relay:{port_id}"


def port_id(text: Any) -> int | None:
    """The id in "port:relay:5", or None for anything else."""
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != PORT or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


# -- cleaning -----------------------------------------------------------------------------------------------
def _text(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit].strip()


def clean_kind(raw: Any) -> str:
    return raw if raw in KINDS else ""


def clean_direction(raw: Any) -> str:
    return raw if raw in DIRECTIONS else "both"


def _flag(raw: Any) -> int | None:
    return None if raw is None or not isinstance(raw, bool) else int(raw)


def _num(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
        return None
    return round(float(raw), 1)


def _int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _clean_network(raw: dict) -> dict:
    return {"link_up": _flag(raw.get("link_up")), "speed_mbps": _num(raw.get("speed_mbps")),
            "poe_w": _num(raw.get("poe_w")), "errors": _int(raw.get("errors"))}


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {**r, "ref": ref(r["id"]), "gear_ref": f"gear:relay:{r['gear_id']}",
            "link_up": None if r["link_up"] is None else bool(r["link_up"]), "missing": bool(r["missing"]),
            "shown": r["name"] or r["gear_name"] or r["key"]}


# -- reading ------------------------------------------------------------------------------------------------
def get(pid: int) -> dict | None:
    return _row(db.one("select * from ports where id = ?", (pid,)))


def by_ref(text: Any) -> dict | None:
    pid = port_id(text)
    return get(pid) if pid is not None else None


def by_key(gear_id: int, key: str) -> dict | None:
    return _row(db.one("select * from ports where gear_id = ? and key = ?", (gear_id, key)))


def of_gear(gear_id: int) -> list[dict]:
    """The ports of one piece of gear, in the order its source lists them."""
    return [_row(r) for r in db.query("select * from ports where gear_id = ? order by position, id",  # type: ignore[misc]
                                      (gear_id,))]


def of_gears(ids: Iterable[int]) -> dict[int, list[dict]]:
    """The ports of several pieces of gear in one read: {gear id: [port, ...]}."""
    wanted = sorted(set(ids))
    out: dict[int, list[dict]] = {g: [] for g in wanted}
    if not wanted:
        return out
    marks = ",".join("?" * len(wanted))
    for r in db.query(f"select * from ports where gear_id in ({marks}) order by gear_id, position, id",  # nosec B608 - only "?" marks
                      tuple(wanted)):
        out[r["gear_id"]].append(_row(r))  # type: ignore[arg-type]
    return out


# -- writing ------------------------------------------------------------------------------------------------
def _insert(tx, gear_id: int, key: str, now: str) -> int:
    if tx.one("select 1 x from gear where id = ?", (gear_id,)) is None:
        raise Refused("no_gear")
    if tx.one("select count(*) n from ports")["n"] >= MAX_PORTS or \
            tx.one("select count(*) n from ports where gear_id = ?", (gear_id,))["n"] >= MAX_PER_GEAR:
        raise Refused("too_many")
    tx.execute("insert into ports (gear_id, key, created_at) values (?, ?, ?)", (gear_id, key, now))
    return tx.one("select last_insert_rowid() id")["id"]


def adopt(gear_id: int, key: Any, *, kind: str = "", direction: str = "", gear_name: str = "",
          heard: bool = True) -> dict:
    """The port `key` on this gear, made the first time. `kind`, `direction` and `gear_name` fill in what is given.
    `heard=False` only makes sure the row is there (a note on a port the source has not listed): nothing else changes."""
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise Refused("key")
    now = db.now()
    with db.transaction() as tx:
        row = tx.one("select * from ports where gear_id = ? and key = ?", (gear_id, key))
        pid = row["id"] if row else _insert(tx, gear_id, key, now)
        if heard:
            tx.execute("update ports set kind = case when ? != '' then ? else kind end,"
                       " direction = case when ? != '' then ? else direction end,"
                       " gear_name = case when ? != '' then ? else gear_name end, missing = 0, last_seen = ? where id = ?",
                       (clean_kind(kind), clean_kind(kind), direction if direction in DIRECTIONS else "",
                        direction if direction in DIRECTIONS else "", _text(gear_name, NAME_MAX),
                        _text(gear_name, NAME_MAX), now, pid))
        return _row(tx.one("select * from ports where id = ?", (pid,)))  # type: ignore[return-value]


def sync(gear_id: int, reported: Iterable[dict], *, keep: Iterable[str] = ()) -> list[dict]:
    """What a source reports now for one piece of gear: every port it has, in order, each
    {"key", "kind", "direction", "gear_name", "link_up", "speed_mbps", "poe_w", "errors"} (all but key optional). Ports
    it no longer reports are marked missing, except those in `keep`. Writes only what changed. Returns the ports."""
    now = db.now()
    seen: list[str] = []
    with db.transaction() as tx:
        if tx.one("select 1 x from gear where id = ?", (gear_id,)) is None:
            raise Refused("no_gear")
        have = {r["key"]: r for r in tx.query("select * from ports where gear_id = ?", (gear_id,))}
        for pos, raw in enumerate(reported):
            key = raw.get("key") if isinstance(raw, dict) else None
            if not isinstance(key, str) or not KEY_RE.match(key) or key in seen:
                continue
            seen.append(key)
            want = {"kind": clean_kind(raw.get("kind")), "direction": clean_direction(raw.get("direction")),
                    "gear_name": _text(raw.get("gear_name"), NAME_MAX), "position": pos, "missing": 0,
                    **_clean_network(raw)}
            old = have.get(key)
            if old is None:
                try:
                    pid = _insert(tx, gear_id, key, now)
                except Refused:
                    log.warning("ports: too many to keep %s on gear %s", key, gear_id)
                    continue
                old = tx.one("select * from ports where id = ?", (pid,))
            if any(old[k] != v for k, v in want.items()) or not old["last_seen"]:
                cols = ", ".join(f"{k} = ?" for k in want)
                tx.execute(f"update ports set {cols}, last_seen = ? where id = ?",  # nosec B608 - fixed column names
                           (*want.values(), now, old["id"]))
        kept = set(seen) | set(keep)
        for key, old in have.items():
            if key not in kept and not old["missing"]:
                tx.execute("update ports set missing = 1, link_up = null where id = ?", (old["id"],))
    return of_gear(gear_id)


def set_note(pid: int, note: Any) -> None:
    text = _text(note, NOTE_MAX)
    if not db.execute("update ports set note = ? where id = ?", (text, pid)):
        raise Refused("no_port")


def rename(pid: int, name: Any) -> None:
    """A Relay-only name for the port. Empty text shows the gear's own name again."""
    if not db.execute("update ports set name = ? where id = ?", (_text(name, NAME_MAX), pid)):
        raise Refused("no_port")


def forget(pid: int, *, gone: bool = False) -> None:
    """Remove a port its source no longer reports (and its note). `gone` when the caller has checked that itself."""
    row = get(pid)
    if row is None:
        raise Refused("no_port")
    if not (row["missing"] or gone):
        raise Refused("present")
    db.execute("delete from ports where id = ?", (pid,))


def notes(gear_id: int) -> dict[str, str]:
    """{key: note} for the ports of one piece of gear that have a note."""
    return {r["key"]: r["note"] for r in db.query("select key, note from ports where gear_id = ? and note != ''",
                                                   (gear_id,))}
