"""Runs one client per configured Blackmagic device and publishes the switchers' tally into core's shared state
(#243, #386, #432, #433).

Settings are a list of devices of three kinds: an ATEM switcher, a HyperDeck recorder or a Videohub router. Each has
its own address, an on/off switch and a kind; a switcher also says whether it drives the tally lights and the number its
camera 1 becomes on the tally screens (so camera 1 on the overflow switcher can be tally 101), and a Videohub keeps its
saved route presets. A device saved before #432 has no kind and is a switcher. A setting from before #386
(`{"host", "port"}`) becomes the first switcher, named "Switcher", on load.

Each switcher that drives tally publishes as its own tally source (`atem_<id>`), so core merges them the same way it
merges any two sources. A device found by discovery keeps its mDNS unique id: when it stops answering, Relay looks
for that id on the LAN and follows it to its new address (a DHCP lease that changed).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import time

from ... import cables as core_cables, channels, config, db, gear, ports, screens, tally
from ...text import same_name
from . import atem_protocol as p
from . import drawings, hyperdeck, videohub
from .atem_client import AtemClient
from .hyperdeck import HyperDeckClient
from .videohub import VideohubClient

log = logging.getLogger("relay.blackmagic")

KEY = "blackmagic"
KINDS = ("atem", "hyperdeck", "videohub")
KIND_NAMES = {"atem": "ATEM switcher", "hyperdeck": "HyperDeck", "videohub": "Videohub"}
DEFAULT_NAME = {"atem": "Switcher", "hyperdeck": "HyperDeck", "videohub": "Videohub"}
DEFAULT_PORT = {"atem": p.PORT, "hyperdeck": hyperdeck.PORT, "videohub": videohub.PORT}
MAX_PRESETS = 20
MAX_DEVICES = 16
NAME_MAX = 40
MAX_OFFSET = 900
REFIND_AFTER_S = 20.0     # not connected this long: look for it by its unique id
REFIND_EVERY_S = 60.0     # and not more often than this per switcher
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[a-z0-9]{1,16}")
_UID_RE = re.compile(r"[A-Za-z0-9:._\-]{1,64}")
# The rack redesign: which drawing a device gets (empty: picked from its product name, drawings.py), where it sits, and the
# cables between connectors that Relay cannot see. A connector is a core port (app/ports.py, #540) on the device's gear,
# keyed by its drawing id ("in1"), and its note is the port's note; notes saved in config before #540 move there. A cable is
# a core cable (app/cables.py, #541) between two ports, whoever recorded it; cables saved in config before #541 move there.
DRAWINGS = drawings.DRAWINGS
PLACES = ("rack", "shelf")
PORT_RE = re.compile(r"[a-z0-9]{1,12}")
NOTE_MAX = ports.NOTE_MAX
MAX_NOTES = 96
MAX_CABLES = 200


def _str(value, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def clean_host(value) -> str:
    host = value.strip() if isinstance(value, str) else ""
    return host if _HOST_RE.fullmatch(host) else ""


def clean_kind(value) -> str:
    return value if value in KINDS else "atem"


def clean_port(value, kind: str = "atem") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        return DEFAULT_PORT[clean_kind(kind)]
    return value


def clean_offset(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_OFFSET:
        return 0
    return value


def new_id() -> str:
    return secrets.token_hex(4)


def clean_presets(raw) -> list[dict]:
    """A Videohub's saved route presets: a name and the input each listed output takes (outputs and inputs from 0)."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _ID_RE.fullmatch(item["id"]):
            continue
        name = videohub.clean_label(item.get("name"))
        routes = item.get("routes") if isinstance(item.get("routes"), dict) else {}
        clean: dict[str, int] = {}
        for o, i in routes.items():
            if (isinstance(o, str) and o.isdigit() and 0 <= int(o) < videohub.MAX_PORTS and isinstance(i, int)
                    and not isinstance(i, bool) and 0 <= i < videohub.MAX_PORTS):
                clean[str(int(o))] = i
        if name and clean and item["id"] not in seen and len(out) < MAX_PRESETS:
            seen.add(item["id"])
            out.append({"id": item["id"], "name": name, "routes": clean})
    return out


def clean_notes(raw) -> dict[str, str]:
    """The operator's note on each connector of one device, by the drawing's connector id (in config only before #540)."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            text = _str(v, NOTE_MAX)
            if isinstance(k, str) and PORT_RE.fullmatch(k) and text and len(out) < MAX_NOTES:
                out[k] = text
    return out


def _end(raw) -> list[str] | None:
    if (isinstance(raw, list) and len(raw) == 2 and all(isinstance(x, str) for x in raw)
            and _ID_RE.fullmatch(raw[0]) and PORT_RE.fullmatch(raw[1])):
        return [raw[0], raw[1]]
    return None


def clean_cables(raw, ids: set[str]) -> list[dict]:
    """Cables between two connectors, each end [device id, connector id]. A cable to a device no longer in the list
    goes, and so does a second cable on the same pair of connectors."""
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _ID_RE.fullmatch(item["id"]):
            continue
        a, b = _end(item.get("a")), _end(item.get("b"))
        if not a or not b or a == b or a[0] not in ids or b[0] not in ids:
            continue
        pair = tuple(sorted((tuple(a), tuple(b))))
        if pair in seen or len(out) >= MAX_CABLES:
            continue
        seen.add(pair)
        out.append({"id": item["id"], "a": a, "b": b})
    return out


def clean_device(raw, fallback_id: str = "") -> dict | None:
    """One device, or None when it has no usable id."""
    if not isinstance(raw, dict):
        return None
    did = raw.get("id") if isinstance(raw.get("id"), str) else fallback_id
    if not _ID_RE.fullmatch(did or ""):
        return None
    kind = clean_kind(raw.get("kind"))
    uid = raw.get("uid") if isinstance(raw.get("uid"), str) else ""
    out = {"id": did, "kind": kind, "name": _str(raw.get("name"), NAME_MAX) or DEFAULT_NAME[kind],
           "host": clean_host(raw.get("host")), "port": clean_port(raw.get("port"), kind),
           "enabled": raw.get("enabled") is not False, "uid": uid if _UID_RE.fullmatch(uid) else "",
           "model": _str(raw.get("model"), 60),
           "drawing": raw.get("drawing") if raw.get("drawing") in DRAWINGS else "",
           "place": raw.get("place") if raw.get("place") in PLACES else "",
           "notes": clean_notes(raw.get("notes"))}
    if kind == "atem":
        out["tally"] = raw.get("tally") is not False
        out["tally_offset"] = clean_offset(raw.get("tally_offset"))
    elif kind == "videohub":
        out["presets"] = clean_presets(raw.get("presets"))
    return out


def clean_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    devices: list[dict] = []
    if isinstance(raw.get("devices"), list):
        seen: set[str] = set()
        for item in raw["devices"]:
            dev = clean_device(item)
            if dev is not None and dev["id"] not in seen and len(devices) < MAX_DEVICES:
                seen.add(dev["id"])
                devices.append(dev)
    elif clean_host(raw.get("host")):
        # Before #386: one switcher, {"host", "port"}. It becomes the first in the list.
        devices.append(clean_device({"host": raw.get("host"), "port": raw.get("port"), "name": "Switcher"}, "main"))
    return {"devices": devices, "cables": clean_cables(raw.get("cables"), {d["id"] for d in devices})}


# What Relay shows for a device (its name, model, drawing and where it sits) is core gear (app/gear.py, #547) under the key
# "blackmagic:<id>"; the module keeps what it needs to reach the device and what it adds (its address, kind, switch,
# tally, presets, connector notes). A device saved before #547 has its name, model, drawing and place in config: they move
# onto gear the first time the list is read. `place` is "rack" for gear in a rack, "shelf" for gear in a room outside any
# rack and "" for neither. Where the order of the devices is a rack's top-to-bottom order, it is the gear's position.
GEAR_KEY = "blackmagic:"
IDENTITY = ("name", "model", "drawing", "place")
ROOM, DEFAULT_RACK = "Equipment room", "Rack 1"


def _net_keys(dev: dict) -> list[str]:
    host = clean_host(dev.get("host")).lower().rstrip(".")
    if not host:
        return []
    try:
        return ["ip:" + str(ipaddress.ip_address(host))]
    except ValueError:
        return ["host:" + (host[:-6] if host.endswith(".local") else host)]


def drawn_place(g: dict) -> str:
    return "rack" if g["rack_id"] else ("shelf" if g["place_id"] else "")


def _set_place(gid: int, place: str) -> None:
    if place == "rack":
        if not gear.get(gid)["rack_id"]:
            rack = gear.rack_in_place(DEFAULT_RACK, ROOM)
            gear.set_rack(gid, rack["id"], gear.next_position(rack["id"]))
    else:
        gear.set_rack(gid, None)
        gear.set_place(gid, screens.place_add(ROOM)["id"] if place == "shelf" else None)


def _write_gear(dev: dict, position: int | None = None) -> dict:
    """The device's gear, made if it is new, with what the module knows about it written in."""
    fresh = gear.by_key(GEAR_KEY + dev["id"]) is None
    keys = [GEAR_KEY + dev["id"]]
    for net in _net_keys(dev):                 # an address another Blackmagic device holds (one box, two services) stays apart
        held = gear.by_key(net)
        if held is None or not any(k.startswith(GEAR_KEY) and k != keys[0] for k in gear.keys(held["id"])):
            keys.append(net)
    g = gear.adopt(keys, name=dev["name"], kind=dev["kind"], heard=False)
    if fresh and position is None and not g["rack_id"] and g["position"] == 0:
        position = gear.next_position(None)    # a new device goes at the end of the list
    if not g["named"] or not same_name(g["name"], dev["name"]):
        gear.rename(g["id"], dev["name"])
    gear.set_details(g["id"], kind=dev["kind"], model=dev["model"] or None, drawing=dev["drawing"])
    if dev["place"] != drawn_place(g):
        _set_place(g["id"], dev["place"])
    if position is not None:
        gear.set_position(g["id"], position)
    return gear.get(g["id"])  # type: ignore[return-value]


def _overlay(dev: dict, g: dict) -> dict:
    return {**dev, "name": g["name"], "model": g["model"], "drawing": g["drawing"], "place": drawn_place(g),
            "position": g["position"], "notes": ports.notes(g["id"]), "gear_id": g["id"]}


def _stored_form(dev: dict) -> dict:
    return {k: v for k, v in dev.items() if k not in IDENTITY and k not in ("position", "notes", "gear_id")}


def _move_notes(gid: int, notes: dict[str, str]) -> None:
    """Connector notes saved in config before #540 become the notes of the device's ports."""
    for key, text in notes.items():
        row = ports.by_key(gid, key) or ports.adopt(gid, key, heard=False)
        if not row["note"]:
            ports.set_note(row["id"], text)


def set_note(dev: dict, key: str, text: str) -> None:
    """The note on one connector of a device (empty text clears it)."""
    g = gear.by_key(GEAR_KEY + dev["id"]) or _write_gear(dev)
    row = ports.by_key(g["id"], key) or ports.adopt(g["id"], key, heard=False)
    ports.set_note(row["id"], text)


def clean_config(raw) -> dict:
    """`clean_settings` for the config file: a device that has no name, model, drawing or place saved there stays without."""
    out = clean_settings(raw)
    given = {d.get("id"): d for d in (raw.get("devices") if isinstance(raw, dict) and isinstance(raw.get("devices"), list) else [])
             if isinstance(d, dict)}
    for dev in out["devices"]:
        for k in IDENTITY:
            if k not in given.get(dev["id"], {}):
                dev.pop(k, None)
        if not dev["notes"]:
            dev.pop("notes")                   # connector notes are ports now (#540); config keeps one only before then
    if not out["cables"]:
        out.pop("cables")                  # cables are core cables now (#541); config keeps them only until they move
    return out


def devices() -> list[dict]:
    """The devices in rack order, with their gear's name, model, drawing and place."""
    raw = config.get().get(KEY)
    items = clean_settings(raw)["devices"]
    legacy = any(isinstance(d, dict) and (any(k in d for k in IDENTITY) or d.get("notes"))
                 for d in (raw.get("devices") if isinstance(raw, dict) and isinstance(raw.get("devices"), list) else []))
    old_cables = isinstance(raw, dict) and bool(raw.get("cables"))
    try:
        known = gear.by_keys()
        out = []
        for dev in items:
            g = known.get(GEAR_KEY + dev["id"])
            g = g if g is not None else _write_gear(dev)
            if dev["notes"]:
                _move_notes(g["id"], dev["notes"])
            out.append(_overlay(dev, g))
        if legacy:
            save_devices(out)                  # the identity is on gear now: take it out of config
        if old_cables:
            _move_cables(out)
    except db.Unavailable:
        return items
    return [d for _, d in sorted(enumerate(out), key=lambda t: (t[1]["position"], t[0]))]


def _move_cables(items: list[dict]) -> None:
    """Cables saved in config before #541 become core cables between the two connectors' ports, once: they are gone from
    config after. A cable to a device that is not in the list goes, as it did before."""
    legacy = clean_settings(config.get().get(KEY))["cables"]
    if not legacy:
        return
    gears = {d["id"]: d.get("gear_id") for d in items}
    for c in legacy:
        ends = []
        for did, key in (c["a"], c["b"]):
            if not gears.get(did):
                break
            row = ports.by_key(gears[did], key) or ports.adopt(gears[did], key, heard=False)
            ends.append(row["id"])
        else:
            try:
                core_cables.add(ends[0], ends[1])
            except core_cables.Refused:
                pass                           # the same pair is already cabled
    save_devices(items)


def cables(items: list[dict] | None = None) -> list[dict]:
    """The core cables with both ends on Blackmagic devices, as the page draws them: each end is [device id, connector id]."""
    items = devices() if items is None else items
    by_gear = {d["gear_id"]: d["id"] for d in items if d.get("gear_id")}
    try:
        rows = core_cables.of_gears(by_gear)
    except db.Unavailable:
        return []
    return [{"id": c["id"], "ref": c["ref"], "a": [by_gear[c["a"]["gear"]], c["a"]["key"]],
             "b": [by_gear[c["b"]["gear"]], c["b"]["key"]], "by": c["by"], "reported": c["reported"], "note": c["note"]}
            for c in rows if c["a"]["gear"] in by_gear and c["b"]["gear"] in by_gear]


def connector(items: list[dict], end) -> dict | None:
    """The core port for [device id, connector id], made when Relay has not listed it yet; None when the device is not there."""
    dev = next((d for d in items if d["id"] == end[0]), None)
    if dev is None or not dev.get("gear_id"):
        return None
    return ports.by_key(dev["gear_id"], end[1]) or ports.adopt(dev["gear_id"], end[1], heard=False)


def save_devices(items: list[dict], reorder: bool = False) -> None:
    """Save the devices. A device's name, model, drawing and place go to its gear, and with `reorder` so does its place in
    the list (the position the rack is drawn in). Its cables go with it: they are core cables on its ports."""
    kept = [d["id"] for d in items]
    stored_items = list(items)
    try:
        before = {d["id"] for d in clean_settings(config.get().get(KEY))["devices"]}
        for i, dev in enumerate(items):
            _write_gear(dev, i if reorder else None)
        stored_items = [_stored_form(d) for d in items]
        for gone in before - set(kept):
            gear.forget_key(GEAR_KEY + gone)
    except db.Unavailable:
        stored_items = list(items)             # without the database the identity stays in config
    config.save({KEY: {"devices": stored_items}}, replace=(KEY,))


def overlaps(items: list[dict]) -> list[str]:
    """Sentences for switchers whose tally numbers collide (both drive tally and their 1..40 ranges overlap)."""
    out = []
    live = [d for d in items if d.get("kind", "atem") == "atem" and d["enabled"] and d["tally"]]
    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if abs(a["tally_offset"] - b["tally_offset"]) < p.MAX_CAMERA_INPUT:
                out.append(f"{a['name']} and {b['name']} both drive tally with overlapping camera numbers: camera "
                           f"{a['tally_offset'] + 1} means a camera on either one. Give one of them a different "
                           f"first camera number.")
    return out


def version_text(version: int) -> str:
    return f"{version >> 16}.{version & 0xFFFF}" if version else ""


class Device:
    """One device's running connection."""

    def __init__(self, cfg: dict, on_change) -> None:
        self.cfg = cfg
        self.client: AtemClient | HyperDeckClient | VideohubClient | None = None
        self._on_change = on_change
        self.down_since = time.monotonic()
        self.last_refind = float("-inf")   # never looked yet (monotonic time can be small just after boot)
        self.refound = ""

    @property
    def source(self) -> str:
        return f"atem_{self.cfg['id']}"

    def open(self) -> None:
        if self.cfg["enabled"] and self.cfg["host"]:
            make = {"atem": AtemClient, "hyperdeck": HyperDeckClient, "videohub": VideohubClient}[self.cfg["kind"]]
            self.client = make(self.cfg["host"], self.cfg["port"], on_change=self._on_change)
            self.client.start()
        self.down_since = time.monotonic()

    async def close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await client.close()

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.connected)


class Service:
    def __init__(self) -> None:
        self.devices: dict[str, Device] = {}
        self._published: set[str] = set()
        self._running = False
        self._watch: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._ported: dict[str, tuple] = {}      # device id -> the connectors last recorded as its ports
        self._chan_sig: dict[str, tuple] = {}    # device id -> the video channels last reported to core
        self._chan_refs: dict[str, dict[str, str]] = {}   # device id -> {channel key: core channel ref}

    # ------------------------------------------------------------------------------------------ tally
    def publish(self) -> None:
        live: set[str] = set()
        for dev in self.devices.values():
            if dev.connected:
                dev.down_since = 0.0
            elif not dev.down_since:
                dev.down_since = time.monotonic()
            if not (dev.cfg["kind"] == "atem" and dev.cfg["tally"] and dev.connected):
                continue
            offset = dev.cfg["tally_offset"]
            refs = self._chan_refs.get(dev.cfg["id"], {})
            cams = [dict(c, n=c["n"] + offset, **({"channel": refs[key]} if (key := f"{dev.cfg['id']}:in{c['n']}") in refs else {}))
                    for c in dev.client.cameras() if c["n"] + offset <= 999]
            tally.tally.publish(dev.source, cams)
            live.add(dev.source)
        for gone in self._published - live:
            tally.tally.clear(gone)
        self._published = live

    def clear_tally(self) -> None:
        for source in self._published:
            tally.tally.clear(source)
        self._published = set()

    # ------------------------------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._running = True
        self._open_all()
        if self._watch is None:
            self._watch = asyncio.get_running_loop().create_task(self._refind_loop(), name="blackmagic-refind")

    async def stop(self) -> None:
        self._running = False
        if self._watch is not None:
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)
            self._watch = None
        await self._close_all()
        self.clear_tally()

    def _open_all(self) -> None:
        if not self._running:
            return
        for cfg in devices():
            dev = Device(cfg, self.publish)
            self.devices[cfg["id"]] = dev
            dev.open()

    async def _close_all(self) -> None:
        devs, self.devices = list(self.devices.values()), {}
        await asyncio.gather(*(d.close() for d in devs), return_exceptions=True)

    async def restart(self) -> None:
        """Every device again, from the settings as they are now."""
        async with self._lock:
            await self._close_all()
            self.publish()
            self._open_all()

    async def sync(self) -> None:
        """Bring the running devices in line with the settings, reopening only the ones that changed."""
        async with self._lock:
            want = {d["id"]: d for d in devices()}
            for did in list(self.devices):
                dev = self.devices[did]
                cfg = want.get(did)
                if cfg is None or _connection(cfg) != _connection(dev.cfg):
                    await dev.close()
                    del self.devices[did]
                else:
                    dev.cfg = cfg
            if self._running:
                for did, cfg in want.items():
                    if did not in self.devices:
                        dev = Device(cfg, self.publish)
                        self.devices[did] = dev
                        dev.open()
            self.publish()

    # ------------------------------------------------------------------------------------------ following a move
    async def _refind_loop(self) -> None:
        from . import discovery

        while True:
            await asyncio.sleep(5.0)
            try:
                await self._refind_once(discovery.discover)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let a bad answer end the loop
                log.exception("Blackmagic re-find failed")

    async def _refind_once(self, discover) -> bool:
        """Look for any lost device by its unique id, and learn the id of a connected one that has none.
        True when a setting changed."""
        now = time.monotonic()
        lost = [d for d in self.devices.values() if d.cfg["enabled"] and d.cfg["uid"] and not d.connected
                and d.down_since and now - d.down_since >= REFIND_AFTER_S and now - d.last_refind >= REFIND_EVERY_S]
        unknown = [d for d in self.devices.values() if d.connected and not d.cfg["uid"]
                   and now - d.last_refind >= REFIND_EVERY_S]
        if not lost and not unknown:
            return False
        for d in lost + unknown:
            d.last_refind = now
        found = await asyncio.to_thread(discover)
        by_uid = {(f["family"], f["uid"].lower()): f for f in found if f.get("uid") and f.get("addable")}
        by_addr = {(f["family"], f["address"]): f for f in found if f.get("uid") and f.get("addable")}
        items = devices()
        changed = False
        moved: dict[str, str] = {}
        for item in items:
            dev = self.devices.get(item["id"])
            if dev is None:
                continue
            if dev in lost:
                hit = by_uid.get((item["kind"], item["uid"].lower()))
                if hit and hit["address"] and hit["address"] != item["host"]:
                    log.info("Blackmagic %s moved from %s to %s", item["name"], item["host"], hit["address"])
                    moved[item["id"]] = f"Found again at {hit['address']} (it was at {item['host']})."
                    item["host"] = hit["address"]
                    changed = True
            elif dev in unknown:
                hit = by_addr.get((item["kind"], item["host"]))
                if hit:
                    item["uid"] = hit["uid"]
                    item["model"] = item["model"] or hit.get("model", "")
                    changed = True
        if changed:
            save_devices(items)
            await self.sync()
            for did, text in moved.items():
                if did in self.devices:
                    self.devices[did].refound = text
        return changed

    # ------------------------------------------------------------------------------------------ what the page shows
    def snapshot(self) -> dict:
        items = devices()
        return {"devices": [self.device_snapshot(d) for d in items], "cables": cables(items), "warnings": overlaps(items),
                "configured": any(d["host"] for d in items), "drawings": dict(drawings.NAMES),
                "connected": sum(1 for d in items if self._connected(d["id"]))}

    def _faced(self, out: dict) -> dict:
        """The snapshot with the device's drawing (both faces, from what it reports) and its connectors recorded as core
        ports. Connectors are recorded only while the device is connected: an unreachable Videohub does not say how many
        inputs it has, and its ports must not all turn missing."""
        out["auto_drawing"] = drawings.auto(out)
        out["face"] = drawings.spec(out)
        if out["connected"] and out.get("gear_id"):
            found = drawings.connectors(out["face"])
            sig = (out["gear_id"], tuple((c["key"], c["kind"], c["direction"], c["gear_name"]) for c in found))
            if self._ported.get(out["id"]) != sig:
                try:
                    ports.sync(out["gear_id"], found)
                    self._ported[out["id"]] = sig
                except (db.Unavailable, ports.Refused):
                    log.warning("Blackmagic: could not record the connectors of %s", out["name"])
        self._report_channels(out)
        return out

    @staticmethod
    def _video_channels(out: dict) -> list[dict]:
        """The video channels a device has, as core channels (app/channels.py, #542): a switcher's inputs and a Videohub's
        inputs (sources) and outputs (destinations). The key is the device id and the device's own number, so a
        rename on the device keeps the channel."""
        if out["kind"] == "atem":
            return [{"key": f"{out['id']}:in{i['id']}", "kind": "video", "direction": "source", "number": i["id"],
                     "gear_name": i["name"]} for i in out.get("inputs", [])]
        hub = out.get("videohub") or {}
        return ([{"key": f"{out['id']}:vin{i['n']}", "kind": "video", "direction": "source", "number": i["n"] + 1,
                  "gear_name": i["label"]} for i in hub.get("inputs", [])]
                + [{"key": f"{out['id']}:vout{o['n']}", "kind": "video", "direction": "destination", "number": o["n"] + 1,
                    "gear_name": o["label"]} for o in hub.get("outputs", [])])

    def _report_channels(self, out: dict) -> None:
        """Tell core which video channels this device has, only while it is connected (an unreachable one does not say
        what it has, and its channels must not all turn missing), and only when they changed."""
        if not (out["connected"] and out.get("gear_id")) or out["kind"] == "hyperdeck":
            return
        found = self._video_channels(out)
        sig = (out["gear_id"], tuple((c["key"], c["number"], c["gear_name"]) for c in found))
        if self._chan_sig.get(out["id"]) == sig:
            return
        refs = channels.report("blackmagic", found, out["gear_id"])
        if refs is not None:
            self._chan_refs[out["id"]] = refs
            self._chan_sig[out["id"]] = sig

    def _connected(self, did: str) -> bool:
        dev = self.devices.get(did)
        return bool(dev and dev.connected)

    def device_snapshot(self, cfg: dict) -> dict:
        dev = self.devices.get(cfg["id"])
        c = dev.client if dev else None
        out = dict(cfg, configured=bool(cfg["host"]), connected=False, error=c.error if c else "",
                   refound=dev.refound if dev else "", product="")
        if cfg["kind"] == "atem":
            out.update(version="", old_firmware=False, inputs=[], mix_effects=[], macros=[], downstream_keyers=0)
        elif cfg["kind"] == "hyperdeck":
            out["hyperdeck"] = {}
        else:
            out["videohub"] = {}
        if c is None or not c.connected:
            return self._faced(out)
        out["connected"] = True
        if cfg["kind"] == "atem":
            st = c.state
            out.update(product=st.product, downstream_keyers=st.downstream_keyers,
                       version=version_text(st.version), old_firmware=bool(st.version) and st.version < p.V8_0_1,
                       inputs=[{"id": i, "name": v["long"] or v["short"] or f"Input {i}"}
                               for i, v in sorted(st.inputs.items()) if i <= 40 or i in (1000, 2001, 2002)],
                       mix_effects=[{"me": m, "program": st.program.get(m), "preview": st.preview.get(m)}
                                    for m in range(st.mix_effects)],
                       macros=[{"slot": k, "name": v} for k, v in sorted(st.macros.items())])
        elif cfg["kind"] == "hyperdeck":
            out.update(product=c.state.model, hyperdeck=self._hyperdeck_snapshot(c))
        else:
            out.update(product=c.state.model, videohub=self._videohub_snapshot(c))
        return self._faced(out)

    @staticmethod
    def _hyperdeck_snapshot(c: HyperDeckClient) -> dict:
        st = c.state
        clip = c.current_clip()
        return {"protocol": st.protocol, "status": st.status, "slot": st.slot, "timecode": st.timecode,
                "video_format": st.video_format, "loop": st.loop, "remote_enabled": st.remote_enabled,
                "clip": {"id": st.clip_id, "name": clip["name"] if clip else ""} if st.clip_id else None,
                "slots": [dict(v) for _, v in sorted(st.slots.items())], "clips": list(st.clips), "notice": st.notice}

    @staticmethod
    def _videohub_snapshot(c: VideohubClient) -> dict:
        st = c.state
        return {"version": st.version, "notice": st.notice, "present": st.present,
                "inputs": [{"n": n, "label": st.input_labels.get(n, "")} for n in range(st.inputs)],
                "outputs": [{"n": n, "label": st.output_labels.get(n, ""), "input": st.routing.get(n),
                             "lock": st.locks.get(n, "U")} for n in range(st.outputs)]}

    # ------------------------------------------------------------------------------------------ control
    def device(self, did) -> Device | None:
        """The device with that id; with none given, the first switcher (a page written for one switcher keeps working)."""
        if did is None:
            return next((d for d in self.devices.values() if d.cfg["kind"] == "atem"), None)
        return self.devices.get(did) if isinstance(did, str) else None

    @staticmethod
    def _send(dev: Device, *cmds: bytes) -> bool:
        return bool(dev.cfg["kind"] == "atem" and dev.client and dev.client.send(*cmds))

    def cut(self, dev: Device, me: int) -> bool:
        return self._send(dev, p.cut(me))

    def auto(self, dev: Device, me: int) -> bool:
        return self._send(dev, p.auto(me))

    def fade_to_black(self, dev: Device, me: int) -> bool:
        return self._send(dev, p.fade_to_black(me))

    def set_program(self, dev: Device, me: int, source: int) -> bool:
        return self._send(dev, p.set_program(me, source))

    def set_preview(self, dev: Device, me: int, source: int) -> bool:
        return self._send(dev, p.set_preview(me, source))

    def downstream(self, dev: Device, keyer: int, action: str) -> bool:
        version = dev.client.state.version if dev.client and dev.cfg["kind"] == "atem" else 0
        if action == "auto":
            return self._send(dev, p.downstream_auto(keyer, version))
        return self._send(dev, p.downstream_on_air(keyer, action == "on"))

    def macro(self, dev: Device, slot: int, action: str) -> bool:
        return self._send(dev, p.macro_run(slot) if action == "run" else p.macro_stop())

    # ---- HyperDeck
    @staticmethod
    def deck(dev: Device, action: str) -> bool:
        c = dev.client
        if dev.cfg["kind"] != "hyperdeck" or c is None or not c.connected:
            return False
        return {"record": c.record, "stop": c.stop, "play": c.play}[action]()

    # ---- Videohub
    @staticmethod
    def hub(dev: Device) -> VideohubClient | None:
        c = dev.client
        return c if dev.cfg["kind"] == "videohub" and c is not None and c.connected else None

    # ------------------------------------------------------------------------------------------ rename (#543)
    def _hub_end(self, key: str):
        """(hub client, "input" or "output", number) for a channel end key "<device id>:vin<n>" or ":vout<n>", else None."""
        did, _, tail = key.rpartition(":")
        m = re.fullmatch(r"v(in|out)(\d+)", tail)
        dev = self.devices.get(did)
        if not m or dev is None:
            return None
        hub = self.hub(dev)
        return None if hub is None else (hub, "input" if m.group(1) == "in" else "output", int(m.group(2)))

    def can_rename(self, key: str) -> bool:
        """Only a connected Videohub takes a new label; an ATEM's input names are not sent from here."""
        return self._hub_end(key) is not None

    def rename_end(self, key: str, name: str) -> None:
        """Send a new label to the Videohub. Raises ValueError with a sentence the operator can read."""
        got = self._hub_end(key)
        if got is None:
            raise ValueError("The Videohub is not connected, so its label was not changed.")
        hub, kind, n = got
        limit = hub.state.outputs if kind == "output" else hub.state.inputs
        label = videohub.clean_label(name)
        if not label or n >= limit:
            raise ValueError("The Videohub has no such port, or the name is empty.")
        if not hub.set_labels(kind, {n: label}):
            raise ValueError("The Videohub is not connected, so its label was not changed.")

    def route(self, dev: Device, pairs: dict[int, int]) -> tuple[int, int]:
        """Send routes the router would take; leave out any output another control surface holds. (sent, left out)"""
        c = self.hub(dev)
        if c is None:
            return 0, len(pairs)
        ok = {o: i for o, i in pairs.items() if 0 <= o < c.state.outputs and 0 <= i < c.state.inputs and c.can_route(o)}
        if ok and not c.route(ok):
            return 0, len(pairs)
        return len(ok), len(pairs) - len(ok)

    @staticmethod
    def limits(dev: Device | None) -> dict:
        st = dev.client.state if dev and dev.cfg["kind"] == "atem" and dev.client else None
        return {"mix_effects": st.mix_effects if st else 0, "keyers": st.downstream_keyers if st else 0}


def _connection(cfg: dict) -> tuple:
    return cfg["kind"], cfg["host"], cfg["port"], cfg["enabled"]


service = Service()
