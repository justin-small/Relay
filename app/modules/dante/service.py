"""Dante discovery and reachability (#257): which Dante devices answer on Relay's Dante network, and whether Relay can
reach their control port.

Read-only. Discovery is core's mDNS listener (app/mdns.py); this module only folds its answers into one record per Dante
device and asks each device's control port (ARC) whether it answers. It never changes anything on a device. Control
(renaming, subscriptions) waits for a packet capture or real gear, because the ARC packets cannot be checked without them.

From the field survey on a real church network (issue #257):

* A device is told apart by its mDNS service types, never by an Audinate MAC prefix (Allen & Heath's stage boxes carry
  their own).
* A computer running Dante Virtual Soundcard or Dante Via announces from every network port it has, so the inventory is
  keyed by the Dante device name, never by an address.
* Ports come from the SRV records (Via's control port is 24440, not 4440), never from a constant.
* A transmit channel is the instance `<channel>@<device>` of `_netaudio-chan._udp`.
"""
from __future__ import annotations

import asyncio
import logging
import select
import struct
import threading
import time
from typing import Any

from ... import gear, gearfeed, mdns, netinfo, netroute, netsock

log = logging.getLogger("relay.dante")

CLASS_ID = "dante"
VIA = "_netaudio-via._tcp.local"
ARC = "_netaudio-arc._udp.local"
CHAN = "_netaudio-chan._udp.local"
SERVICES = mdns.SERVICES + (VIA,)
PORT_KEY = {"_netaudio-arc._udp.local": "arc", "_netaudio-cmc._udp.local": "cmc", "_netaudio-dbc._udp.local": "dbc",
            "_netaudio-chan._udp.local": "chan", VIA: "via"}
DEFAULT_ARC = 4440
MAX_DEVICES = 200
MAX_CHANNELS = 512
SCAN_EVERY = 30.0
PROBE_WAIT = 0.6


def _ipv4(ips: list[str]) -> list[str]:
    return [i for i in ips if ":" not in i]


def split_channel(label: str) -> tuple[str, str]:
    """`<channel>@<device>` of a `_netaudio-chan` instance: (channel, device). A channel name may hold an `@`, so the
    device is what follows the last one."""
    channel, _, device = label.rpartition("@")
    return (channel, device) if channel and device else ("", label)


def inventory(found: list[mdns.Found]) -> list[dict]:
    """One record per Dante device, keyed by its Dante name (case does not matter), from what mDNS announced.

    {"name", "key", "host", "ips", "ports": {"arc": 4440, ...}, "model", "manufacturer", "router_info", "arc_version",
     "via", "transmit": [channel name, ...]}"""
    out: dict[str, dict] = {}
    for f in found:
        if f.service not in PORT_KEY:
            continue
        channel, name = split_channel(f.label) if f.service == CHAN else ("", f.label)
        key = name.strip().lower()
        if not key:
            continue
        if key not in out and len(out) >= MAX_DEVICES:
            continue
        d = out.setdefault(key, {"name": name.strip(), "key": key, "host": "", "ips": [], "ports": {}, "model": "",
                                 "manufacturer": "", "router_info": "", "arc_version": "", "via": False,
                                 "transmit": []})
        if f.host and not d["host"]:
            d["host"] = f.host
        for ip in _ipv4(f.ips):
            if ip not in d["ips"]:
                d["ips"].append(ip)
        if f.port:
            d["ports"].setdefault(PORT_KEY[f.service], f.port)
        if f.service == VIA:
            d["via"] = True
        if f.service == ARC:          # the ARC announcement carries what the device says about itself
            d["model"] = d["model"] or f.txt.get("model", "")
            d["manufacturer"] = d["manufacturer"] or f.txt.get("mf", "")
            d["router_info"] = d["router_info"] or f.txt.get("router_info", "")
            d["arc_version"] = d["arc_version"] or f.txt.get("arcp_vers", "")
        if channel and channel not in d["transmit"] and len(d["transmit"]) < MAX_CHANNELS:
            d["transmit"].append(channel)
    for d in out.values():
        d["via"] = d["via"] or d["router_info"].lower() == "dante via" or d["model"].lower().startswith("via")
        d["transmit"].sort(key=str.lower)
    return sorted(out.values(), key=lambda d: d["name"].lower())


def probe_packet(sequence: int) -> bytes:
    """An ARC request for the device's channel counts: the 10-byte header alone (protocol 0x27ff, length, sequence,
    command 0x1000, no result). NOT checked against real gear yet: a device that does not answer it may still be
    reachable, which the page says."""
    return struct.pack(">HHHHH", 0x27FF, 10, sequence & 0xFFFF, 0x1000, 0)


def probe(ip: str, port: int, sequence: int = 1, wait: float = PROBE_WAIT) -> bool:
    """True when something at `ip:port` sent a datagram back. Blocks up to `wait` seconds: call through to_thread.
    Raises netsock.RouteError when the Dante network is not there."""
    sock = netsock.udp_socket(CLASS_ID)
    try:
        sock.sendto(probe_packet(sequence), (ip, port))
        end = time.monotonic() + wait
        while time.monotonic() < end:
            ready, _, _ = select.select([sock], [], [], max(0.0, end - time.monotonic()))
            if not ready:
                break
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                continue
            if addr[0] == ip and len(data) >= 2:
                return True
        return False
    finally:
        sock.close()


def scan_addresses(resolved: Any) -> list[str]:
    """The local addresses mDNS questions leave from: the Dante port when one is chosen, else every real port."""
    if resolved.routed and resolved.address:
        return [resolved.address]
    return [a for p in netinfo.interfaces() if not p.virtual for a in p.addresses]


class Service:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.devices: list[dict] = []
        self.error = ""
        self.scanned = 0.0
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None

    # -------------------------------------------------------------------------------------------- scanning
    def scan_once(self) -> None:
        """Listen, fold, probe each device's control port, and keep the answer. Blocks: call through to_thread."""
        r = netroute.resolve(CLASS_ID)
        if not r.available:
            with self._lock:
                self.error = r.reason or "The Dante network port is not available."
            return
        try:
            found = mdns.scan(scan_addresses(r), services=SERVICES)
        except Exception:  # noqa: BLE001
            log.warning("dante: discovery failed", exc_info=True)
            with self._lock:
                self.error = "Discovery could not listen on the network."
            return
        devices = inventory(found)
        for seq, d in enumerate(devices, 1):
            d["reachable"] = None
            ip, port = (d["ips"][0] if d["ips"] else ""), d["ports"].get("arc", DEFAULT_ARC)
            d["arc_port"] = port
            if not ip:
                continue
            try:
                d["reachable"] = probe(ip, port, seq)
            except OSError:
                d["reachable"] = None
        self.record(devices)

    def record(self, devices: list[dict]) -> None:
        """Keep what a scan found and tell core its gear changed."""
        with self._lock:
            self.devices, self.error, self.scanned = devices, "", time.time()
        gearfeed.changed("dante")

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.to_thread(self.scan_once)
            except Exception:  # noqa: BLE001
                log.warning("dante: scan failed", exc_info=True)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), SCAN_EVERY)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        self._wake = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop(), name="dante-scan")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def rescan(self) -> None:
        """Scan again now. With the loop running it wakes the loop; without it, scans here."""
        if self._task and self._wake:
            self._wake.set()
        else:
            await asyncio.to_thread(self.scan_once)

    async def restart(self) -> None:
        await self.stop()
        self.start()

    # -------------------------------------------------------------------------------------------- what is shown
    def snapshot(self) -> dict:
        with self._lock:
            devices = [dict(d) for d in self.devices]
            return {"devices": devices, "error": self.error, "scanned": self.scanned, "running": self._task is not None,
                    "reachable": sum(1 for d in devices if d.get("reachable")),
                    "unreachable": sum(1 for d in devices if d.get("reachable") is False)}

    def gear_list(self) -> dict | None:
        """Boxes for the network map and each device's transmit channels as core channels (app/gearfeed.py). None until
        the first scan has run, so nothing turns missing before Relay has looked."""
        with self._lock:
            if not self.scanned:
                return None
            devices = [dict(d) for d in self.devices]
        out_devices, groups = [], []
        for d in devices:
            out_devices.append({"ips": d["ips"], "host": d["host"], "name": d["name"], "kind": "av", "dante": True,
                                "tags": ["Dante"] + (["Dante Via"] if d["via"] else [])})
            g = gear.by_key("dante:" + d["host"]) if d["host"] else None
            groups.append({"gear": g["id"] if g else None, "within": d["key"] + ":",
                           "items": [{"key": f"{d['key']}:tx:{c}", "kind": "audio", "direction": "source",
                                      "gear_name": c} for c in d["transmit"]]})
        return {"devices": out_devices, "channels": groups}


service = Service()

