"""The network map (#437): every device Relay can see, and which of them carry NDI video or Dante audio.

A core page that modules feed. Core imports no module: a module that knows devices overrides `Module.network_map()`
and returns plain dicts (see DEVICE below); this file asks every module that does, merges what comes back with
what core itself sees, and answers /api/admin/network/map. Turning a module off removes only what it added.

Sources
  relay      core, always on: this computer and its network ports (app/netinfo.py).
  discovery  core, always on: NDI (_ndi._tcp) and Dante (_netaudio-*) announced over mDNS (app/mdns.py). It only
             listens and calls no product's API, so it stays core (decided 2026-11-16).
  modules    one per module that overrides network_map(), while it is on: UniFi (switches, APs, which port each
             device is on), ATEM, OBS and ProPresenter (they name the gear they know).

Merge: devices are one device when they share a MAC address, else an IP address, else the same host name (a Mac with
two network ports answers once on each, and is one machine). A device is NDI or Dante when any source says so; one
that says it is not, against another that says it is, leaves a note. If a source fails its devices stay, greyed, with
the age of the last good read, and the rest stays live.

A module's device (every key optional but one of mac, ip or host):
    {"mac": "aa:bb:cc:dd:ee:ff", "ip": "192.168.1.20", "host": "stage-pc", "name": "Stage PC", "kind": "switch",
     "ndi": True, "dante": None, "uplink_mac": "...", "uplink_port": 12, "port_name": "Stage box 1", "speed": 1000,
     "poe_w": 3.2, "errors": 0, "tags": ["OBS Studio"], "identified_by": ["traffic flows"], "ports": [ ... ]}
`ndi`/`dante` are True (it does), False (it does not) or None (not known). Nothing here may be a secret.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
from typing import Any, Callable

from . import db, gear, mdns, netinfo, screens

log = logging.getLogger("relay")

FRESH_S = 20.0               # a snapshot younger than this is served again
KINDS = ("gateway", "switch", "ap", "relay", "computer", "av", "client", "other")
_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_last: dict[str, tuple[float, list[dict]]] = {}      # source id → (when, devices) of its last good read
_cache: dict[str, Any] = {"at": 0.0, "result": None}


# ------------------------------------------------------------------ cleaning (a source is not trusted to be tidy)
def _text(v: Any, n: int = 80) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _mac(v: Any) -> str:
    s = _text(v, 24).lower().replace("-", ":")
    return s if _MAC.match(s) else ""


def _ip(v: Any) -> str:
    try:
        return str(ipaddress.ip_address(_text(v, 45)))
    except ValueError:
        return ""


def _host(v: Any) -> str:
    h = _text(v, 80).lower().rstrip(".")
    return h[:-6] if h.endswith(".local") else h


def _flag(v: Any) -> bool | None:
    return v if isinstance(v, bool) else None


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v else None


def clean_device(raw: Any) -> dict | None:
    """One device from a source, in the shape the merge uses; None when it has no way to be told apart."""
    if not isinstance(raw, dict):
        return None
    ips = []
    for v in ([raw.get("ip")] + (raw.get("ips") if isinstance(raw.get("ips"), list) else [])):
        ip = _ip(v)
        if ip and ip not in ips:
            ips.append(ip)
    d = {"mac": _mac(raw.get("mac")), "ips": ips, "host": _host(raw.get("host")), "name": _text(raw.get("name")),
         "kind": raw.get("kind") if raw.get("kind") in KINDS else "", "ndi": _flag(raw.get("ndi")), "dante": _flag(raw.get("dante")),
         "uplink_mac": _mac(raw.get("uplink_mac")),
         "uplink_port": _int(raw.get("uplink_port")),
         "port_name": _text(raw.get("port_name"), 40), "speed": _num(raw.get("speed")), "poe_w": _num(raw.get("poe_w")),
         "model": _text(raw.get("model"), 60), "poe_budget_w": _num(raw.get("poe_budget_w")),
         "errors": _int(raw.get("errors")),
         "tags": [_text(t, 40) for t in raw.get("tags", []) if _text(t, 40)][:8] if isinstance(raw.get("tags"), list) else [],
         "identified_by": [_text(t, 40) for t in raw.get("identified_by", []) if _text(t, 40)][:6]
         if isinstance(raw.get("identified_by"), list) else [],
         "ports": [p for p in (_port(x) for x in raw.get("ports", [])[:64]) if p] if isinstance(raw.get("ports"), list) else []}
    return d if (d["mac"] or d["ips"] or d["host"]) else None


def _port(raw: Any) -> dict | None:
    if not isinstance(raw, dict) or _int(raw.get("port")) is None:
        return None
    return {"port": raw["port"], "name": _text(raw.get("name"), 40), "up": bool(raw.get("up")), "speed": _num(raw.get("speed")),
            "poe_w": _num(raw.get("poe_w")), "errors": _int(raw.get("errors")) or 0,
            "use": _num(raw.get("use")), "client": _text(raw.get("client"), 80), "client_mac": _mac(raw.get("client_mac")),
            "warn": _text(raw.get("warn"), 160), "uplink": bool(raw.get("uplink"))}


# ------------------------------------------------------------------ the merge
def merge(sources: list[dict]) -> list[dict]:
    """`sources` is [{"id", "label", "stale": age or None, "devices": [raw device, ...]}, ...] in priority order (the
    first one to give a name or kind wins). Returns the merged devices, each with the sources that saw it."""
    out: list[dict] = []
    by_mac: dict[str, dict] = {}
    by_ip: dict[str, dict] = {}
    by_host: dict[str, dict] = {}
    for src in sources:
        for raw in src["devices"]:
            d = clean_device(raw)
            if not d:
                continue
            dev = (by_mac.get(d["mac"]) if d["mac"] else None) or next((by_ip[i] for i in d["ips"] if i in by_ip), None) \
                or (by_host.get(d["host"]) if d["host"] else None)
            if dev is None:
                dev = {"id": "", "mac": "", "ips": [], "host": "", "name": "", "kind": "", "ndi": None, "dante": None,
                       "uplink_mac": "", "uplink_port": None, "port_name": "", "speed": None, "poe_w": None, "errors": None,
                       "model": "", "poe_budget_w": None,
                       "tags": [], "identified_by": [], "ports": [], "seen_by": [], "stale_by": [], "notes": [], "_says": {}}
                out.append(dev)
            _fold(dev, d, src)
            if dev["mac"]:
                by_mac[dev["mac"]] = dev
            for i in dev["ips"]:
                by_ip[i] = dev
            if dev["host"]:
                by_host[dev["host"]] = dev
    for dev in out:
        says = dev.pop("_says")
        for key, label in (("ndi", "NDI"), ("dante", "Dante")):
            yes = [s for s, v in says.get(key, {}).items() if v]
            no = [s for s, v in says.get(key, {}).items() if v is False]
            dev[key] = True if yes else (False if no else None)
            if yes and no:
                dev["notes"].append(f"{', '.join(yes)} say this is {label}; {', '.join(no)} say it is not.")
        dev["id"] = dev["mac"] or (dev["ips"][0] if dev["ips"] else dev["host"])
        if not dev["kind"]:
            dev["kind"] = "av" if (dev["ndi"] or dev["dante"]) else "client"
        dev["name"] = dev["name"] or dev["host"] or (dev["ips"][0] if dev["ips"] else dev["mac"])
    seen_ids: set[str] = set()
    for dev in out:                                   # two merged rows can end up with one id: keep them apart
        while dev["id"] in seen_ids:
            dev["id"] += "+"
        seen_ids.add(dev["id"])
    macs = {d["mac"]: d["id"] for d in out if d["mac"]}
    for dev in out:
        dev["parent"] = macs.get(dev["uplink_mac"], "") if dev["uplink_mac"] and macs.get(dev["uplink_mac"]) != dev["id"] else ""
    return out


def _fold(dev: dict, d: dict, src: dict) -> None:
    sid = src["id"]
    for key in ("mac", "host", "name", "kind", "uplink_mac", "port_name", "model"):
        if not dev[key] and d[key]:
            dev[key] = d[key]
    for key in ("uplink_port", "speed", "poe_w", "errors", "poe_budget_w"):
        if dev[key] is None and d[key] is not None:
            dev[key] = d[key]
    for ip in d["ips"]:
        if ip not in dev["ips"]:
            dev["ips"].append(ip)
    for key in ("tags", "identified_by"):
        for t in d[key]:
            if t not in dev[key]:
                dev[key].append(t)
    if d["ports"] and not dev["ports"]:
        dev["ports"] = d["ports"]
    for key in ("ndi", "dante"):
        if d[key] is True:
            dev["_says"].setdefault(key, {})[src["label"]] = True
        elif d[key] is False:
            dev["_says"].setdefault(key, {}).setdefault(src["label"], False)
    if sid not in dev["seen_by"]:
        dev["seen_by"].append(sid)
    if src.get("stale") is not None and sid not in dev["stale_by"]:
        dev["stale_by"].append(sid)


def assign_ports(devices: list[dict], relay_ports: list[dict]) -> None:
    """For a device no source placed behind a switch, the Relay port whose subnet holds its address."""
    nets = []
    for p in relay_ports:
        for cidr in p.get("ipv4", []):
            try:
                nets.append((ipaddress.ip_interface(cidr).network, p["id"]))
            except ValueError:
                continue
    for dev in devices:
        dev["via"] = ""
        if dev["parent"] or dev["kind"] == "relay":
            continue
        for ip in dev["ips"]:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            hit = next((pid for net, pid in nets if a in net), "")
            if hit:
                dev["via"] = hit
                break


# ------------------------------------------------------------------ the sources
def relay_source(ports: list[netinfo.Port]) -> dict:
    ips = [a for p in ports if not p.virtual for a in p.addresses]
    return {"id": "relay", "label": "Relay", "devices": [{"name": "This Relay computer", "kind": "relay", "ips": ips,
                                                        "tags": ["Relay"], "identified_by": []}]}


def discovery_devices(found: list[mdns.Found]) -> list[dict]:
    out = []
    for f in found:
        ndi = f.service.startswith("_ndi.")
        dante = f.service.startswith("_netaudio")
        out.append({"ips": [i for i in f.ips if ":" not in i], "host": f.host, "name": f.label if ndi else "", "ndi": True if ndi else None,
                    "dante": True if dante else None, "identified_by": ["mDNS " + f.service.split(".")[0]]})
    return out


def _module_sources(registry: Any) -> list[dict]:
    """One entry per module that overrides network_map(): its devices while it is on, or nothing (and `on` False)."""
    from .modules import Module        # the base class only: core names no module

    out = []
    for mod in registry.all():
        if type(mod).network_map is Module.network_map:
            continue
        sid = mod.manifest.id
        on = registry.enabled(sid)
        entry = {"id": sid, "label": mod.manifest.name, "on": on, "adds": mod.network_map_adds, "kind": "module",
                 "devices": [], "error": ""}
        if on:
            try:
                entry["devices"] = mod.network_map() or []
            except Exception:                                  # noqa: BLE001 - one module must not take the page down
                log.warning("network map: %s failed", sid, exc_info=True)
                entry["error"] = "This source could not be read."
        out.append(entry)
    return out


async def snapshot(registry: Any, force: bool = False) -> dict:
    """The merged map, as the page reads it. Served again for FRESH_S unless `force`."""
    import asyncio

    now = time.time()
    if not force and _cache["result"] is not None and now - _cache["at"] < FRESH_S:
        return labelled(_cache["result"])
    ports = await asyncio.to_thread(netinfo.interfaces)
    relay_ports = [p.as_dict() for p in ports if not p.virtual and p.addresses]
    sources: list[dict] = _module_sources(registry)
    sources.append({"id": "relay", "label": "Relay", "on": True, "adds": "This computer and its network ports.",
                    "kind": "core", "error": "", "devices": relay_source(ports)["devices"]})
    disc = {"id": "discovery", "label": "Discovery (mDNS)", "on": True, "kind": "core", "error": "",
            "adds": "NDI and Dante devices that announce themselves on Relay's networks.", "devices": []}
    try:
        found = await asyncio.wait_for(asyncio.to_thread(mdns.scan, [a for p in ports if not p.virtual for a in p.addresses]), 12)
        disc["devices"] = discovery_devices(found)
    except Exception:                                          # noqa: BLE001
        log.warning("network map: discovery failed", exc_info=True)
        disc["error"] = "Discovery could not listen on the network."
    sources.append(disc)
    for s in sources:                                          # a source that failed keeps its last good devices, with their age
        if s["on"] and not s["error"]:
            _last[s["id"]] = (now, s["devices"])
            s["at"], s["stale"] = now, None
        elif s["on"] and s["id"] in _last:
            s["at"], s["devices"] = _last[s["id"]]
            s["stale"] = max(0, int(now - s["at"]))
        else:
            s["at"], s["stale"] = (None, None)
    live = [s for s in sources if s["on"]]
    devices = merge([{"id": s["id"], "label": s["label"], "stale": s["stale"], "devices": s["devices"]} for s in live])
    assign_ports(devices, relay_ports)
    try:
        await asyncio.to_thread(adopt_devices, devices)
    except db.Unavailable:
        log.warning("network map: the database is down, so gear was not recorded")
    result = {
        "generated_at": now, "devices": devices, "relay_ports": relay_ports,
        "sources": [{"id": s["id"], "label": s["label"], "kind": s["kind"], "on": s["on"], "adds": s["adds"], "error": s["error"],
                     "at": s["at"], "stale": s["stale"], "count": len(s["devices"])} for s in sources],
        "has_switch_source": any(d["kind"] in ("switch", "gateway", "ap") for d in devices),
    }
    _cache.update(at=now, result=result)
    return labelled(result)


# ------------------------------------------------------------------ what the operator adds: names, notes, racks
# A device is core gear (app/gear.py, #547): one stored record found again by its MAC (else its address or host name), so a
# name, a note and a rack survive a new IP address. A name changes only what Relay shows: nothing is sent to the device or to
# the module that found it. The Network page still speaks in map ids (the MAC when a device has one, else its address or host
# name), and a closet is a rack, inside a core place of the same name. What stays in config under KEY is the note on an empty
# switch port ("<switch id>#<port>"), which waits for core ports (#540); names, device notes and closets saved there before
# #547 are moved onto gear the first time they are read.
KEY = "network_map"
NAME_MAX, NOTE_MAX, PLACE_MAX = 60, 300, 40
MAX_LABELS, MAX_PLACES, MAX_GEAR = 2000, 30, 200
_KEY_RE = re.compile(r"^[0-9a-z:.\-]{1,90}\+*(#\d{1,4})?$")
DEFAULT_STORED: dict = {"names": {}, "notes": {}, "places": []}
POE_NEAR = 0.85              # a switch past this share of its PoE budget is listed under Needs a look


def _key(v: Any) -> str:
    return v if isinstance(v, str) and _KEY_RE.match(v) else ""


def clean_stored(raw: Any) -> dict:
    """The names, notes and closets saved in config (port notes now, the rest only before #547), tidied: unknown keys,
    empty text and repeats go."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {"names": {}, "notes": {}, "places": []}
    for field, cap in (("names", NAME_MAX), ("notes", NOTE_MAX)):
        items = raw.get(field) if isinstance(raw.get(field), dict) else {}
        for k, v in items.items():
            text = " ".join(_text(v, cap).split()) if field == "names" else _text(v, cap)
            if _key(k) and text and len(out[field]) < MAX_LABELS and (field == "notes" or "#" not in k):
                out[field][k] = text
    seen_names: set[str] = set()
    seen_gear: set[str] = set()
    for p in raw.get("places") if isinstance(raw.get("places"), list) else []:
        if not isinstance(p, dict):
            continue
        name = " ".join(_text(p.get("name"), PLACE_MAX).split())
        if not name or name.lower() in seen_names or len(out["places"]) >= MAX_PLACES:
            continue
        seen_names.add(name.lower())
        gear_ids = []
        for g in p.get("gear") if isinstance(p.get("gear"), list) else []:
            if _key(g) and "#" not in g and g not in seen_gear and len(seen_gear) < MAX_GEAR:
                seen_gear.add(g)
                gear_ids.append(g)
        out["places"].append({"name": name, "gear": gear_ids})
    return out


def device_keys(d: dict) -> list[str]:
    """The gear keys of a merged device: its MAC, else its addresses and host name; Dante and Relay's own computer too."""
    out = gear.net_keys(d.get("mac", ""), d.get("ips", []), d.get("host", ""))
    if d.get("kind") == "relay":
        out.append("relay:this")
    if d.get("dante") and d.get("host"):
        out.append("dante:" + d["host"])
    return out


def id_key(map_id: Any) -> str:
    """The gear key a map id stands for ("" for an id that is not one)."""
    text = _key(map_id).rstrip("+") if isinstance(map_id, str) else ""
    if not text or "#" in text:
        return ""
    if _MAC.match(text):
        return "mac:" + text
    return ("ip:" if _ip(text) else "host:") + text


def _map_id(keys: list[str]) -> str:
    """The id the Network page uses for gear: the MAC, else an address, else a host name."""
    for head in ("mac:", "ip:", "host:"):
        for k in keys:
            if k.startswith(head):
                return k[len(head):]
    return ""


def adopt_devices(devices: list[dict]) -> None:
    """Every device heard from just now is gear (made the first time, found again by its MAC or address)."""
    for d in devices:
        keys = device_keys(d)
        if not keys or d.get("stale_by"):
            continue
        kind = "dante" if d.get("dante") and d["kind"] in ("av", "client", "other") else d["kind"]
        try:
            gear.adopt(keys, name=d["name"], kind=kind, model=d.get("model", ""))
        except gear.Refused:
            log.warning("network map: too much gear to keep %s", d["name"])


def _gear_for(map_id: str) -> dict | None:
    """The gear a map id names, made when Relay has not seen it yet."""
    key = id_key(map_id)
    if not key:
        return None
    return gear.by_key(key) or gear.adopt([key], heard=False)


def _port_notes(raw: Any) -> dict:
    return {k: v for k, v in clean_stored(raw)["notes"].items() if "#" in k}


def _import_legacy() -> None:
    """Names, device notes and closets saved in config before #547 move onto gear, once: they are gone from config after."""
    from . import config

    old = clean_stored(config.get().get(KEY))
    if not (old["names"] or old["places"] or any("#" not in k for k in old["notes"])):
        return
    try:
        for k, v in old["names"].items():
            g = _gear_for(k)
            if g:
                gear.rename(g["id"], v)
        for k, v in old["notes"].items():
            g = _gear_for(k) if "#" not in k else None
            if g:
                gear.set_notes(g["id"], v)
        _write_racks(old["places"])
    except (gear.Refused, screens.Refused):
        log.warning("network map: could not move every saved name, note and closet onto gear", exc_info=True)
    config.save({KEY: {"names": {}, "notes": {k: v for k, v in old["notes"].items() if "#" in k}, "places": []}},
                replace=(KEY,))


def _write_racks(places: list[dict]) -> None:
    """A closet is a rack in a core place of the same name; gear goes in it top to bottom. A rack no longer listed goes,
    and its gear stays in the place."""
    keep: set[int] = set()
    for pos, p in enumerate(places):
        rack = gear.rack_by_name(p["name"]) or gear.rack_in_place(p["name"], p["name"])
        keep.add(rack["id"])
        gear.rack_set_position(rack["id"], pos)
        for i, gid_text in enumerate(p["gear"]):
            g = _gear_for(gid_text)
            if g:
                gear.set_rack(g["id"], rack["id"], i)
    for r in gear.racks():
        if r["id"] not in keep:
            gear.rack_delete(r["id"])


def stored() -> dict:
    """{"names": {map id: name}, "notes": {map id or "switch#port": text}, "places": [{"name", "gear": [map id]}]}: names an
    operator typed, notes, and the racks with the gear in each, top to bottom."""
    from . import config

    _import_legacy()
    names: dict[str, str] = {}
    notes: dict[str, str] = {}
    ids: dict[int, str] = {}
    for g in gear.listing():
        did = _map_id(g["keys"])
        if not did:
            continue
        ids[g["id"]] = did
        if g["named"]:
            names[did] = g["name"]
        if g["notes"]:
            notes[did] = g["notes"]
    notes.update(_port_notes(config.get().get(KEY)))
    places, seen = [], set()
    for r in gear.racks():
        if r["name"].lower() in seen:
            continue
        seen.add(r["name"].lower())
        places.append({"name": r["name"], "gear": [ids[g["id"]] for g in gear.in_rack(r["id"]) if g["id"] in ids]})
    return {"names": names, "notes": notes, "places": places}


def save_labels(names: Any = None, notes: Any = None) -> dict:
    """Set or clear (empty text) names and notes; the rest stay. Returns what is saved."""
    from . import config

    _import_legacy()
    for k, v in (names.items() if isinstance(names, dict) else []):
        g = _gear_for(k) if _key(k) and "#" not in k else None
        if g:
            gear.rename(g["id"], " ".join(v.split())[:NAME_MAX] if isinstance(v, str) else "")
    ports = _port_notes(config.get().get(KEY))
    port_changed = False
    for k, v in (notes.items() if isinstance(notes, dict) else []):
        if not _key(k):
            continue
        text = _text(v, NOTE_MAX)
        if "#" in k:
            port_changed = True
            if text:
                ports[k] = text
            else:
                ports.pop(k, None)
        else:
            g = _gear_for(k)
            if g:
                gear.set_notes(g["id"], text)
    if port_changed:
        config.save({KEY: {"names": {}, "notes": ports, "places": []}}, replace=(KEY,))
    return stored()


def save_places(places: Any) -> dict:
    """Replace the list of closets (racks) and the switches in each, in order."""
    _import_legacy()
    clean = clean_stored({"places": places})["places"]
    _write_racks(clean)
    return stored()


def issues(devices: list[dict], sources: list[dict]) -> list[dict]:
    """What is worth a look, worst first: [{"level": "err"|"warn"|"info", "title", "detail", "device", "port"}].
    Only from what the sources report; Relay itself never probes a device."""
    out: list[dict] = []
    by_mac = {d["mac"]: d for d in devices if d["mac"]}
    for g in devices:
        if g["kind"] not in ("gateway", "switch", "ap"):
            continue
        for p in g["ports"]:
            if p.get("warn"):
                who = by_mac.get(p.get("client_mac") or "")
                title = f"{who['name'] if who else (p.get('client') or 'Port ' + str(p['port']))}: {g['name']} port {p['port']}"
                out.append({"level": "warn", "title": title, "detail": p["warn"], "device": who["id"] if who else g["id"],
                            "port": p["port"], "switch": g["id"]})
        if g["poe_budget_w"] and g["poe_w"] and g["poe_w"] >= POE_NEAR * g["poe_budget_w"]:
            out.append({"level": "warn", "title": f"{g['name']} is near its PoE limit",
                        "detail": f"{g['poe_w']:g} of {g['poe_budget_w']:g} W in use. Another powered device on it may not start.",
                        "device": g["id"], "port": None, "switch": g["id"]})
    for d in devices:
        if (d["ndi"] or d["dante"]) and d["seen_by"] and set(d["seen_by"]) <= set(d["stale_by"]):
            out.append({"level": "err", "title": f"{d['name']} is not answering",
                        "detail": "Every source that saw it is showing an older read.", "device": d["id"], "port": None,
                        "switch": d["parent"] or ""})
        for n in d["notes"]:
            out.append({"level": "info", "title": d["name"], "detail": n, "device": d["id"], "port": None, "switch": d["parent"] or ""})
    for s in sources:
        if s["on"] and s["error"]:
            out.append({"level": "err", "title": f"{s['label']} is not answering", "detail": s["error"], "device": "",
                        "port": None, "switch": ""})
    rank = {"err": 0, "warn": 1, "info": 2}
    return sorted(out, key=lambda i: rank[i["level"]])


def labelled(result: dict) -> dict:
    """The snapshot with the operator's names applied (the device's own name kept in own_name), the notes and closets,
    and what needs a look. A copy: the cached snapshot is left as the sources gave it."""
    st = stored()
    by_key = {k: g for k, g in gear.by_keys().items() if g["named"]}
    devices = []
    for d in result["devices"]:
        d = dict(d)
        d["own_name"] = d["name"]
        hit = next((by_key[k] for k in device_keys(d) if k in by_key), None)
        if hit:
            d["name"] = hit["name"]
        devices.append(d)
    out = dict(result)
    out.update(devices=devices, names=st["names"], notes=st["notes"], places=st["places"],
               issues=issues(devices, result["sources"]))
    return out


def reset() -> None:
    """Forget the cache and every source's last read (tests)."""
    _cache.update(at=0.0, result=None)
    _last.clear()
