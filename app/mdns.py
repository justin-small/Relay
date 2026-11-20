"""A small mDNS listener for the network map (#437): which devices on Relay's networks announce NDI or Dante.

It only asks "who offers this service?" on the multicast address and reads the answers; it calls no product's API
and sends nothing else, so it is core, like Relay's own port list (decided 2026-11-16; app/network_map.py).

Three things from the field survey on a real church network shape this file:

* Behind UniFi's mDNS proxy the answer comes from the gateway's own address and often holds the PTR record (sometimes
  with TXT) but no SRV or A record. So the reply's source address is never taken as the device's address: after the
  first round, every instance that lacks a host and port is asked for its SRV, and every host that lacks an address is
  asked for its A record.
* Without the proxy only the local VLAN is visible; with it many service types are. Either way is just what is heard.
* Queries leave from an ordinary (not 5353) port, so devices answer straight back to that socket and nothing here has
  to join the multicast group or fight another program for port 5353.

`parse` and `build_query` are plain functions over bytes, tested without a network (tests/test_network_map.py).
"""
from __future__ import annotations

import ipaddress
import socket
import struct
import time
from dataclasses import dataclass, field

GROUP = ("224.0.0.251", 5353)
TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV, TYPE_AAAA = 1, 12, 16, 33, 28
# What the map looks for: NDI sources and the four Dante announcements.
SERVICES = ("_ndi._tcp.local", "_netaudio-arc._udp.local", "_netaudio-cmc._udp.local", "_netaudio-dbc._udp.local",
            "_netaudio-chan._udp.local")
MAX_NAME = 253


@dataclass
class Record:
    name: str
    rtype: int
    ttl: int
    value: object       # PTR: the target name; SRV: (port, target); A/AAAA: the address; TXT: {key: value}


@dataclass
class Found:
    """One announced service instance, with whatever the replies told about its host."""
    service: str                       # "_ndi._tcp.local"
    instance: str                      # "STAGE-PC (OBS)._ndi._tcp.local"
    host: str = ""                     # "stage-pc.local"
    port: int | None = None
    ips: list[str] = field(default_factory=list)
    txt: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """The instance's own name without the service on the end: "STAGE-PC (OBS)"."""
        return self.instance[:-len(self.service) - 1] if self.instance.endswith("." + self.service) else self.instance


def _name(raw: bytes, pos: int) -> tuple[str, int]:
    """A DNS name at `pos` (labels, or a compression pointer back into the packet). Returns it and the position after
    it. Raises ValueError on anything malformed, and refuses pointer loops."""
    labels: list[str] = []
    end = -1
    jumps = 0
    while True:
        if pos >= len(raw):
            raise ValueError("name runs off the packet")
        n = raw[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if pos + 1 >= len(raw):
                raise ValueError("short pointer")
            if end < 0:
                end = pos + 2
            pos = ((n & 0x3F) << 8) | raw[pos + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError("pointer loop")
            continue
        if n & 0xC0:
            raise ValueError("bad label")
        if pos + 1 + n > len(raw):
            raise ValueError("label runs off the packet")
        labels.append(raw[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
        if sum(len(x) + 1 for x in labels) > MAX_NAME:
            raise ValueError("name too long")
    return ".".join(labels), (end if end >= 0 else pos)


def _txt(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i < len(data):
        n = data[i]
        chunk = data[i + 1:i + 1 + n]
        i += 1 + n
        if not chunk:
            continue
        key, _, val = chunk.decode("utf-8", "replace").partition("=")
        if key and len(out) < 24:
            out[key.lower()[:40]] = val[:120]
    return out


def parse(raw: bytes) -> list[Record]:
    """Every answer, authority and additional record in one mDNS packet. A packet that cannot be read gives []."""
    try:
        if len(raw) < 12:
            return []
        qd, an, ns, ar = struct.unpack(">4H", raw[4:12])
        pos = 12
        for _ in range(qd):
            _, pos = _name(raw, pos)
            pos += 4
        out: list[Record] = []
        for _ in range(min(an + ns + ar, 200)):
            name, pos = _name(raw, pos)
            if pos + 10 > len(raw):
                break
            rtype, _cls, ttl, rdlen = struct.unpack(">HHIH", raw[pos:pos + 10])
            pos += 10
            data = raw[pos:pos + rdlen]
            if len(data) < rdlen:
                break
            value: object = None
            if rtype == TYPE_PTR:
                value, _ = _name(raw, pos)
            elif rtype == TYPE_SRV and rdlen >= 6:
                target, _ = _name(raw, pos + 6)
                value = (struct.unpack(">H", data[4:6])[0], target)
            elif rtype == TYPE_A and rdlen == 4:
                value = socket.inet_ntoa(data)
            elif rtype == TYPE_AAAA and rdlen == 16:
                value = str(ipaddress.IPv6Address(data))
            elif rtype == TYPE_TXT:
                value = _txt(data)
            pos += rdlen
            if value is not None:
                out.append(Record(name.lower() if rtype in (TYPE_A, TYPE_AAAA) else name, rtype, ttl, value))
        return out
    except (ValueError, struct.error, IndexError):
        return []


def build_query(questions: list[tuple[str, int]]) -> bytes:
    """A query packet for (name, type) pairs. The unicast-response bit is set, so answers come straight back."""
    out = struct.pack(">6H", 0, 0, len(questions), 0, 0, 0)
    for name, rtype in questions:
        for label in name.rstrip(".").split("."):
            b = label.encode("utf-8")[:63]
            out += bytes([len(b)]) + b
        out += b"\0" + struct.pack(">HH", rtype, 0x8001)
    return out


def collect(packets: list[bytes], found: dict[str, Found], services: tuple[str, ...] = SERVICES) -> None:
    """Fold the records of the packets into `found` (instance → Found): PTRs add instances; SRV, TXT and address
    records fill them in, in whatever order they arrive and across packets."""
    recs = [r for p in packets for r in parse(p)]
    for r in recs:
        if r.rtype == TYPE_PTR and r.name.lower() in services and isinstance(r.value, str):
            found.setdefault(r.value, Found(r.name.lower(), r.value))
    hosts: dict[str, list[str]] = {}
    for r in recs:
        if r.rtype in (TYPE_A, TYPE_AAAA) and isinstance(r.value, str):
            hosts.setdefault(r.name.lower(), [])
            if r.value not in hosts[r.name.lower()]:
                hosts[r.name.lower()].append(r.value)
    for r in recs:
        f = found.get(r.name)
        if f is None:
            continue
        if r.rtype == TYPE_SRV and isinstance(r.value, tuple):
            f.port, f.host = r.value[0], r.value[1].lower()
        elif r.rtype == TYPE_TXT and isinstance(r.value, dict):
            f.txt.update(r.value)
    for f in found.values():
        for ip in hosts.get(f.host, []):
            if ip not in f.ips:
                f.ips.append(ip)


def _sockets(ips: list[str]) -> list[socket.socket]:
    out = []
    for ip in ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
            s.bind((ip, 0))
            s.setblocking(False)
            out.append(s)
        except OSError:
            continue
    return out


def _listen(socks: list[socket.socket], seconds: float) -> list[bytes]:
    import select

    got: list[bytes] = []
    end = time.monotonic() + seconds
    while socks and time.monotonic() < end:
        ready, _, _ = select.select(socks, [], [], max(0.0, min(0.1, end - time.monotonic())))
        for s in ready:
            try:
                data, _addr = s.recvfrom(9000)     # the sender's address is deliberately ignored
            except OSError:
                continue
            got.append(data)
    return got


def scan(local_ips: list[str], seconds: float = 2.0, services: tuple[str, ...] = SERVICES) -> list[Found]:
    """Ask on every given local address and return what answered. Blocks for about 2 x `seconds`: call it through
    asyncio.to_thread. An interface that will not send is skipped; no interfaces gives []."""
    socks = _sockets(local_ips)
    found: dict[str, Found] = {}
    try:
        for s in socks:
            try:
                s.sendto(build_query([(x, TYPE_PTR) for x in services]), GROUP)
            except OSError:
                pass
        packets = _listen(socks, seconds)
        collect(packets, found, services)
        # Second round: an instance with no host or port asks for its SRV and TXT; a host with no address, its A.
        ask: list[tuple[str, int]] = []
        for f in found.values():
            if not f.host or f.port is None:
                ask += [(f.instance, TYPE_SRV), (f.instance, TYPE_TXT)]
        for s in socks:
            for i in range(0, len(ask), 8):
                try:
                    s.sendto(build_query(ask[i:i + 8]), GROUP)
                except OSError:
                    pass
        packets += _listen(socks, seconds / 2 if ask else 0.0)
        collect(packets, found, services)
        ask = [(f.host, TYPE_A) for f in found.values() if f.host and not f.ips]
        for s in socks:
            for i in range(0, len(ask), 8):
                try:
                    s.sendto(build_query(ask[i:i + 8]), GROUP)
                except OSError:
                    pass
        packets += _listen(socks, seconds / 2 if ask else 0.0)
        collect(packets, found, services)
    finally:
        for s in socks:
            s.close()
    return sorted(found.values(), key=lambda f: f.instance.lower())
