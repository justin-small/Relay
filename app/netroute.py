"""Which network port each kind of Relay traffic uses (#344).

A traffic class is a named flow that can sit on its own port: core's `viewers`, `crew` and `discovery`, and the
classes each module declares in its manifest (`Manifest.traffic`), such as `dante` or `ndi.video`. The operator
routes a class to one of this machine's ports (app/netinfo.py); a class with no route follows the system default,
which is what Relay did before routes existed.

Every part of Relay asks `resolve(class_id)` which port and address to use. Modules never read
config["network"] themselves.

config.json keeps:

    "network": {
        "routes": {"dante": {"port": "port:relay:5", "key": "mac:aa:bb:cc:dd:ee:ff"}},   # class id -> port
        "policy": {"dante": "fallback"},                   # only where it differs from the class's default
    }

This computer's network ports are core ports (app/ports.py, #540) on the gear "relay:this": every hardware adapter Relay has
seen, so a route to a USB adapter that is unplugged right now survives a restart, and its name and note show on the
Network page. A route names its port by ref (`port`), and also by the adapter's id (`key`, netinfo's `Port.id`), which is
what resolving uses: a route resolves without the database, so Relay keeps routing Dante while the database is down.

Before #540 a route was the adapter id alone and `known_ports` listed every adapter seen; `startup()` turns them into
ports once the database is up.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from . import db, gear, netinfo, ports as port_rows
from .netinfo import Port

log = logging.getLogger("relay.netroute")

POLICIES = ("strict", "fallback")
_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}(\.[a-z][a-z0-9_]{0,31})?$")
_PORT_ID_RE = re.compile(r"^(mac|name):[^\x00-\x1f]{1,200}$")


@dataclass(frozen=True)
class Traffic:
    """A kind of traffic that can be routed to its own port.

    `media` marks audio or video: when its port is missing it is not run (policy "strict"), so Dante audio never
    ends up on the crew network unasked. Other classes use the default route instead ("fallback"). `exclusive`
    means the class needs a port no other exclusive class is routed to (a protocol that wants the address to
    itself)."""
    id: str
    label: str
    media: bool = False
    exclusive: bool = False

    @property
    def default_policy(self) -> str:
        return "strict" if self.media else "fallback"


CORE_TRAFFIC: tuple[Traffic, ...] = (
    Traffic("viewers", "Caption and translation pages (plain HTTP)"),
    Traffic("crew", "Operator panel, sign-in and crew pages (HTTPS)"),
    Traffic("discovery", "Relay's announcement to caption panels (mDNS)"),
    Traffic("translation", "Translation service (OpenAI, over the internet)"),
)


@dataclass(frozen=True)
class Resolved:
    """Where a traffic class goes right now."""
    traffic: str
    port_id: str | None           # the routed adapter's id (netinfo), None when the class follows the system default
    port: Port | None             # that port as the machine shows it now; None when it is not there
    address: str | None           # the IPv4 address to use; None when there is none
    available: bool               # the class can run: link up and an address present (or the default route used)
    reason: str = ""              # a short sentence when not available, or when it fell back
    policy: str = "fallback"
    fallback: bool = False        # the routed port is unavailable and the class uses the default route instead
    port_ref: str = ""            # the routed port as a core port ref ("port:relay:5"), "" when not known yet

    @property
    def routed(self) -> bool:
        return self.port_id is not None

    def as_dict(self) -> dict:
        return {"traffic": self.traffic, "port_id": self.port_id, "port_ref": self.port_ref,
                "port": self.port.as_dict() if self.port else None, "address": self.address, "available": self.available,
                "reason": self.reason, "policy": self.policy, "fallback": self.fallback}


# ------------------------------------------------------------------ the classes

def classes() -> dict[str, tuple[str | None, Traffic]]:
    """Every traffic class, core's then each module's (on or off), by id: (owning module id or None, Traffic)."""
    out: dict[str, tuple[str | None, Traffic]] = {t.id: (None, t) for t in CORE_TRAFFIC}
    from .modules import modules  # imported here: the registry imports this file for Traffic

    for mod in modules.all():
        for t in mod.manifest.traffic:
            out.setdefault(t.id, (mod.manifest.id, t))
    return out


def owns(module_id: str, class_id: str) -> bool:
    """Whether a module may declare a class: its id is the module's id, or starts with "<module id>."."""
    return class_id == module_id or class_id.startswith(module_id + ".")


# ------------------------------------------------------------------ config

_notices: list[str] = []
_logged: set[str] = set()


def notices() -> list[str]:
    """What the last clean() dropped or refused, as sentences for the operator."""
    return list(_notices)


def _note(text: str) -> None:
    _notices.append(text)
    if text not in _logged:  # clean() runs on every config load; say each thing once
        _logged.add(text)
        log.warning(text)


def _label(cls: dict, cid: str) -> str:
    return cls[cid][1].label if cid in cls else cid


def route_key(route: Any) -> str | None:
    """The adapter id a saved route resolves by: {"port", "key"} now, the bare id before #540."""
    if isinstance(route, dict):
        route = route.get("key")
    return route if isinstance(route, str) and _PORT_ID_RE.match(route) else None


def route_ref(route: Any) -> str:
    ref = route.get("port") if isinstance(route, dict) else ""
    return ref if port_rows.port_id(ref) is not None else ""


def route_problems(routes: dict[str, Any], cls: dict | None = None) -> dict[str, str]:
    """Why each route that cannot be kept is refused, by class id (empty when all are fine).

    Used by clean() and by anything that saves routes (the Network page), so both refuse the same things."""
    cls = classes() if cls is None else cls
    out: dict[str, str] = {}
    exclusive_on: dict[str, str] = {}
    for cid, route in routes.items():
        pid = route_key(route)
        if cid not in cls:
            out[cid] = f"There is no traffic called {cid!r}, so its network port setting was removed."
        elif pid is None:
            out[cid] = (f"{_label(cls, cid)} was set to a network port Relay cannot use, so it now uses "
                        "the system's default network.")
        elif cls[cid][1].exclusive:
            other = exclusive_on.get(pid)
            if other:
                out[cid] = (f"{_label(cls, cid)} needs a network port of its own, and "
                            f"{_label(cls, other)} is already on that port, so it now uses the system's default network.")
            else:
                exclusive_on[pid] = cid
    return out


def _clean_known(raw: Any) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for pid, info in raw.items():
        if not isinstance(pid, str) or not _PORT_ID_RE.match(pid) or len(out) >= 256:
            continue
        info = info if isinstance(info, dict) else {}
        out[pid] = {k: str(info.get(k) or "")[:100] for k in ("name", "friendly_name", "kind")}
    return out


def clean(raw: Any) -> dict:
    """config["network"], normalised: unknown classes, unusable ports, bad policies and clashes dropped. A route saved
    before #540 (the adapter id alone) becomes {"port": "", "key": id}; startup() fills in the port."""
    _notices.clear()
    raw = raw if isinstance(raw, dict) else {}
    cls = classes()
    routes_in = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
    routes = {str(c): p for c, p in routes_in.items() if isinstance(c, str) and _CLASS_RE.match(c)}
    problems = route_problems(routes, cls)
    for text in problems.values():
        _note(text)
    routes = {c: {"port": route_ref(p), "key": route_key(p)} for c, p in routes.items() if c not in problems}
    policy_in = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    policy = {c: p for c, p in policy_in.items()
              if c in cls and p in POLICIES and p != cls[c][1].default_policy}
    out = {"routes": routes, "policy": policy}
    known = _clean_known(raw.get("known_ports"))
    if known:
        out["known_ports"] = known                     # before #540; startup() moves them onto ports
    return out


def _network_cfg() -> dict:
    from . import config

    net = config.get().get("network")
    return net if isinstance(net, dict) else {"routes": {}, "policy": {}}


def _routes() -> dict[str, dict]:
    return {c: r for c, r in (_network_cfg().get("routes") or {}).items() if route_key(r)}


# ------------------------------------------------------------------ this computer's ports (core ports, #540)

RELAY_KEY = "relay:this"
_KINDS = {"ethernet": "eth", "wifi": "other", "thunderbolt": "other", "usb": "eth"}


def relay_gear() -> dict:
    """This computer, as gear."""
    return gear.by_key(RELAY_KEY) or gear.adopt([RELAY_KEY], name="This Relay computer", kind="relay", heard=False)


def _reported(p: Port) -> dict:
    return {"key": p.id, "kind": _KINDS.get(p.kind, "other"), "direction": "both", "gear_name": p.friendly_name or p.name,
            "link_up": p.up, "speed_mbps": p.speed_mbps}


def remember(ports_now: list[Port]) -> list[dict]:
    """This computer's hardware network ports as core ports: one row each, refreshed now; one that is gone is marked
    missing. A virtual port (VPN, bridge) is kept only while a route uses it. Returns the rows."""
    gid = relay_gear()["id"]
    routed = {route_key(r) for r in _routes().values()}
    have = {r["key"] for r in port_rows.of_gear(gid)}
    show = [p for p in ports_now if _PORT_ID_RE.match(p.id) and (not p.virtual or p.id in routed or p.id in have)]
    return port_rows.sync(gid, [_reported(p) for p in show])


def _port_for(key: str) -> dict | None:
    """The core port of this computer's adapter `key`, made when it is connected now and Relay has no row for it."""
    gid = relay_gear()["id"]
    row = port_rows.by_key(gid, key)
    if row is None:
        present = next((p for p in ports() if p.id == key), None)
        if present is not None:
            row = port_rows.adopt(gid, key, **{k: v for k, v in _reported(present).items()
                                               if k in ("kind", "direction", "gear_name")})
    return row


def migrate() -> bool:
    """Routes and known ports saved before #540 move onto core ports, once. True when config was written."""
    from . import config

    net = _network_cfg()
    routes = net.get("routes") or {}
    known = _clean_known(net.get("known_ports"))
    if not known and all(route_ref(r) for r in routes.values()):
        return False
    gid = relay_gear()["id"]
    for key, info in known.items():
        if _PORT_ID_RE.match(key):
            port_rows.adopt(gid, key, kind=_KINDS.get(info.get("kind", ""), "other"),
                            gear_name=info.get("friendly_name") or info.get("name") or "")
    fixed = {}
    for cid, r in routes.items():
        key = route_key(r)
        row = port_rows.by_key(gid, key) if key else None
        if key and row is None:
            row = port_rows.adopt(gid, key, heard=False)
        fixed[cid] = {"port": row["ref"] if row else "", "key": key}
    config.save({"network": {"routes": fixed, "policy": net.get("policy") or {}}}, replace=("network",))
    return True


# ------------------------------------------------------------------ resolving

_lock = threading.Lock()
_ports: list[Port] | None = None


def update(ports: list[Port]) -> None:
    """The machine's current ports (from netinfo.watch), so resolve() never has to read the interfaces itself."""
    global _ports
    with _lock:
        _ports = list(ports)


def ports() -> list[Port]:
    """The last port list update() was given, read once from the machine if there has been none."""
    global _ports
    with _lock:
        if _ports is None:
            _ports = netinfo.interfaces()
        return list(_ports)


def _first_address(port: Port) -> str | None:
    """The address to use on a port: a routable one first, then a link-local one (a Dante network with no DHCP)."""
    addrs = port.addresses
    usable = [a for a in addrs if netinfo._usable(a)]
    return (usable or addrs or [None])[0]


def _default(cid: str, policy: str, reason: str = "", fallback: bool = False) -> Resolved:
    ips = netinfo.lan_ips()
    port = next((p for p in ports() if ips and ips[0] in p.addresses), None)
    if not ips:
        return Resolved(cid, None, port, None, False, reason or "This computer has no network address.", policy, fallback)
    return Resolved(cid, None, port, ips[0], True, reason, policy, fallback)


_names: dict[str, str] = {}      # adapter id -> the name its port row had, for a port that is unplugged now


def _port_name(port: Port | None, pid: str, net: dict | None = None) -> str:
    """The name to say for an adapter: as the machine shows it, else as its core port was last seen (kept in memory, so
    a missing adapter is still named while the database is down)."""
    if port:
        return port.friendly_name or port.name
    if pid not in _names:
        try:
            row = port_rows.by_key(relay_gear()["id"], pid)
            if row:
                _names[pid] = row["name"] or row["gear_name"]
        except db.Unavailable:
            pass
    info = ((net or {}).get("known_ports") or {}).get(pid) or {}
    return _names.get(pid) or info.get("friendly_name") or info.get("name") or "the chosen network port"


def resolve(class_id: str) -> Resolved:
    """Where a traffic class should go now. Raises KeyError for a class nobody declared."""
    cls = classes()
    if class_id not in cls:
        raise KeyError(class_id)
    traffic = cls[class_id][1]
    net = _network_cfg()
    policy = (net.get("policy") or {}).get(class_id) or traffic.default_policy
    route = (net.get("routes") or {}).get(class_id)
    pid = route_key(route)
    if not pid:
        return _default(class_id, policy)
    ref = route_ref(route)
    port = next((p for p in ports() if p.id == pid), None)
    name = _port_name(port, pid, net)
    address = _first_address(port) if port else None
    if port is None:
        problem = f"{traffic.label}: {name} is not connected to this computer."
    elif port.up is False:
        problem = f"{traffic.label}: {name} is down (no cable or no link)."
    elif not address:
        problem = f"{traffic.label}: {name} has no network address."
    else:
        return Resolved(class_id, pid, port, address, True, "", policy, port_ref=ref)
    if policy == "fallback":
        fell = _default(class_id, policy, problem + " Using the system's default network instead.", fallback=True)
        return Resolved(class_id, pid, port, fell.address, fell.available, fell.reason, policy, True, ref)
    return Resolved(class_id, pid, port, None, False, problem, policy, port_ref=ref)


def link_addresses(class_id: str) -> list[str]:
    """The addresses to put in links and QR codes for a class's pages (#346): its routed port's address while that
    port is up; none while a strict route's port is missing (only this computer reaches it then); every LAN
    address, default route's first, when the class follows the system default."""
    r = resolve(class_id)
    if r.routed and not r.fallback:
        return [r.address] if r.available and r.address else []
    return netinfo.lan_ips()


def module_resolved(module_id: str) -> dict[str, Resolved]:
    """resolve() for every class a module declares."""
    from .modules import modules

    mod = modules.get(module_id)
    return {t.id: resolve(t.id) for t in (mod.manifest.traffic if mod else ())}


def health_problem(module_id: str) -> str:
    """A sentence when a routed class of the module cannot use its port: a strict class that cannot run, or one
    that fell back to the default network (#348: "Dante: USB LAN is down"); "" when nothing is wrong."""
    try:
        problems = [r.reason for r in module_resolved(module_id).values()
                    if r.routed and r.reason and ((r.policy == "strict" and not r.available) or r.fallback)]
    except Exception:
        log.exception("could not resolve the network routes of module %s", module_id)
        return ""
    return " ".join(problems)


# ------------------------------------------------------------------ telling modules

_last: dict[str, dict[str, tuple]] = {}


def _key(r: Resolved) -> tuple:
    return (r.port_id, r.port.id if r.port else None, r.address, r.available, r.fallback)


def refresh() -> list[str]:
    """Tell each running module whose classes now resolve differently (`Module.network_changed`).

    Called after every network change and after routes are saved. A module that has just started is only
    recorded: it resolved its classes itself in start(). Returns the ids of the modules that were told."""
    from .modules import modules

    told: list[str] = []
    running = {m.manifest.id: m for m in modules.all() if m.manifest.traffic and modules.running(m.manifest.id)}
    for mid in list(_last):
        if mid not in running:
            del _last[mid]
    for mid, mod in running.items():
        try:
            now = module_resolved(mid)
        except Exception:
            log.exception("could not resolve the network routes of module %s", mid)
            continue
        keys = {c: _key(r) for c, r in now.items()}
        before = _last.get(mid)
        _last[mid] = keys
        if before is None or before == keys:
            continue
        try:
            mod.network_changed(now)
            told.append(mid)
        except Exception:
            log.exception("module %s failed to handle a network change", mid)
    return told


def _remember_quietly(ports_now: list[Port]) -> None:
    try:
        remember(ports_now)
    except db.Unavailable:
        log.warning("the database is down, so this computer's network ports were not recorded")
    except Exception:
        log.exception("could not remember the network ports")


def startup() -> None:
    """Read the ports once at startup, record them as core ports and move routes saved before #540 onto them. Blocks
    (it reads the interfaces)."""
    now = netinfo.interfaces()
    update(now)
    try:
        migrate()
    except db.Unavailable:
        log.warning("the database is down, so saved network routes were not moved onto ports yet")
    except Exception:
        log.exception("could not move the saved network routes onto ports")
    _remember_quietly(now)                 # after migrate: a known port that is not connected is marked missing


def on_network_change(ports_now: list[Port]) -> None:
    """netinfo.watch saw a change: keep the port list, record this computer's ports and tell modules what moved."""
    update(ports_now)
    _remember_quietly(ports_now)
    refresh()


# ------------------------------------------------------------------ the Network page (#347)

def _status(cid: str, r: Resolved, owner: str | None) -> tuple[str, str]:
    """(chip, sentence) for a class: default, ok, changed, fallback, down or missing."""
    if not r.routed:
        return "default", "Uses the system's default network."
    if r.fallback:
        return "fallback", r.reason
    if not r.available:
        return ("missing" if r.port is None else "down"), r.reason
    from . import serving

    served = [g for g in serving.groups() if g.traffic == cid]
    if any(r.address not in g.hosts for g in served):
        return "changed", f"{r.address} is new; Relay is moving its listener there."
    told = _last.get(owner or "", {}).get(cid)
    if told is not None and told[2] != r.address:
        return "changed", f"{r.address} is new; the module is being told."
    return "ok", ""


def _port_label(pid: str, net: dict | None = None) -> str:
    port = next((p for p in ports() if p.id == pid), None)
    return _port_name(port, pid, net) if (port or pid) else pid


def _port_rows(net: dict, routes: dict, on_classes: list) -> list[dict]:
    """This computer's ports for the Network page: every one Relay keeps (connected or not), as core ports, with the
    machine's live view of those connected now. Without the database, only the connected ones."""
    now = {p.id: p for p in ports()}
    try:
        stored = {r["key"]: r for r in port_rows.of_gear(relay_gear()["id"])}
    except db.Unavailable:
        stored = {}
    out = []
    for key in list(stored) + [k for k in now if k not in stored]:      # a virtual one is kept once routed
        p, row = now.get(key), stored.get(key)
        if row:
            _names[key] = row["name"] or row["gear_name"]
        base = p.as_dict() if p else {"id": key, "name": "", "friendly_name": row["gear_name"] if row else "",
                                      "kind": "other", "ipv4": [], "up": None, "mac": None}
        base.update(id=row["ref"] if row else key, key=key, present=p is not None, known=row is not None,
                    note=row["note"] if row else "", relay_name=row["name"] if row else "",
                    classes=[cid for cid, _o, _t in on_classes if route_key(routes.get(cid)) == key])
        out.append(base)
    return out


def overview() -> dict:
    """Everything the Network page shows: every port seen (present or not), every class of core and of the modules
    that are on with its route, policy and status, and warnings. A port's `id` is its core port ref ("port:relay:5");
    `key` is the adapter's id. Port ids and names only; nothing free text."""
    from . import netsock
    from .modules import Module, modules

    net = _network_cfg()
    routes = net.get("routes") or {}
    now = {p.id: p for p in ports()}
    cls = classes()
    on_classes = [(cid, owner, t) for cid, (owner, t) in cls.items() if owner is None or modules.enabled(owner)]
    port_rows_out = _port_rows(net, routes, on_classes)
    by_key = {r["key"]: r["id"] for r in port_rows_out}

    class_rows, warnings = [], []
    for cid, owner, t in on_classes:
        r = resolve(cid)
        chip, sentence = _status(cid, r, owner)
        mod = modules.get(owner) if owner else None
        class_rows.append({
            "id": cid, "label": t.label, "owner": mod.manifest.name if mod else "Relay", "module": owner,
            "media": t.media, "exclusive": t.exclusive,
            "port_id": route_ref(routes.get(cid)) or by_key.get(route_key(routes.get(cid)) or "") or None,
            "policy": r.policy, "default_policy": t.default_policy, "status": chip, "detail": sentence,
            "address": r.address, "has_module_test": bool(mod and type(mod).test is not Module.test),
        })
        if r.routed and r.port is not None and r.port.link_local_only:
            warnings.append(f"{t.label} is on {_port_label(r.port_id or '', net)}, which has only a self-assigned "
                            "(169.254) address: only devices on that same network without DHCP can reach it.")
        if r.routed and not r.fallback:
            warnings.extend(netsock.notes(r))
    for a, b, network in netsock.overlapping(list(now.values())):
        warnings.append(f"{a.friendly_name or a.name} and {b.friendly_name or b.name} are both on {network}: this "
                        "computer cannot reliably choose between them. Put them on different networks.")
    for p in now.values():
        if not p.ipv4 and p.up is not False and not p.virtual:
            warnings.append(f"{p.friendly_name or p.name} is connected but has no address.")
    return {"ports": port_rows_out, "classes": class_rows, "warnings": list(dict.fromkeys(warnings)),
            "notices": notices(), "policies": list(POLICIES)}


def _panel_hosts(port_id: str | None, policy: str) -> list[str] | None:
    """The addresses the operator panel would answer on with this crew route, or None for "as configured"."""
    if not port_id:
        return None
    port = next((p for p in ports() if p.id == port_id), None)
    if port is not None and port.up is not False and port.addresses:
        return [*port.addresses, "127.0.0.1"]
    return ["127.0.0.1"] if policy == "strict" else None


class RouteRefused(ValueError):
    """A route change that cannot be saved. `message` is the fixed sentence for the page, the only text that may
    reach a browser (never str() of the exception: CodeQL treats that as exposing exception details)."""

    def __init__(self, message: str):
        super().__init__("route refused")
        self.message = message


def change(class_id: str, port_id: str | None, policy: str | None, browsing_from: str | None) -> Resolved:
    """Route a class to a port (None: the system default) and set its policy, as the Network page asks.

    Refuses an unknown class, a port Relay has never seen, an exclusive clash, and any change that would take the
    operator panel off the address the admin is using (#347: make that change from this computer or from a browser
    on the new network). Applies at once: modules are told and listeners move (app/serving.py)."""
    from . import config, serving

    cls = classes()
    if class_id not in cls:
        raise RouteRefused("There is no such traffic.")
    traffic = cls[class_id][1]
    net = _network_cfg()
    routes = dict(net.get("routes") or {})
    policies = dict(net.get("policy") or {})
    if port_id:
        routes[class_id] = _chosen(port_id)
    else:
        routes.pop(class_id, None)
    if policy is not None:
        if policy not in POLICIES:
            raise RouteRefused("Choose strict or use the default network.")
        policies[class_id] = policy
    problems = route_problems(routes, cls)
    if class_id in problems:
        raise RouteRefused(problems[class_id])
    if class_id == "crew" and browsing_from and not browsing_from.startswith("127."):
        hosts = _panel_hosts(route_key(routes.get("crew")), policies.get("crew") or traffic.default_policy)
        if hosts is not None and browsing_from not in hosts:
            raise RouteRefused(f"This would take the operator panel off {browsing_from}, the address you are using. "
                               "Make this change on this computer (https://127.0.0.1/admin) or from a browser on "
                               "the new network.")
    config.save({"network": {"routes": routes, "policy": policies}}, replace=("network",))
    refresh()
    serving.rebind_soon()
    return resolve(class_id)


def _chosen(port_id: str) -> dict:
    """The route to a port the page picked: a core port ref of this computer, or the id of an adapter connected now."""
    try:
        gid = relay_gear()["id"]
        row = port_rows.by_ref(port_id)
        if row is None and port_rows.port_id(port_id) is None:
            row = _port_for(port_id)
    except db.Unavailable:
        raise RouteRefused("The database is down, so routes cannot be changed right now.") from None
    if row is None or row["gear_id"] != gid:
        raise RouteRefused("Choose one of the network ports listed on this page.")
    return {"port": row["ref"], "key": row["key"]}


def forget(port_id: str) -> None:
    """Forget a port of this computer that is not connected and carries no route (a core port ref)."""
    try:
        row = port_rows.by_ref(port_id)
        if row is None or row["gear_id"] != relay_gear()["id"]:
            raise RouteRefused("Relay does not know that network port.")
        if any(p.id == row["key"] for p in ports()):
            raise RouteRefused("That network port is connected; only one that is gone can be forgotten.")
        if any(route_key(r) == row["key"] for r in (_network_cfg().get("routes") or {}).values()):
            raise RouteRefused("Move the traffic on that network port elsewhere first.")
        port_rows.forget(row["id"], gone=True)
    except db.Unavailable:
        raise RouteRefused("The database is down, so ports cannot be forgotten right now.") from None
