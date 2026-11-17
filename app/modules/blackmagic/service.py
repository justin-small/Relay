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
import logging
import re
import secrets
import time

from ... import config, tally
from . import atem_protocol as p
from . import hyperdeck, videohub
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
           "model": _str(raw.get("model"), 60)}
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
    return {"devices": devices}


def devices() -> list[dict]:
    return clean_settings(config.get().get(KEY))["devices"]


def save_devices(items: list[dict]) -> None:
    config.save({KEY: {"devices": items}}, replace=(KEY,))


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
            cams = [dict(c, n=c["n"] + offset) for c in dev.client.cameras() if c["n"] + offset <= 999]
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
        return {"devices": [self.device_snapshot(d) for d in items], "warnings": overlaps(items),
                "configured": any(d["host"] for d in items),
                "connected": sum(1 for d in items if self._connected(d["id"]))}

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
            return out
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
        return out

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
