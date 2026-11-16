"""Which network port each kind of Relay traffic uses (#344).

A traffic class is a named flow that can sit on its own port: core's `viewers`, `crew` and `discovery`, and the
classes each module declares in its manifest (`Manifest.traffic`), such as `dante` or `ndi.video`. The operator
routes a class to one of this machine's ports (app/netinfo.py); a class with no route follows the system default,
which is what Relay did before routes existed.

Every part of Relay asks `resolve(class_id)` which port and address to use. Modules never read
config["network"] themselves.

config.json keeps:

    "network": {
        "routes": {"dante": "mac:aa:bb:cc:dd:ee:ff"},     # class id -> port id
        "policy": {"dante": "fallback"},                   # only where it differs from the class's default
        "known_ports": {"mac:aa:...": {"name": "en5", "friendly_name": "USB 10/100/1000 LAN", "kind": "ethernet"}}
    }

`known_ports` is every port Relay has seen, so a route to a USB adapter that is unplugged right now survives
a restart. A route to a port id that was never seen is dropped with a notice, never an error.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any

from . import netinfo
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
)


@dataclass(frozen=True)
class Resolved:
    """Where a traffic class goes right now."""
    traffic: str
    port_id: str | None           # the routed port, None when the class follows the system default
    port: Port | None             # that port as the machine shows it now; None when it is not there
    address: str | None           # the IPv4 address to use; None when there is none
    available: bool               # the class can run: link up and an address present (or the default route used)
    reason: str = ""              # a short sentence when not available, or when it fell back
    policy: str = "fallback"
    fallback: bool = False        # the routed port is unavailable and the class uses the default route instead

    @property
    def routed(self) -> bool:
        return self.port_id is not None

    def as_dict(self) -> dict:
        return {"traffic": self.traffic, "port_id": self.port_id, "port": self.port.as_dict() if self.port else None,
                "address": self.address, "available": self.available, "reason": self.reason,
                "policy": self.policy, "fallback": self.fallback}


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


def route_problems(routes: dict[str, str], known: dict[str, Any], cls: dict | None = None) -> dict[str, str]:
    """Why each route that cannot be kept is refused, by class id (empty when all are fine).

    Used by clean() and by anything that saves routes (the Network page), so both refuse the same things."""
    cls = classes() if cls is None else cls
    out: dict[str, str] = {}
    exclusive_on: dict[str, str] = {}
    for cid, pid in routes.items():
        if cid not in cls:
            out[cid] = f"There is no traffic called {cid!r}, so its network port setting was removed."
        elif not isinstance(pid, str) or pid not in known:
            out[cid] = (f"{_label(cls, cid)} was set to a network port Relay has never seen, so it now uses "
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
    """config["network"], normalised: unknown classes, unknown ports, bad policies and clashes dropped."""
    _notices.clear()
    raw = raw if isinstance(raw, dict) else {}
    cls = classes()
    known = _clean_known(raw.get("known_ports"))
    routes_in = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
    routes = {str(c): p for c, p in routes_in.items() if isinstance(c, str) and _CLASS_RE.match(c)}
    problems = route_problems(routes, known, cls)
    for text in problems.values():
        _note(text)
    routes = {c: p for c, p in routes.items() if c not in problems}
    policy_in = raw.get("policy") if isinstance(raw.get("policy"), dict) else {}
    policy = {c: p for c, p in policy_in.items()
              if c in cls and p in POLICIES and p != cls[c][1].default_policy}
    return {"routes": routes, "policy": policy, "known_ports": known}


def _network_cfg() -> dict:
    from . import config

    net = config.get().get("network")
    return net if isinstance(net, dict) else {"routes": {}, "policy": {}, "known_ports": {}}


def remember(ports: list[Port]) -> bool:
    """Add ports Relay has not seen before (or that were renamed) to known_ports. True when config was written.

    Writes only when something is new, so the 5-second network watch does not rewrite config.json."""
    from . import config

    known = dict(_network_cfg().get("known_ports") or {})
    changed = False
    for p in ports:
        entry = {"name": p.name[:100], "friendly_name": p.friendly_name[:100], "kind": p.kind}
        if known.get(p.id) != entry and _PORT_ID_RE.match(p.id):
            known[p.id] = entry
            changed = True
    if changed:
        config.save({"network": {"known_ports": known}})
    return changed


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


def _port_name(port: Port | None, pid: str, known: dict) -> str:
    if port:
        return port.friendly_name or port.name
    info = known.get(pid) or {}
    return info.get("friendly_name") or info.get("name") or "the chosen network port"


def resolve(class_id: str) -> Resolved:
    """Where a traffic class should go now. Raises KeyError for a class nobody declared."""
    cls = classes()
    if class_id not in cls:
        raise KeyError(class_id)
    traffic = cls[class_id][1]
    net = _network_cfg()
    policy = (net.get("policy") or {}).get(class_id) or traffic.default_policy
    pid = (net.get("routes") or {}).get(class_id)
    if not pid:
        return _default(class_id, policy)
    port = next((p for p in ports() if p.id == pid), None)
    name = _port_name(port, pid, net.get("known_ports") or {})
    address = _first_address(port) if port else None
    if port is None:
        problem = f"{traffic.label}: {name} is not connected to this computer."
    elif port.up is False:
        problem = f"{traffic.label}: {name} is down (no cable or no link)."
    elif not address:
        problem = f"{traffic.label}: {name} has no network address."
    else:
        return Resolved(class_id, pid, port, address, True, "", policy)
    if policy == "fallback":
        fell = _default(class_id, policy, problem + " Using the system's default network instead.", fallback=True)
        return Resolved(class_id, pid, port, fell.address, fell.available, fell.reason, policy, True)
    return Resolved(class_id, pid, port, None, False, problem, policy)


def module_resolved(module_id: str) -> dict[str, Resolved]:
    """resolve() for every class a module declares."""
    from .modules import modules

    mod = modules.get(module_id)
    return {t.id: resolve(t.id) for t in (mod.manifest.traffic if mod else ())}


def health_problem(module_id: str) -> str:
    """A sentence when a routed, strict class of the module cannot run; "" when nothing is wrong."""
    try:
        problems = [r.reason for r in module_resolved(module_id).values()
                    if r.routed and r.policy == "strict" and not r.available]
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


def startup() -> None:
    """Read the ports once at startup and remember any new ones. Blocks (it reads the interfaces)."""
    now = netinfo.interfaces()
    update(now)
    try:
        remember(now)
    except Exception:
        log.exception("could not remember the network ports")


def on_network_change(ports_now: list[Port]) -> None:
    """netinfo.watch saw a change: keep the port list, remember new ports and tell modules what moved."""
    update(ports_now)
    try:
        remember(ports_now)
    except Exception:
        log.exception("could not remember the network ports")
    refresh()
