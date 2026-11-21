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
from . import arc, heartbeat, inputs
from .monitor import monitor

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
METER_EVERY = 1.0
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
    command 0x1000, no result). Checked on 2026-11-20 against real DT168 stage boxes, a Mac running Dante Virtual
    Soundcard and Dante Via (port 24440), from the Dante network and from the main LAN across the router."""
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
        self.levels: dict[str, dict] = {}
        self.scanned = 0.0
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._meter: asyncio.Task | None = None
        self.heartbeats = heartbeat.Heartbeats()

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
            d["clock"], d["subscriptions"], d["info"] = None, None, {}
            d["heartbeat"] = self.heartbeats.seen(d["ips"])
            d["online"] = bool(d["reachable"] or d["heartbeat"])
            if d["reachable"]:
                self.read_state(d, ip, port)
        self.record(devices)
        monitor.observe(devices)

    @staticmethod
    def read_state(d: dict, ip: str, port: int) -> None:
        """The clock and the subscriptions of one device that answered. A device that answers in a way Relay cannot
        read keeps None, which is "no reading", never a made-up one."""
        try:
            d["info"] = arc.read_info(ip, port)
            d["clock"] = arc.parse_clock(arc.ask(ip, port, arc.CMD_CLOCK, 2))
            d["subscriptions"] = arc.read_subscriptions(ip, port)
        except OSError:
            pass

    # -------------------------------------------------------------------------------------------- levels
    def meter_targets(self) -> dict[str, set[int]]:
        """Per device key, the channel numbers worth metering: Relay's own computer's channels and the starred ones."""
        local = {a for p in netinfo.interfaces() for a in p.addresses}
        starred = monitor.starred()
        with self._lock:
            devices = [dict(d) for d in self.devices]
        out: dict[str, set[int]] = {}
        for d in devices:
            names = d["transmit"]
            own = bool(local.intersection(d["ips"]))
            want = {i for i, c in enumerate(names) if own or (d["name"], c) in starred}
            if want and d["ips"]:
                out[d["key"]] = want
        return out

    def read_levels(self) -> None:
        """One meter reading of every device with a channel worth watching. Blocks: call through to_thread."""
        targets = self.meter_targets()
        if not targets:
            with self._lock:
                self.levels = {}
            return
        with self._lock:
            devices = {d["key"]: dict(d) for d in self.devices}
        out: dict[str, dict] = {}
        for key, want in targets.items():
            d = devices.get(key)
            if not d:
                continue
            got = self.heartbeats.levels(d["ips"])
            if got is None:
                continue
            names = d["transmit"]
            named = len(got) == len(names)   # a count that disagrees means the order cannot be trusted
            out[key] = {"device": d["name"], "channels": [
                {"channel": names[i] if named else f"Channel {i + 1}", "level": got[i]} for i in sorted(want)
                if i < len(got) and got[i] is not None]}
        with self._lock:
            self.levels = out

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

    async def _meter_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.read_levels)
            except Exception:  # noqa: BLE001
                log.warning("dante: metering failed", exc_info=True)
            await asyncio.sleep(METER_EVERY)

    def start(self) -> None:
        self._wake = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._loop(), name="dante-scan")
        self._meter = loop.create_task(self._meter_loop(), name="dante-meter")
        try:
            self.heartbeats.listen(netsock.multicast_socket(CLASS_ID, heartbeat.GROUP, heartbeat.PORT))
        except (OSError, ValueError) as e:
            log.warning("dante: cannot listen for heartbeats, so no levels: %s", e)

    async def stop(self) -> None:
        self.heartbeats.stop()
        tasks, self._task, self._meter = [self._task, self._meter], None, None
        for task in tasks:
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
                    "online": sum(1 for d in devices if d.get("online")),
                    "unreachable": sum(1 for d in devices if d.get("reachable") is False),
                    "problems": monitor.current(), "levels": list(self.levels.values()),
                    "leader": next((d["name"] for d in devices if (d.get("clock") or {}).get("leader")), "")}

    def input_labels(self, devices: list[dict]) -> dict[int, dict]:
        """Which input devices are Dante (Dante Virtual Soundcard, Via, AVIO), and what feeds this computer's Dante inputs (see inputs.py)."""
        with self._lock:
            known = [dict(d) for d in self.devices]
        return inputs.labels(devices, known)

    def devices_copy(self) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self.devices]

    def keep_subscriptions(self, key: str, subs: list[dict] | None) -> None:
        """Routing wrote and read back a device: keep what it now shows, so the page does not wait for the next scan."""
        with self._lock:
            for d in self.devices:
                if d["key"] == key:
                    d["subscriptions"] = subs

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

