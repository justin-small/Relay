"""Finding NDI sources over multicast DNS (#294).

NDI senders announce `_ndi._tcp` with an instance name `MACHINE (SOURCE)` (the source part can be blank or end in a
comma: `PTZ-1 (NDI HX2, )`), an SRV port (5961) and TXT `groups=public discovery=5960`. A sender in a custom group
(`groups=BirdDog-Comms`) is only for receivers watching that group, so every source carries its groups and `scan` takes
the groups to watch (`public` is always watched; with no groups set, every group is listed). A BirdDog converter also
announces `_birddog._tcp` whose TXT `srnname` and `srcip` say which NDI source it is set to; that is attached to the
source at that address as `converter`. Receivers announce nothing, so they cannot be listed from here.

`extra_ips` are asked directly (unicast mDNS on port 5353), for a source on another VLAN the multicast does not cross.
Everything goes out through the module's traffic class ("ndi"), so it follows the network port chosen for it.
Standard library only; the sender's frame size and rate come from the receiver (service.py), not from here.
"""
from __future__ import annotations

import logging
import re
import socket
import struct
import time

from ... import netsock

log = logging.getLogger("relay.ndi")

TRAFFIC = "ndi"
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
NDI = "_ndi._tcp.local"
BIRDDOG = "_birddog._tcp.local"
PTR, TXT, SRV, A = 12, 16, 33, 1
MAX_FOUND = 300
_INSTANCE = re.compile(r"^(?P<machine>.*?)\s*\((?P<source>.*)\)\s*$", re.S)


def _name(labels: list[str]) -> bytes:
    return b"".join(bytes([len(x.encode())]) + x.encode() for x in labels if x) + b"\0"


def question(name: str, rtype: int, unicast: bool = False) -> bytes:
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _name(name.rstrip(".").split(".")) \
        + struct.pack("!HH", rtype, 0x8001 if unicast else 1)


def _read_name(data: bytes, pos: int) -> tuple[str, int]:
    labels, end, jumped, hops = [], pos, False, 0
    while True:
        if pos >= len(data) or hops > 20:
            raise ValueError("bad name")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise ValueError("bad pointer")
            ptr = ((n & 0x3F) << 8) | data[pos + 1]
            if not jumped:
                end = pos + 2
            pos, jumped, hops = ptr, True, hops + 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if jumped else pos)


def parse_records(data: bytes) -> list[tuple[str, int, bytes, str]]:
    """Every resource record in a DNS message: (name, type, rdata, the name a PTR or SRV points at)."""
    try:
        _, _, qd, an, ns, ar = struct.unpack("!6H", data[:12])
        pos = 12
        for _ in range(qd):
            _, pos = _read_name(data, pos)
            pos += 4
        out = []
        for _ in range(an + ns + ar):
            name, pos = _read_name(data, pos)
            rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
            pos += 10
            rdata = data[pos:pos + rdlen]
            if len(rdata) != rdlen:
                raise ValueError("short record")
            target = ""
            if rtype == PTR:
                target, _ = _read_name(data, pos)
            elif rtype == SRV and rdlen >= 7:
                target, _ = _read_name(data, pos + 6)
            out.append((name, rtype, rdata, target))
            pos += rdlen
        return out
    except (struct.error, IndexError) as exc:
        raise ValueError("bad message") from exc


def parse_txt(rdata: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    pos = 0
    while pos < len(rdata):
        n = rdata[pos]
        item = rdata[pos + 1:pos + 1 + n].decode("utf-8", "replace")
        pos += 1 + n
        key, _, value = item.partition("=")
        if key and key.lower() not in out:
            out[key.lower()] = value.strip()[:200]
    return out


def split_instance(instance: str) -> tuple[str, str]:
    """`MACHINE (SOURCE)` -> (machine, source), read loosely: a blank source, a trailing comma and an instance with no
    brackets at all are all fine."""
    m = _INSTANCE.match(instance.strip())
    if not m:
        return instance.strip()[:80], ""
    return m.group("machine").strip()[:80], m.group("source").strip().strip(",").strip()[:80]


def parse_groups(value: str) -> list[str]:
    return [g for g in (x.strip() for x in re.split(r"[,;]", value or "")) if g][:20] or ["public"]


def collect(records: list[tuple[str, int, bytes, str]]) -> list[dict]:
    """The NDI sources in a set of records, each with its machine, source name, address, port and groups, and (when a
    BirdDog converter announced it) what that converter is set to."""
    srv = {n.lower(): (struct.unpack("!H", d[4:6])[0], t) for n, ty, d, t in records if ty == SRV and len(d) >= 7}
    txt = {n.lower(): parse_txt(d) for n, ty, d, _ in records if ty == TXT}
    addr = {n.lower().rstrip("."): socket.inet_ntoa(d) for n, ty, d, _ in records if ty == A and len(d) == 4}
    birddog: dict[str, dict] = {}
    found: dict[str, dict] = {}
    for n, ty, _, full in records:
        service = n.lower()
        if ty != PTR or not full or service not in (NDI, BIRDDOG):
            continue
        instance = full[: -len(service) - 1] if full.lower().endswith("." + service) else full
        port, host = srv.get(full.lower(), (0, ""))
        t = txt.get(full.lower(), {})
        address = addr.get(host.lower().rstrip("."), "")
        if service == BIRDDOG:
            if address and (t.get("srnname") or t.get("srcip")):
                birddog[address] = {"name": instance[:60], "source": t.get("srnname", "")[:80],
                                    "source_ip": t.get("srcip", "")[:45]}
            continue
        if full.lower() in found or len(found) >= MAX_FOUND:
            continue
        machine, source = split_instance(instance)
        found[full.lower()] = {"name": instance[:160], "machine": machine, "source": source, "host": host.rstrip("."),
                               "address": address, "port": port, "groups": parse_groups(t.get("groups", "")),
                               "converter": None}
    for f in found.values():
        f["converter"] = birddog.get(f["address"])
    return sorted(found.values(), key=lambda f: (f["machine"].lower(), f["source"].lower()))


def visible(sources: list[dict], groups: list[str]) -> list[dict]:
    """Only the sources in a watched group (`public` is always watched); every source when no groups are set."""
    if not groups:
        return sources
    watch = {g.lower() for g in groups} | {"public"}
    return [s for s in sources if any(g.lower() in watch for g in s["groups"])]


def scan(seconds: float = 2.5, extra_ips: list[str] | None = None) -> list[dict]:
    """Ask the LAN (and each extra address) for NDI sources and return what answers within `seconds`."""
    records: list[tuple[str, int, bytes, str]] = []
    asked: set[tuple[int, str, str]] = set()
    try:
        sock = netsock.multicast_sender(TRAFFIC, ttl=255)
    except netsock.RouteError as exc:
        log.info("NDI mDNS search not possible: %s", exc)
        return []
    targets = [(MDNS_GROUP, MDNS_PORT)] + [(ip, MDNS_PORT) for ip in (extra_ips or [])]
    first = [question(NDI, PTR), question(BIRDDOG, PTR)]
    try:
        sock.settimeout(0.2)
        deadline = time.monotonic() + seconds
        sent = -1.0
        while time.monotonic() < deadline:
            if time.monotonic() - sent > 0.9:
                for q in first:
                    for t in targets:
                        try:
                            sock.sendto(q, t)
                        except OSError:
                            pass
                sent = time.monotonic()
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                records += parse_records(data)
            except ValueError:
                continue
            records = records[-4000:]
            for n, ty, _, t in list(records):
                wants = []
                if ty == PTR and n.lower() in (NDI, BIRDDOG) and t:
                    wants = [(SRV, t), (TXT, t)]
                elif ty == SRV and t:
                    wants = [(A, t)]
                for ty2, name in wants:
                    if (ty2, name, "") not in asked:
                        asked.add((ty2, name, ""))
                        for tgt in targets:
                            try:
                                sock.sendto(question(name, ty2), tgt)
                            except OSError:
                                pass
    except OSError as exc:
        log.info("NDI mDNS search failed: %s", exc)
    finally:
        sock.close()
    return collect(records)
