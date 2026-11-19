"""Channels (#542, part of the gear/ports/channels epic #539): every audio, video and talk channel, in one list.

A channel is what an operator calls "Camera 2" or "Vocal 1" or "Stage". A ref names one: `channel:relay:<id>`. Tables:
app/migrations/0014_channels.sql.

A channel is made of **ends**, what each module reports: NDI's "BOOTH (Camera 2)", a Videohub input, an ATEM input, a tally
camera. A module calls `sync(provider, reported, gear_id)` with every channel it sees now; a new one gets a channel of its
own, one it no longer reports is marked missing, not dropped, so its name stays. Relay never joins two ends by itself:
`suggestions()` lists the likely pairs (the same name, or the same number on the same gear) and an operator confirms each
with `merge()`, or says they are different with `decline()`. `split()` takes one end back out. After a merge the old ref
still works: `by_ref` follows it to the channel that now holds the ends.

`kind` is audio, video or talk; an end's `direction` is source (what produces the signal) or destination (what takes it).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Iterable

from . import db, modules
from .text import same_name

log = logging.getLogger("relay.channels")

CHANNEL = "channel"
KINDS = ("audio", "video", "talk")
DIRECTIONS = ("source", "destination")
KIND_NAMES = {"audio": "Audio", "video": "Video", "talk": "Talk"}
NAME_MAX = 60
NOTE_MAX = 300
KEY_RE = re.compile(r"^[^\x00-\x1f]{1,200}$")
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_CHANNELS = 20000
MAX_PER_PROVIDER = 4000
# "Input 3", "Out 12", "Channel 5": names a device gives everything, so they say nothing about which channel it is.
GENERIC = re.compile(r"^(?:input|output|in|out|channel|ch|source|src|camera input)\s*\d*$", re.I)

REFUSALS = {"no_channel": "That channel does not exist.", "no_end": "That channel end does not exist.",
            "same": "A channel cannot be joined to itself.", "kind": "Only channels of the same kind can be joined.",
            "name": "Name the channel.", "device": "The gear did not take the new name, so nothing was changed. Check that it is connected and try again.",
            "no_device": "That channel's gear cannot be renamed from Relay.",
            "too_many": "That is as many channels as Relay keeps.",
            "present": "That channel is still reported; only a missing one can be forgotten.",
            "only_end": "That is the only end of its channel; there is nothing to split."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


# -- refs ---------------------------------------------------------------------------------------------------
def ref(channel_id: int) -> str:
    return f"{CHANNEL}:relay:{channel_id}"


def channel_id(text: Any) -> int | None:
    """The id in "channel:relay:5", or None for anything else."""
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != CHANNEL or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


def _text(raw: Any, limit: int) -> str:
    return " ".join(str(raw or "").split())[:limit].strip()


def _int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw < 10**9 else None


# -- what changed ---------------------------------------------------------------------------------------------
_gen = 0
_names: tuple[int, dict[str, str]] = (-1, {})


def generation() -> int:
    """A number that goes up whenever any channel is written, so a reader of names can tell its copy is stale."""
    return _gen


def _touch() -> None:
    global _gen
    _gen += 1


def names() -> dict[str, str]:
    """{channel ref: name shown} for every channel, a merged-away ref included (it shows its new channel's name). Read
    again only after a write; empty while the database is down."""
    global _names
    if _names[0] != _gen:
        try:
            out: dict[str, str] = {}
            roots = {c["id"]: c["shown"] for c in listing()}
            for r in db.query("select id from channels"):
                root = _root_id(r["id"])
                if root in roots:
                    out[ref(r["id"])] = roots[root]
            _names = (_gen, out)
        except db.Unavailable:
            return {}
    return _names[1]


# -- reading ------------------------------------------------------------------------------------------------
def _end(r: dict) -> dict:
    gid = r["gear_id"]
    return {"id": r["id"], "provider": r["provider"], "key": r["key"], "gear": gid,
            "gear_ref": f"gear:relay:{gid}" if gid else "", "direction": r["direction"], "number": r["number"],
            "gear_name": r["gear_name"], "missing": bool(r["missing"])}


def _end_name(e: dict) -> str:
    return e["gear_name"] or (f"Channel {e['number']}" if e["number"] is not None else e["key"])


def _build(r: dict, ends: list[dict]) -> dict:
    live = [e for e in ends if not e["missing"]]
    lead = (live or ends or [None])[0]
    return {"id": r["id"], "ref": ref(r["id"]), "kind": r["kind"], "name": r["name"], "note": r["note"],
            "shown": r["name"] or (_end_name(lead) if lead else f"Channel {r['id']}"),
            "gear_name": _end_name(lead) if lead else "",
            "can_device": any(_renamable(e) for e in ends),
            "missing": not live, "ends": ends, "providers": sorted({e["provider"] for e in ends}),
            "direction": lead["direction"] if lead else "source", "number": lead["number"] if lead else None,
            "gear_ref": lead["gear_ref"] if lead else ""}


def _gear_api(provider: str) -> dict | None:
    """What a module offers for naming on its gear: `modules.provide("gear.<id>", {"can_rename": fn(key), "rename": fn(key,
    name)})`. None while that module is off."""
    api = modules.using(f"gear.{provider}")
    return api if isinstance(api, dict) and callable(api.get("rename")) and callable(api.get("can_rename")) else None


def _renamable(e: dict) -> bool:
    api = _gear_api(e["provider"])
    try:
        return bool(api and not e["missing"] and api["can_rename"](e["key"]))
    except Exception:  # a module's own check must not break the list
        log.exception("channels: %s could not say whether %s can be renamed", e["provider"], e["key"])
        return False


def _roots(kind: str = "", only: int = 0) -> list[dict]:
    """The channels that are not merged into another, oldest first: all, one kind, or the one with id `only`."""
    rows = db.query("select * from channels where merged_into is null and (? = '' or kind = ?) and (? = 0 or id = ?)"
                    " order by id", (kind, kind, only, only))
    if not rows:
        return []
    ends: dict[int, list[dict]] = {}
    for e in db.query("select * from channel_ends order by position, id"):
        ends.setdefault(e["channel_id"], []).append(_end(e))
    return [_build(r, ends.get(r["id"], [])) for r in rows]


def listing(kind: str = "") -> list[dict]:
    """Every channel (one per merged group), oldest first, with its ends. `kind` narrows it."""
    return _roots(kind=kind) if kind in KINDS else _roots()


def _root_id(cid: int) -> int | None:
    seen: set[int] = set()
    while cid not in seen:
        seen.add(cid)
        row = db.one("select id, merged_into from channels where id = ?", (cid,))
        if row is None:
            return None
        if row["merged_into"] is None:
            return row["id"]
        cid = row["merged_into"]
    return None


def get(cid: int) -> dict | None:
    """The channel `cid` is now part of (following a merge), or None."""
    root = _root_id(cid)
    if root is None:
        return None
    got = _roots(only=root)
    return got[0] if got else None


def by_ref(text: Any) -> dict | None:
    cid = channel_id(text)
    return get(cid) if cid is not None else None


def of_gear(gear_id: int) -> list[dict]:
    """The channels with an end on this gear."""
    return [c for c in listing() if any(e["gear"] == gear_id for e in c["ends"])]


def of_provider(provider: str) -> list[dict]:
    return [c for c in listing() if provider in c["providers"]]


def describe(text: Any, fallback: str = "") -> dict:
    """What a page shows for a channel ref it saved beside a typed name: {"ref", "name", "missing", "found"}. A ref that
    is gone (or none) is `found` False and shows the typed name, so the page can flag a name-only channel."""
    got = by_ref(text)
    if got is None:
        return {"ref": "", "name": fallback, "missing": False, "found": False}
    return {"ref": got["ref"], "name": got["shown"], "missing": got["missing"], "found": True, "number": got["number"]}


# -- writing ------------------------------------------------------------------------------------------------
def _clean(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    key, kind = raw.get("key"), raw.get("kind")
    if not isinstance(key, str) or not KEY_RE.match(key) or kind not in KINDS:
        return None
    direction = raw.get("direction") if raw.get("direction") in DIRECTIONS else "source"
    return {"key": key, "kind": kind, "direction": direction, "number": _int(raw.get("number")),
            "gear_name": _text(raw.get("gear_name"), NAME_MAX)}


def sync(provider: str, reported: Iterable[dict], gear_id: int | None = None, within: str = "") -> dict[str, str]:
    """Every channel `provider` reports now (on `gear_id`, or with no gear), each
    {"key", "kind", "direction", "number", "gear_name"} (key and kind required). A new one gets a channel of its own; one
    already there is updated and comes back when it was missing; one this provider had on that gear and no longer reports
    is marked missing. `within` limits "no longer reported" to keys starting with it, for a provider that reports one
    machine at a time (OBS: "<machine id>:"). Returns {key: channel ref}, following any merge."""
    if not isinstance(provider, str) or not PROVIDER_RE.match(provider):
        return {}
    now = db.now()
    out: dict[str, str] = {}
    with db.transaction() as tx:
        if gear_id is not None and tx.one("select 1 x from gear where id = ?", (gear_id,)) is None:
            return {}
        have = {r["key"]: r for r in tx.query("select * from channel_ends where provider = ?", (provider,))}
        seen: set[str] = set()
        for pos, item in enumerate(reported):
            c = _clean(item)
            if c is None or c["key"] in seen or len(seen) >= MAX_PER_PROVIDER:
                continue
            seen.add(c["key"])
            old = have.get(c["key"])
            if old is None:
                if tx.one("select count(*) n from channels")["n"] >= MAX_CHANNELS:
                    log.warning("channels: too many to keep another from %s", provider)
                    continue
                tx.execute("insert into channels (kind, created_at) values (?, ?)", (c["kind"], now))
                cid = tx.one("select last_insert_rowid() id")["id"]
                tx.execute("insert into channel_ends (channel_id, provider, key, gear_id, direction, number, gear_name,"
                           " position, last_seen, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (cid, provider, c["key"], gear_id, c["direction"], c["number"], c["gear_name"], pos, now, now))
            else:
                tx.execute("update channel_ends set gear_id = ?, direction = ?, number = ?, gear_name = ?, position = ?,"
                           " missing = 0, last_seen = ? where id = ?",
                           (gear_id, c["direction"], c["number"], c["gear_name"], pos, now, old["id"]))
        for key, old in have.items():
            if key not in seen and not old["missing"] and old["gear_id"] == gear_id and key.startswith(within):
                tx.execute("update channel_ends set missing = 1 where id = ?", (old["id"],))
        for r in tx.query("select e.key, e.channel_id from channel_ends e where e.provider = ?", (provider,)):
            if r["key"] in seen:
                out[r["key"]] = ref(_follow(tx, r["channel_id"]))
    _touch()
    return out


def report(provider: str, reported: Iterable[dict], gear_id: int | None = None, within: str = "") -> dict[str, str] | None:
    """`sync` for a module that reports from a thread or a refresh loop: the database being down (or not yet migrated)
    is logged and answered with None, so a module's own work never stops for it. Otherwise the same answer as `sync`."""
    try:
        return sync(provider, list(reported), gear_id, within)
    except (db.Unavailable, sqlite3.Error):
        log.info("channels: the database is not ready, so %s's channels were not recorded", provider)
        return None


def _follow(tx, cid: int) -> int:
    for _ in range(64):
        row = tx.one("select merged_into from channels where id = ?", (cid,))
        if row is None or row["merged_into"] is None:
            return cid
        cid = row["merged_into"]
    return cid


def rename(cid: int, name: Any) -> None:
    """A Relay-only name for the channel. Empty text shows the reporting end's own name again."""
    root = _root_id(cid)
    if root is None or not db.execute("update channels set name = ? where id = ?", (_text(name, NAME_MAX), root)):
        raise Refused("no_channel")
    _touch()


def rename_on_device(cid: int, name: Any) -> None:
    """Send a new name to the gear for every end of this channel whose module can rename it, then show the gear's name
    (a Relay-only name is cleared, so the two cannot disagree). Nothing changes in Relay when the gear refuses."""
    text = _text(name, NAME_MAX)
    got = get(cid)
    if got is None:
        raise Refused("no_channel")
    if not text:
        raise Refused("name")
    ends = [e for e in got["ends"] if _renamable(e)]
    if not ends:
        raise Refused("no_device")
    for e in ends:
        try:
            _gear_api(e["provider"])["rename"](e["key"], text)  # type: ignore[index]
        except ValueError as exc:
            log.info("channels: %s refused the name for %s: %s", e["provider"], e["key"], exc)
            raise Refused("device") from exc
    with db.transaction() as tx:
        for e in ends:
            tx.execute("update channel_ends set gear_name = ? where id = ?", (text, e["id"]))
        tx.execute("update channels set name = '' where id = ?", (got["id"],))
    _touch()


def set_note(cid: int, note: Any) -> None:
    root = _root_id(cid)
    if root is None or not db.execute("update channels set note = ? where id = ?", (_text(note, NOTE_MAX), root)):
        raise Refused("no_channel")


def merge(keep: int, other: int) -> dict:
    """Make `other` part of `keep`: its ends move over, and its ref follows to `keep`. The name an operator typed on
    `keep` stays; `other`'s is used only when `keep` has none."""
    with db.transaction() as tx:
        a, b = _follow(tx, keep), _follow(tx, other)
        ra, rb = tx.one("select * from channels where id = ?", (a,)), tx.one("select * from channels where id = ?", (b,))
        if ra is None or rb is None:
            raise Refused("no_channel")
        if a == b:
            raise Refused("same")
        if ra["kind"] != rb["kind"]:
            raise Refused("kind")
        tx.execute("update channel_ends set channel_id = ? where channel_id = ?", (a, b))
        tx.execute("update channels set merged_into = ? where id = ?", (a, b))
        tx.execute("update channels set merged_into = ? where merged_into = ?", (a, b))
        if not ra["name"] and rb["name"]:
            tx.execute("update channels set name = ? where id = ?", (rb["name"], a))
        if not ra["note"] and rb["note"]:
            tx.execute("update channels set note = ? where id = ?", (rb["note"], a))
        tx.execute("delete from channel_apart where a in (?, ?) and b in (?, ?)", (a, b, a, b))
    _touch()
    return get(a)  # type: ignore[return-value]


def split(end_id: int) -> dict:
    """Take one end out of its channel into a channel of its own. Returns the new channel."""
    with db.transaction() as tx:
        e = tx.one("select * from channel_ends where id = ?", (end_id,))
        if e is None:
            raise Refused("no_end")
        if tx.one("select count(*) n from channel_ends where channel_id = ?", (e["channel_id"],))["n"] < 2:
            raise Refused("only_end")
        kind = tx.one("select kind from channels where id = ?", (e["channel_id"],))["kind"]
        tx.execute("insert into channels (kind, created_at) values (?, ?)", (kind, db.now()))
        cid = tx.one("select last_insert_rowid() id")["id"]
        tx.execute("update channel_ends set channel_id = ? where id = ?", (cid, end_id))
    _touch()
    return get(cid)  # type: ignore[return-value]


def decline(a: int, b: int) -> None:
    """Remember that these two channels are not the same, so they are not suggested again."""
    ra, rb = _root_id(a), _root_id(b)
    if ra is None or rb is None:
        raise Refused("no_channel")
    if ra == rb:
        raise Refused("same")
    lo, hi = sorted((ra, rb))
    db.execute("insert or ignore into channel_apart (a, b) values (?, ?)", (lo, hi))


def forget(cid: int) -> None:
    """Remove a channel none of whose ends is reported any more (its name goes with it)."""
    got = get(cid)
    if got is None:
        raise Refused("no_channel")
    if not got["missing"]:
        raise Refused("present")
    db.execute("delete from channels where id = ?", (got["id"],))
    _touch()


# -- suggestions --------------------------------------------------------------------------------------------
def _labels(c: dict) -> list[str]:
    out = [c["name"]] if c["name"] else []
    out += [e["gear_name"] for e in c["ends"] if e["gear_name"] and not e["missing"]]
    return [n for n in out if not GENERIC.match(n.strip())]


def _why(a: dict, b: dict) -> str:
    for x in _labels(a):
        for y in _labels(b):
            if same_name(x, y):
                return "name"
    for e in a["ends"]:
        for f in b["ends"]:
            if e["gear"] and e["gear"] == f["gear"] and e["number"] is not None and e["number"] == f["number"] \
                    and not e["missing"] and not f["missing"] and e["direction"] == f["direction"]:
                return "number"
    return ""


def suggestions() -> list[dict]:
    """Pairs of present channels that are probably one: {"a", "b", "why"} with the channels and "name" (the same name) or
    "number" (the same number on the same gear). A pair an operator declined is left out."""
    chans = [c for c in listing() if not c["missing"]]
    apart = {(r["a"], r["b"]) for r in db.query("select a, b from channel_apart")}
    out = []
    for i, a in enumerate(chans):
        for b in chans[i + 1:]:
            if a["kind"] != b["kind"] or (a["id"], b["id"]) in apart:
                continue
            why = _why(a, b)
            if why:
                out.append({"a": a, "b": b, "why": why})
    return out
