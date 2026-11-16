"""Wi-Fi check-in (#356): a person's own devices, and marking them arrived when one is on the church network.

The UniFi module says who is on the network; this file never talks to a controller. A device is matched by MAC
address, or by the hostname the controller reports (looser: two phones can share a name, so a hostname belongs to one
person only). A person adds their phone from the crew page while it is on the Wi-Fi ("This is my phone": the UniFi
client with the request's address), or an admin adds one by MAC address or hostname.

Being on the network counts as arriving; leaving changes nothing, like the other check-in methods.
"""
from __future__ import annotations

import re
import time

from ... import db
from . import checkin, people

LABEL_MAX = 40
PER_PERSON = 5
_HOST = re.compile(r"^[a-z0-9][a-z0-9.\-_]{0,62}$")


def clean_mac(raw) -> str:
    if not isinstance(raw, str):
        return ""
    hexed = re.sub(r"[^0-9A-Fa-f]", "", raw).lower()
    return ":".join(hexed[i:i + 2] for i in range(0, 12, 2)) if len(hexed) == 12 else ""


def clean_hostname(raw) -> str:
    text = raw.strip().lower() if isinstance(raw, str) else ""
    return text if _HOST.match(text) else ""


def _label(raw) -> str:
    return " ".join(raw.split())[:LABEL_MAX] if isinstance(raw, str) else ""


def mine(user_id: int) -> list[dict]:
    people.ready()
    return db.query("select id, kind, value, label, added_ts, added_by from crew_devices where user_id = ?"
                    " order by added_ts", (user_id,))


def everyone() -> list[dict]:
    people.ready()
    return db.query("select d.id, d.user_id, u.name, d.kind, d.value, d.label, d.added_ts, d.added_by"
                    " from crew_devices d join users u on u.id = d.user_id order by lower(u.name), d.added_ts")


def add(user_id: int, kind: str, value: str, label: str = "", by: str = "self") -> dict:
    """Add a device. Raises people.Invalid with a sentence for the page."""
    people.ready()
    if kind == "mac":
        value = clean_mac(value)
        if not value:
            raise people.Invalid("That is not a MAC address. It looks like 3c:22:fb:5e:90:40.")
    elif kind == "hostname":
        value = clean_hostname(value)
        if not value:
            raise people.Invalid("That is not a device name. Use the name the network shows, like justins-iphone.")
    else:
        raise people.Invalid("A device is a MAC address or a hostname.")
    taken = db.one("select user_id from crew_devices where kind = ? and value = ?", (kind, value))
    if taken:
        raise people.Invalid("That device is already yours." if taken["user_id"] == user_id
                             else "That device belongs to someone else. Ask an admin.")
    n = db.one("select count(*) as n from crew_devices where user_id = ?", (user_id,))["n"]
    if n >= PER_PERSON:
        raise people.Invalid(f"You can add {PER_PERSON} devices. Remove one first.")
    db.execute("insert into crew_devices (user_id, kind, value, label, added_ts, added_by) values (?, ?, ?, ?, ?, ?)",
               (user_id, kind, value, _label(label), time.time(), "admin" if by == "admin" else "self"))
    return db.one("select id, kind, value, label, added_ts, added_by from crew_devices where kind = ? and value = ?",
                  (kind, value))


def remove(device_id: int, user_id: int | None = None) -> bool:
    """Remove a device; only the owner's own when `user_id` is given."""
    people.ready()
    if user_id is None:
        return db.execute("delete from crew_devices where id = ?", (device_id,)) > 0
    return db.execute("delete from crew_devices where id = ? and user_id = ?", (device_id, user_id)) > 0


def present(clients: list[dict]) -> set[int]:
    """The people with a device among these network clients (each a UniFi client: mac, hostname)."""
    people.ready()
    macs = {c.get("mac") for c in clients if c.get("mac") and c.get("missing") is None}
    names = {c.get("hostname") for c in clients if c.get("hostname") and c.get("missing") is None}
    if not macs and not names:
        return set()
    found = set()
    for row in db.query("select user_id, kind, value from crew_devices"):
        if (row["kind"] == "mac" and row["value"] in macs) or (row["kind"] == "hostname" and row["value"] in names):
            found.add(row["user_id"])
    return found


def arrive_present(clients: list[dict], key: str, now: float | None = None) -> list[int]:
    """Mark everyone with a device on the network arrived for this plan. Returns who arrived just now."""
    ts = time.time() if now is None else now
    new = []
    for uid in sorted(present(clients)):
        if checkin.mine(uid, key) is None:
            checkin.arrive(uid, key, "wifi", ts)
            new.append(uid)
    return new
