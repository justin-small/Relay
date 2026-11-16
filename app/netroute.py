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


def _port_label(pid: str, known: dict) -> str:
    port = next((p for p in ports() if p.id == pid), None)
    if port:
        return port.friendly_name or port.name
    info = known.get(pid) or {}
    return info.get("friendly_name") or info.get("name") or pid


def overview() -> dict:
    """Everything the Network page shows: every port seen (present or not), every class of core and of the modules
    that are on with its route, policy and status, and warnings. Port ids and names only; nothing free text."""
    from . import netsock
    from .modules import Module, modules

    net = _network_cfg()
    known = net.get("known_ports") or {}
    routes = net.get("routes") or {}
    now = {p.id: p for p in ports()}
    cls = classes()
    on_classes = [(cid, owner, t) for cid, (owner, t) in cls.items() if owner is None or modules.enabled(owner)]

    port_rows = []
    for pid in list(now) + [k for k in known if k not in now]:
        p = now.get(pid)
        info = known.get(pid) or {}
        row = p.as_dict() if p else {"id": pid, "name": info.get("name") or "", "friendly_name": info.get("friendly_name") or "",
                                     "kind": info.get("kind") or "other", "ipv4": [], "up": None, "mac": None}
        row["present"] = p is not None
        row["known"] = pid in known
        row["classes"] = [cid for cid, _o, _t in on_classes if routes.get(cid) == pid]
        port_rows.append(row)

    class_rows, warnings = [], []
    for cid, owner, t in on_classes:
        r = resolve(cid)
        chip, sentence = _status(cid, r, owner)
        mod = modules.get(owner) if owner else None
        class_rows.append({
            "id": cid, "label": t.label, "owner": mod.manifest.name if mod else "Relay", "module": owner,
            "media": t.media, "exclusive": t.exclusive, "port_id": routes.get(cid),
            "policy": r.policy, "default_policy": t.default_policy, "status": chip, "detail": sentence,
            "address": r.address, "has_module_test": bool(mod and type(mod).test is not Module.test),
        })
        if r.routed and r.port is not None and r.port.link_local_only:
            warnings.append(f"{t.label} is on {_port_label(r.port_id or '', known)}, which has only a self-assigned "
                            "(169.254) address: only devices on that same network without DHCP can reach it.")
        if r.routed and not r.fallback:
            warnings.extend(netsock.notes(r))
    for a, b, network in netsock.overlapping(list(now.values())):
        warnings.append(f"{a.friendly_name or a.name} and {b.friendly_name or b.name} are both on {network}: this "
                        "computer cannot reliably choose between them. Put them on different networks.")
    for p in now.values():
        if not p.ipv4 and p.up is not False and not p.virtual:
            warnings.append(f"{p.friendly_name or p.name} is connected but has no address.")
    return {"ports": port_rows, "classes": class_rows, "warnings": list(dict.fromkeys(warnings)),
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
    known = net.get("known_ports") or {}
    routes = dict(net.get("routes") or {})
    policies = dict(net.get("policy") or {})
    if port_id:
        if port_id not in known:
            present = next((p for p in ports() if p.id == port_id), None)
            if present is None:
                raise RouteRefused("Choose one of the network ports listed on this page.")
            remember([present])
            net = _network_cfg()
            known = net.get("known_ports") or {}
        routes[class_id] = port_id
    else:
        routes.pop(class_id, None)
    if policy is not None:
        if policy not in POLICIES:
            raise RouteRefused("Choose strict or use the default network.")
        policies[class_id] = policy
    problems = route_problems(routes, known, cls)
    if class_id in problems:
        raise RouteRefused(problems[class_id])
    if class_id == "crew" and browsing_from and not browsing_from.startswith("127."):
        hosts = _panel_hosts(routes.get("crew"), policies.get("crew") or traffic.default_policy)
        if hosts is not None and browsing_from not in hosts:
            raise RouteRefused(f"This would take the operator panel off {browsing_from}, the address you are using. "
                               "Make this change on this computer (https://127.0.0.1/admin) or from a browser on "
                               "the new network.")
    config.save({"network": {**net, "routes": routes, "policy": policies}}, replace=("network",))
    refresh()
    serving.rebind_soon()
    return resolve(class_id)


def forget(port_id: str) -> None:
    """Forget a port that is not connected and carries no route."""
    from . import config

    net = _network_cfg()
    known = dict(net.get("known_ports") or {})
    if port_id not in known:
        raise RouteRefused("Relay does not know that network port.")
    if any(p.id == port_id for p in ports()):
        raise RouteRefused("That network port is connected; only one that is gone can be forgotten.")
    if port_id in (net.get("routes") or {}).values():
        raise RouteRefused("Move the traffic on that network port elsewhere first.")
    known.pop(port_id)
    config.save({"network": {**net, "known_ports": known}}, replace=("network",))
