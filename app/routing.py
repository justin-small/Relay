"""Routes (#544, part of the gear/ports/channels epic #539): which source channel feeds which destination channel.

A route says "Lobby TV takes Camera 2" (`route:relay:<id>`), with a gain in dB where the gear has one. Both ends are core
channels (app/channels.py). Table: app/migrations/0015_routes.sql. A **salvo** is a saved set of routes taken in one click.

**Wanted and actual.** A route here is what was asked for. The module that owns the destination applies it and reports what
the gear really does. It offers `modules.provide("routes.<module id>", {...})` with:

* `exclusive`  True when a destination takes one source (a video output), False when it mixes several (an audio output);
* `gain`       True when the gear has a gain per route;
* `apply(end, wanted)`  point the destination end `end` (a channel end dict) at `wanted`, a list of
               {"ends": [the source channel's ends], "gain_db"}; pick the end you can use. Return "" or a sentence saying why not;
* `read(end)`  what the gear does now: a list of {"provider", "key", "gain_db"} naming source ends, or None when it cannot say;
* `permission` (optional) a permission that may route its destinations, besides network.manage (`may_route`).

The gear is the truth: `status` and `feeds` show what it reports and flag a difference from what was asked, and Relay never
quietly overwrites the gear. A module that loses its state on restart calls `reapply(provider)` when it starts.
Everything here blocks (database, gear): call it through `asyncio.to_thread`.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from . import channels, db, modules

log = logging.getLogger("relay.routing")

ROUTE = "route"
SALVO = "salvo"
KINDS = ("audio", "video")
MAX_SALVOS = 200
NAME_MAX = 60
SCOPE_MAX = 80
GAIN_MIN_DB, GAIN_MAX_DB = -144.0, 24.0

REFUSALS = {"no_channel": "That channel does not exist.", "no_route": "That route does not exist.",
            "same": "A channel cannot feed itself.", "kind": "Only audio and video can be routed, and both ends must be the same kind.",
            "no_destination": "That channel is not something that takes a signal.",
            "no_source": "That channel is not something that gives a signal.",
            "no_salvo": "That preset does not exist.", "name": "Give the preset a name.",
            "name_taken": "There is already a preset with that name.", "too_many": "That is as many presets as Relay keeps."}
NO_PROVIDER = "No module that is on can apply this, so Relay will when it is."


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


def ref(route_id: int) -> str:
    return f"{ROUTE}:relay:{route_id}"


def route_id(text: Any) -> int | None:
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != ROUTE or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


def _gain(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw:
        return None
    return round(max(GAIN_MIN_DB, min(GAIN_MAX_DB, float(raw))), 2)


def _cid(value: Any) -> int | None:
    """A channel id from an id or a ref, followed through any merge."""
    cid = value if isinstance(value, int) and not isinstance(value, bool) else channels.channel_id(value)
    return None if cid is None else channels.root_id(cid)


# -- providers ----------------------------------------------------------------------------------------------
_core: dict[str, dict] = {}


def provide_core(provider: str, api: dict) -> None:
    """Core code that owns destinations (the audio bus) offers the same interface a module does, under its provider id.
    It is always on, so nothing withdraws it."""
    _core[provider] = api


def _api(provider: str) -> dict | None:
    api = _core.get(provider) or modules.using(f"routes.{provider}")
    return api if isinstance(api, dict) and callable(api.get("apply")) else None


def _appliers(dest: dict) -> list[tuple[dict, dict]]:
    """[(destination end, provider api)] for each present destination end whose module is on and can apply routes."""
    out = []
    for e in dest["ends"]:
        api = _api(e["provider"])
        if api and e["direction"] == "destination" and not e["missing"]:
            out.append((e, api))
    return out


MANAGE = "network.manage"


def permissions(destination: Any) -> list[str]:
    """The permissions that may route `destination`: network.manage, plus the `permission` each module (or core owner)
    that applies it names in its routes API, so an NDI operator can route NDI outputs from the NDI page (#546)."""
    out = [MANAGE]
    dest = channels.get(_cid(destination) or 0)
    for _, api in _appliers(dest) if dest is not None else []:
        perm = api.get("permission")
        if isinstance(perm, str) and perm and perm not in out:
            out.append(perm)
    return out


def may_route(destination: Any, can) -> bool:
    """Whether a user whose `can(permission)` answers may change what `destination` takes."""
    return any(can(p) for p in permissions(destination))


def exclusive(dest: dict) -> bool:
    appliers = _appliers(dest)
    if not appliers:
        return dest["kind"] == "video"        # while no module can say, a picture goes to one place
    return any(api.get("exclusive") for _, api in appliers)


def has_gain(dest: dict) -> bool:
    return any(api.get("gain") for _, api in _appliers(dest))


# -- reading ------------------------------------------------------------------------------------------------
def _wanted_rows(dest_id: int) -> list[dict]:
    rows = db.query("select id, source, gain_db from routes where destination = ? order by id", (dest_id,))
    seen: dict[int, dict] = {}
    for r in rows:
        src = channels.root_id(r["source"])
        if src is not None and src != dest_id and src not in seen:
            seen[src] = {"id": r["id"], "source": src, "gain_db": r["gain_db"]}
    return list(seen.values())


def wanted(destination: Any) -> list[dict]:
    """What was asked for `destination`: [{"ends": the source channel's ends, "gain_db"}]. For a module that rebuilds its
    own state from the routes (the audio bus)."""
    cid = _cid(destination)
    world = _world()
    if cid is None or cid not in world:
        return []
    return [{"ends": world[s["source"]]["ends"], "gain_db": s["gain_db"]} for s in _wanted_rows(cid) if s["source"] in world]


def _dest_ids() -> list[int]:
    ids: list[int] = []
    for r in db.query("select distinct destination d from routes order by d"):
        root = channels.root_id(r["d"])
        if root is not None and root not in ids:
            ids.append(root)
    return ids


def _sources_of(sources: Iterable[dict], world: dict[int, dict]) -> list[dict]:
    out = []
    for s in sources:
        c = world.get(s["source"])
        if c is not None:
            out.append({"ref": c["ref"], "name": c["shown"], "missing": c["missing"], "gain_db": s.get("gain_db")})
    return sorted(out, key=lambda x: x["ref"])


def _actual(dest: dict, world: dict[int, dict]) -> list[dict] | None:
    """What the gear does for `dest`, as [{"source": id, "gain_db"}], or None when no module that is on can say."""
    found: dict[int, dict] = {}
    said = False
    ends = {(e["provider"], e["key"]): c["id"] for c in world.values() for e in c["ends"]}
    for end, api in _appliers(dest):
        read = api.get("read")
        if not callable(read):
            continue
        try:
            got = read(end)
        except Exception:  # a module's own read must not break a page
            log.exception("routing: %s could not read %s", end["provider"], end["key"])
            continue
        if got is None:
            continue
        said = True
        for item in got:
            cid = ends.get((item.get("provider", end["provider"]), item.get("key")))
            if cid is not None:
                found[cid] = {"source": cid, "gain_db": _gain(item.get("gain_db"))}
    return list(found.values()) if said else None


def _same(a: list[dict], b: list[dict], gain: bool) -> bool:
    key = (lambda x: (x["source"], x.get("gain_db"))) if gain else (lambda x: x["source"])
    return sorted(map(key, a)) == sorted(map(key, b))


def _view(dest: dict, world: dict[int, dict]) -> dict:
    wanted = _wanted_rows(dest["id"])
    actual = _actual(dest, world)
    gain = has_gain(dest)
    if actual is None:
        state = "wanted" if wanted else "none"
    elif not wanted:
        state = "actual" if actual else "none"
    else:
        state = "same" if _same(wanted, actual, gain) else "differs"
    shown = actual if actual is not None and state in ("actual", "differs", "same") else wanted
    return {"destination": dest["ref"], "name": dest["shown"], "kind": dest["kind"], "state": state,
            "gain": gain, "exclusive": exclusive(dest), "providers": dest["providers"],
            "wanted": _sources_of(wanted, world), "actual": None if actual is None else _sources_of(actual, world),
            "sources": _sources_of(shown, world)}


def _world() -> dict[int, dict]:
    return {c["id"]: c for c in channels.listing()}


def status(destination: Any) -> dict | None:
    """One destination's route: what was asked for, what the gear does, and whether they agree ("same", "differs",
    "wanted" (the gear cannot say), "actual" (the gear does something nobody asked for here) or "none")."""
    cid = _cid(destination)
    world = _world()
    dest = world.get(cid) if cid is not None else None
    return None if dest is None else _view(dest, world)


def listing(provider: str = "", gear_id: int | None = None) -> list[dict]:
    """Every destination that has a route, wanted or on the gear, shown with the sources it takes. `provider` and `gear_id`
    keep the destinations with an end there (a module's page lists its own)."""
    world = _world()
    out = []
    for dest in world.values():
        if not any(e["direction"] == "destination" for e in dest["ends"]):
            continue
        if provider and provider not in dest["providers"]:
            continue
        if gear_id is not None and not any(e["gear"] == gear_id for e in dest["ends"]):
            continue
        v = _view(dest, world)
        if v["state"] != "none":
            out.append(v)
    return out


def feeds(destination: Any) -> list[dict]:
    """What feeds this channel: [{"ref", "name", "gain_db", "missing"}], from the gear when it can say, else from what
    was asked for. Any page can ask this about any channel ref."""
    v = status(destination)
    return [] if v is None else v["sources"]


def feeding(source: Any) -> list[dict]:
    """Where this channel goes: the destinations that take it, [{"ref", "name"}]."""
    cid = _cid(source)
    if cid is None:
        return []
    return [{"ref": v["destination"], "name": v["name"]} for v in listing() if any(s["ref"] == channels.ref(cid) for s in v["sources"])]


def destinations(provider: str, gear_id: int | None = None) -> list[dict]:
    """Every channel that takes a signal on `provider` (and `gear_id`), routed or not, as {"ref", "name", "kind"}."""
    out = []
    for c in channels.listing():
        for e in c["ends"]:
            if e["provider"] == provider and e["direction"] == "destination" and not e["missing"] \
                    and (gear_id is None or e["gear"] == gear_id):
                out.append({"ref": c["ref"], "name": c["shown"], "kind": c["kind"]})
                break
    return out


# -- writing ------------------------------------------------------------------------------------------------
def _check(dest: dict | None, src: dict | None) -> None:
    if dest is None or src is None:
        raise Refused("no_channel")
    if dest["id"] == src["id"]:
        raise Refused("same")
    if dest["kind"] not in KINDS or dest["kind"] != src["kind"]:
        raise Refused("kind")
    if not any(e["direction"] == "destination" for e in dest["ends"]):
        raise Refused("no_destination")
    if not any(e["direction"] == "source" for e in src["ends"]):
        raise Refused("no_source")


def _push(dest: dict) -> list[str]:
    """Tell every module that owns an end of `dest` what it should take now. Sentences for what would not apply."""
    world = _world()
    wanted = [{"ends": world[s["source"]]["ends"], "gain_db": s["gain_db"]}
              for s in _wanted_rows(dest["id"]) if s["source"] in world]
    problems = []
    appliers = _appliers(dest)
    if not appliers:
        return [NO_PROVIDER]
    for end, api in appliers:
        try:
            said = api["apply"](end, wanted)
        except Exception:  # the gear's module failed; the route stays asked for
            log.exception("routing: %s could not apply %s", end["provider"], end["key"])
            said = "The gear did not take that."
        if said:
            problems.append(str(said))
    return problems


def replace(destination: Any, wanted: Iterable[tuple[Any, Any]], apply: bool = True) -> dict:
    """Make `wanted` ([(source channel, gain_db or None)]) the sources `destination` takes, and apply it. An empty list
    takes the destination off everything. Returns {"status": the destination's view, "problems": [sentences]}."""
    dest = channels.get(_cid(destination) or 0)
    if dest is None:
        raise Refused("no_channel")
    pairs: dict[int, float | None] = {}
    for src_ref, gain in wanted:
        src = channels.get(_cid(src_ref) or 0)
        _check(dest, src)
        pairs[src["id"]] = _gain(gain)
    if len(pairs) > 1 and exclusive(dest):
        raise Refused("kind")
    gains = has_gain(dest) or not _appliers(dest)
    now = db.now()
    with db.transaction() as tx:
        tx.execute("delete from routes where destination = ?", (dest["id"],))
        for src, gain in pairs.items():
            tx.execute("insert into routes (destination, source, gain_db, created_at, updated_at) values (?, ?, ?, ?, ?)",
                       (dest["id"], src, gain if gains else None, now, now))
    problems = _push(dest) if apply else []
    return {"status": status(dest["id"]), "problems": problems}


def set(destination: Any, source: Any, gain_db: Any = None, apply: bool = True) -> dict:  # noqa: A001
    """`destination` takes `source`. A destination that takes one source (video) drops the others; one that mixes (audio)
    keeps them and sets this one's gain."""
    dest, src = channels.get(_cid(destination) or 0), channels.get(_cid(source) or 0)
    _check(dest, src)
    keep = [] if exclusive(dest) else [(s["source"], s["gain_db"]) for s in _wanted_rows(dest["id"]) if s["source"] != src["id"]]
    return replace(dest["id"], keep + [(src["id"], gain_db)], apply)


def clear(destination: Any, source: Any = None, apply: bool = True) -> dict:
    """Take `destination` off `source`, or off everything when `source` is None."""
    dest = channels.get(_cid(destination) or 0)
    if dest is None:
        raise Refused("no_channel")
    drop = _cid(source) if source is not None else None
    keep = [] if source is None else [(s["source"], s["gain_db"]) for s in _wanted_rows(dest["id"]) if s["source"] != drop]
    return replace(dest["id"], keep, apply)


def reapply(provider: str) -> list[str]:
    """Apply every route asked for on a destination of `provider` again: for a module whose gear forgets its routes when
    it restarts. Skips a destination whose gear already agrees. Returns the sentences for what would not apply."""
    problems: list[str] = []
    world = _world()
    for did in _dest_ids():
        dest = world.get(did)
        if dest is None or provider not in dest["providers"]:
            continue
        if _view(dest, world)["state"] == "same":
            continue
        problems += _push(dest)
    return problems


# -- salvos -------------------------------------------------------------------------------------------------
def _scope(raw: Any) -> str:
    return " ".join(str(raw or "").split())[:SCOPE_MAX]


def _salvo(r: dict, rows: list[dict], names: dict[int, str]) -> dict:
    return {"id": r["id"], "ref": f"{SALVO}:relay:{r['id']}", "name": r["name"], "scope": r["scope"],
            "routes": [{"destination": channels.ref(x["destination"]), "destination_name": names.get(x["destination"], ""),
                        "source": channels.ref(x["source"]) if x["source"] else "",
                        "source_name": names.get(x["source"], "") if x["source"] else "", "gain_db": x["gain_db"]}
                       for x in rows]}


def salvos(scope: str | None = None) -> list[dict]:
    """Saved presets, all or one scope's."""
    names = {k: c["shown"] for k, c in _world().items()}
    rows: dict[int, list[dict]] = {}
    for x in db.query("select * from salvo_routes order by rowid"):
        rows.setdefault(x["salvo_id"], []).append(x)
    return [_salvo(r, rows.get(r["id"], []), names) for r in db.query("select * from salvos order by id")
            if scope is None or r["scope"] == _scope(scope)]


def save_salvo(name: Any, destinations: Iterable[Any], scope: str = "", sid: int | None = None) -> dict:
    """Save what each of `destinations` takes now (the gear's word, else what was asked) as a preset; with `sid` an
    existing preset is replaced. A destination that takes nothing is saved as taking nothing."""
    name = " ".join(str(name or "").split())[:NAME_MAX]
    if not name:
        raise Refused("name")
    scope = _scope(scope)
    world = _world()
    cells: list[tuple[int, int | None, float | None]] = []
    for d in destinations:
        did = _cid(d)
        dest = world.get(did) if did is not None else None
        if dest is None:
            raise Refused("no_channel")
        got = _view(dest, world)["sources"]
        if not got:
            cells.append((did, None, None))
        for s in got:
            cells.append((did, channels.channel_id(s["ref"]), s["gain_db"]))
    with db.transaction() as tx:
        old = tx.one("select id from salvos where id = ?", (sid,)) if sid else None
        if sid and old is None:
            raise Refused("no_salvo")
        if any(r["id"] != sid and r["scope"] == scope and r["name"].casefold() == name.casefold()  # allow-shared: a preset name is its own key
               for r in tx.query("select id, name, scope from salvos")):
            raise Refused("name_taken")
        if old is None:
            if tx.one("select count(*) n from salvos")["n"] >= MAX_SALVOS:
                raise Refused("too_many")
            tx.execute("insert into salvos (name, scope, created_at) values (?, ?, ?)", (name, scope, db.now()))
            sid = tx.one("select last_insert_rowid() id")["id"]
        else:
            tx.execute("update salvos set name = ?, scope = ? where id = ?", (name, scope, sid))
            tx.execute("delete from salvo_routes where salvo_id = ?", (sid,))
        for d, s, g in cells:
            tx.execute("insert or ignore into salvo_routes (salvo_id, destination, source, gain_db) values (?, ?, ?, ?)",
                       (sid, d, s, g))
    return next(x for x in salvos() if x["id"] == sid)


def import_salvo(name: Any, scope: str, cells: Iterable[tuple[Any, Any, Any]]) -> dict | None:
    """Keep a preset a module held before routes moved here: `cells` are (destination ref, source ref or None, gain_db).
    Nothing is applied. None when the name is already taken or a channel is gone."""
    name = " ".join(str(name or "").split())[:NAME_MAX]
    scope = _scope(scope)
    if not name:
        return None
    rows = []
    for d, src, gain in cells:
        did, sid_ = _cid(d), (_cid(src) if src else None)
        if did is None or (src and sid_ is None):
            continue
        rows.append((did, sid_, _gain(gain)))
    with db.transaction() as tx:
        if any(r["scope"] == scope and r["name"].casefold() == name.casefold()  # allow-shared: a preset name is its own key
               for r in tx.query("select name, scope from salvos")):
            return None
        tx.execute("insert into salvos (name, scope, created_at) values (?, ?, ?)", (name, scope, db.now()))
        sid = tx.one("select last_insert_rowid() id")["id"]
        for row in rows:
            tx.execute("insert or ignore into salvo_routes (salvo_id, destination, source, gain_db) values (?, ?, ?, ?)",
                       (sid, *row))
    return next((x for x in salvos() if x["id"] == sid), None)


def delete_salvo(sid: int) -> None:
    with db.transaction() as tx:
        if tx.one("select 1 x from salvos where id = ?", (sid,)) is None:
            raise Refused("no_salvo")
        tx.execute("delete from salvos where id = ?", (sid,))


def take_salvo(sid: int) -> list[str]:
    """Apply every route in a preset. Returns the sentences for what would not apply ([] when all did)."""
    if db.one("select 1 x from salvos where id = ?", (sid,)) is None:
        raise Refused("no_salvo")
    per: dict[int, list[tuple[int, float | None]]] = {}
    for x in db.query("select * from salvo_routes where salvo_id = ? order by rowid", (sid,)):
        per.setdefault(x["destination"], [])
        if x["source"] is not None:
            per[x["destination"]].append((x["source"], x["gain_db"]))
    problems: list[str] = []
    for dest, wanted in per.items():
        try:
            problems += replace(dest, wanted)["problems"]
        except Refused:
            problems.append("A channel in that preset is gone or no longer fits.")
    return problems
