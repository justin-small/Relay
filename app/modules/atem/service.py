"""Runs one ATEM client per configured switcher and publishes their tally into core's shared state (#243, #386).

Settings are a list of switchers, each with its own address, an on/off switch, whether it drives the tally lights,
and the number its camera 1 becomes on the tally screens (so camera 1 on the overflow switcher can be tally 101).
A setting from before #386 (`{"host", "port"}`) becomes the first switcher, named "Switcher", on load.

Each switcher that drives tally publishes as its own tally source (`atem_<id>`), so core merges them the same way it
merges any two sources. A switcher found by discovery keeps its mDNS unique id: when it stops answering, Relay looks
for that id on the LAN and follows it to its new address (a DHCP lease that changed).
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time

from ... import config, tally
from . import protocol as p
from .client import AtemClient

log = logging.getLogger("relay.atem")

KEY = "atem"
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


def clean_port(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        return p.PORT
    return value


def clean_offset(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_OFFSET:
        return 0
    return value


def new_id() -> str:
    return secrets.token_hex(4)


def clean_device(raw, fallback_id: str = "") -> dict | None:
    """One switcher, or None when it has no usable id."""
    if not isinstance(raw, dict):
        return None
    did = raw.get("id") if isinstance(raw.get("id"), str) else fallback_id
    if not _ID_RE.fullmatch(did or ""):
        return None
    uid = raw.get("uid") if isinstance(raw.get("uid"), str) else ""
    return {"id": did, "name": _str(raw.get("name"), NAME_MAX) or "Switcher", "host": clean_host(raw.get("host")),
            "port": clean_port(raw.get("port")), "enabled": raw.get("enabled") is not False,
            "tally": raw.get("tally") is not False, "tally_offset": clean_offset(raw.get("tally_offset")),
            "uid": uid if _UID_RE.fullmatch(uid) else "", "model": _str(raw.get("model"), 60)}


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
    live = [d for d in items if d["enabled"] and d["tally"]]
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
    """One switcher's running connection."""

    def __init__(self, cfg: dict, on_change) -> None:
        self.cfg = cfg
        self.client: AtemClient | None = None
        self._on_change = on_change
        self.down_since = time.monotonic()
        self.last_refind = float("-inf")   # never looked yet (monotonic time can be small just after boot)
        self.refound = ""

    @property
    def source(self) -> str:
        return f"atem_{self.cfg['id']}"

    def open(self) -> None:
        if self.cfg["enabled"] and self.cfg["host"]:
            self.client = AtemClient(self.cfg["host"], self.cfg["port"], on_change=self._on_change)
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
            if not (dev.cfg["tally"] and dev.connected):
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
            self._watch = asyncio.get_running_loop().create_task(self._refind_loop(), name="atem-refind")

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
        """Every switcher again, from the settings as they are now."""
        async with self._lock:
            await self._close_all()
            self.publish()
            self._open_all()

    async def sync(self) -> None:
        """Bring the running switchers in line with the settings, reopening only the ones that changed."""
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
                log.exception("ATEM re-find failed")

    async def _refind_once(self, discover) -> bool:
        """Look for any lost switcher by its unique id, and learn the id of a connected one that has none.
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
        by_uid = {f["uid"]: f for f in found if f.get("uid") and f.get("atem")}
        by_addr = {f["address"]: f for f in found if f.get("uid") and f.get("atem")}
        items = devices()
        changed = False
        moved: dict[str, str] = {}
        for item in items:
            dev = self.devices.get(item["id"])
            if dev is None:
                continue
            if dev in lost:
                hit = by_uid.get(item["uid"])
                if hit and hit["address"] and hit["address"] != item["host"]:
                    log.info("ATEM %s moved from %s to %s", item["name"], item["host"], hit["address"])
                    moved[item["id"]] = f"Found again at {hit['address']} (it was at {item['host']})."
                    item["host"] = hit["address"]
                    changed = True
            elif dev in unknown:
                hit = by_addr.get(item["host"])
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
                   refound=dev.refound if dev else "", product="", version="", old_firmware=False,
                   inputs=[], mix_effects=[], macros=[], downstream_keyers=0)
        if c is None or not c.connected:
            return out
        st = c.state
        out.update(connected=True, product=st.product, downstream_keyers=st.downstream_keyers,
                   version=version_text(st.version), old_firmware=bool(st.version) and st.version < p.V8_0_1,
                   inputs=[{"id": i, "name": v["long"] or v["short"] or f"Input {i}"}
                           for i, v in sorted(st.inputs.items()) if i <= 40 or i in (1000, 2001, 2002)],
                   mix_effects=[{"me": m, "program": st.program.get(m), "preview": st.preview.get(m)}
                                for m in range(st.mix_effects)],
                   macros=[{"slot": k, "name": v} for k, v in sorted(st.macros.items())])
        return out

    # ------------------------------------------------------------------------------------------ control
    def device(self, did) -> Device | None:
        """The switcher with that id; with none given, the first one."""
        if did is None:
            return next(iter(self.devices.values()), None)
        return self.devices.get(did) if isinstance(did, str) else None

    @staticmethod
    def _send(dev: Device, *cmds: bytes) -> bool:
        return bool(dev.client and dev.client.send(*cmds))

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
        version = dev.client.state.version if dev.client else 0
        if action == "auto":
            return self._send(dev, p.downstream_auto(keyer, version))
        return self._send(dev, p.downstream_on_air(keyer, action == "on"))

    def macro(self, dev: Device, slot: int, action: str) -> bool:
        return self._send(dev, p.macro_run(slot) if action == "run" else p.macro_stop())

    @staticmethod
    def limits(dev: Device | None) -> dict:
        st = dev.client.state if dev and dev.client else None
        return {"mix_effects": st.mix_effects if st else 0, "keyers": st.downstream_keyers if st else 0}


def _connection(cfg: dict) -> tuple:
    return cfg["host"], cfg["port"], cfg["enabled"]


service = Service()
