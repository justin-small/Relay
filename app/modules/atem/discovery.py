"""Finding Blackmagic gear on the LAN (#386): multicast DNS, and a probe of the subnet for ATEMs that mDNS misses.

`discover()` asks the LAN over mDNS (standard library only, like core's announcer) for the service types Blackmagic
products announce, and for the list of every service type, following any that look like Blackmagic's. It lists the
whole range (switchers, HyperDecks, Videohubs, Teranex, cameras, Web Presenters, Cloud Store, ...) so the operator
sees what is there; only ATEMs can be added here. Control of the others is for their own modules.

Checked on real hardware (2026-11-16): an ATEM Television Studio HD III (8.1.1) announces `_blackmagic._tcp` on 9910
with TXT `name`, `class=AtemSwitcher`, `release version` and `unique id`. An ATEM 2 M/E Constellation HD (9.6.2) does
not: it announces `_switcher_ctrl._udp` on 9910 (TXT: only `unique id`) and `_videohub._tcp` on 9990 (its router, no TXT),
and `_http._tcp` on 80 under the same instance name, whose TXT carries `device name`, `release version` and `unique id`
(uppercase).
The control service's TXT has only a lowercase `unique id`, so every instance fills the keys its own TXT lacks from its
`_http._tcp` twin, and ids are compared lowercase. `_hyperdeck_ctrl._tcp` is asked for
too, and the service-type list catches anything else with a Blackmagic word in its name. A product is told apart by
the words in its service type, its TXT `class` and `name`, and its instance name.

`probe()` is the fallback where mDNS is blocked or the switcher is on a VLAN that does not pass it: it sends the
ATEM's "hello" to every address in this computer's /24 and lists the ones that answer. A switcher holds a session
open for a few seconds for each hello it answers, so the probe runs only when the operator asks for it.

Both go out through the module's traffic class ("atem"), so they use the network port the operator chose for it.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import struct
import time

from ... import netinfo, netroute, netsock
from . import protocol as p

log = logging.getLogger("relay.atem")

TRAFFIC = "atem"
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
META = "_services._dns-sd._udp.local"
SERVICES = ("_blackmagic._tcp.local", "_switcher_ctrl._udp.local", "_hyperdeck_ctrl._tcp.local", "_videohub._tcp.local")
HTTP = "_http._tcp.local"  # where a Constellation keeps the TXT its other services leave out
_BMD_WORDS = ("blackmagic", "bmd", "atem", "hyperdeck", "videohub", "teranex", "ultimatte")
MAX_FOUND = 200
MAX_PROBE_NETS = 2

PTR, TXT, SRV, A = 12, 16, 33, 1

# (family, label, words that mark it), checked in order.
FAMILIES = (
    ("atem", "ATEM switcher", ("atem", "switcher_ctrl")),
    ("hyperdeck", "HyperDeck recorder", ("hyperdeck",)),
    ("videohub", "Videohub router", ("videohub",)),
    ("teranex", "Teranex converter", ("teranex",)),
    ("ultimatte", "Ultimatte keyer", ("ultimatte",)),
    ("streaming", "Web Presenter / Streaming", ("web presenter", "webpresenter", "streaming")),
    ("storage", "Cloud Store", ("cloud store", "cloudstore", "cloud pod")),
    ("monitor", "SmartView / SmartScope", ("smartview", "smartscope")),
    ("camera", "Camera", ("ursa", "pocket", "studio camera", "camera")),
)
_UID_KEYS = ("unique id", "uniqueid", "unique_id", "serial", "serial number", "id")
_UID_OK = re.compile(r"[^A-Za-z0-9:._\-]")


# ------------------------------------------------------------------------------------------------ wire format
def _name(labels: list[str]) -> bytes:
    return b"".join(bytes([len(x.encode())]) + x.encode() for x in labels if x) + b"\0"


def question(name: str, rtype: int) -> bytes:
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _name(name.rstrip(".").split(".")) + struct.pack("!HH", rtype, 1)


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
            out[key.lower()] = value.strip()[:100]
    return out


# ------------------------------------------------------------------------------------------------ what was found
def family(*texts: str) -> tuple[str, str]:
    hay = " ".join(t.lower() for t in texts if t)
    for fid, label, words in FAMILIES:
        if any(w in hay for w in words):
            return fid, label
    return "other", "Blackmagic device"


def _looks_blackmagic(service: str) -> bool:
    s = service.lower()
    return any(w in s for w in _BMD_WORDS)


def _uid(txt: dict[str, str], instance: str) -> str:
    for key in _UID_KEYS:
        if txt.get(key):
            return _UID_OK.sub("", txt[key])[:64].lower()  # a Constellation sends its id in both cases
    return ("n:" + _UID_OK.sub("-", instance))[:64] if instance else ""


def collect(records: list[tuple[str, int, bytes, str]], services: set[str]) -> list[dict]:
    """Devices in a set of records: one per instance of a Blackmagic service type, with its address when known."""
    srv = {n.lower(): (struct.unpack("!H", d[4:6])[0], t) for n, ty, d, t in records if ty == SRV and len(d) >= 7}
    txt = {n.lower(): parse_txt(d) for n, ty, d, _ in records if ty == TXT}
    addr = {n.lower().rstrip("."): socket.inet_ntoa(d) for n, ty, d, _ in records if ty == A and len(d) == 4}
    found: dict[str, dict] = {}
    for n, ty, _, full in records:
        service = n.lower()
        if ty != PTR or service not in services or not full or full.lower() in found:
            continue
        instance = full[: -len(service) - 1] if full.lower().endswith("." + service) else full
        port, host = srv.get(full.lower(), (0, ""))
        t = {**txt.get(f"{instance}.{HTTP}".lower(), {}), **txt.get(full.lower(), {})} if service != HTTP \
            else txt.get(full.lower(), {})
        model = t.get("name") or t.get("model") or t.get("device name") or instance
        fid, label = family(service, t.get("class", ""), t.get("name", ""), instance)
        found[full.lower()] = {
            "name": instance[:60], "model": model[:60], "family": fid, "kind": label, "atem": fid == "atem",
            "service": service.removesuffix(".local"), "host": host.rstrip("."),
            "address": addr.get(host.lower().rstrip("."), ""), "port": port, "uid": _uid(t, instance),
            "firmware": (t.get("release version") or t.get("internal version") or t.get("version") or "")[:40],
        }
        if len(found) >= MAX_FOUND:
            break
    # One device may announce several service types: keep one entry per unique id. An ATEM entry beats any other, then
    # the one on the switcher's control port (a Constellation's router also says ATEM), then one with an address; an
    # address the kept entry lacks is taken from the one dropped.
    def rank(f: dict) -> tuple[bool, bool, bool]:
        return f["atem"], f["atem"] and f["port"] == p.PORT, bool(f["address"])

    out: dict[str, dict] = {}
    for f in found.values():
        key = f["uid"] or f["address"] or f["name"]
        have = out.get(key)
        keep, drop = (f, have) if have is None or rank(f) > rank(have) else (have, f)
        if drop and not keep["address"] and drop["address"] and drop["host"] in ("", keep["host"]):
            keep = {**keep, "address": drop["address"]}
        out[key] = keep
    return sorted(out.values(), key=lambda f: (not f["atem"], f["kind"], f["name"].lower()))


def discover(seconds: float = 2.5) -> list[dict]:
    """Ask the LAN for Blackmagic products over mDNS and return what answers within `seconds`."""
    services = set(SERVICES)
    records: list[tuple[str, int, bytes, str]] = []
    asked: set[tuple[int, str]] = set()
    try:
        sock = netsock.multicast_sender(TRAFFIC, ttl=255)
    except netsock.RouteError as exc:
        log.info("Blackmagic mDNS search not possible: %s", exc)
        return []
    first = [question(s, PTR) for s in SERVICES] + [question(META, PTR)]
    try:
        sock.settimeout(0.2)
        deadline = time.monotonic() + seconds
        sent = -1.0
        while time.monotonic() < deadline:
            if time.monotonic() - sent > 0.9:
                for q in first:
                    sock.sendto(q, (MDNS_GROUP, MDNS_PORT))
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
                if ty == PTR and n.lower() == META and t and _looks_blackmagic(t) and t.lower() not in services:
                    services.add(t.lower())
                    first.append(question(t, PTR))
                    sock.sendto(first[-1], (MDNS_GROUP, MDNS_PORT))
                # A responder that answered with less than all of it: ask for the rest by name.
                wants = []
                if ty == PTR and n.lower() in services and t:
                    wants = [(SRV, t), (TXT, t)]
                    if n.lower() != HTTP and t.lower().endswith("." + n.lower()):
                        wants.append((TXT, f"{t[: -len(n) - 1]}.{HTTP}"))
                elif ty == SRV and t:
                    wants = [(A, t)]
                for want in wants:
                    if want not in asked:
                        asked.add(want)
                        sock.sendto(question(want[1], want[0]), (MDNS_GROUP, MDNS_PORT))
    except OSError as exc:
        log.info("Blackmagic mDNS search failed: %s", exc)
    finally:
        sock.close()
    return collect(records, services)


# ------------------------------------------------------------------------------------------------ subnet probe
def probe_targets() -> list[str]:
    """Every other address in this computer's /24 on the port the ATEM's traffic goes out of (at most two)."""
    try:
        r = netroute.resolve(TRAFFIC)
        ports = [r.port] if r.port is not None and r.address else []
    except KeyError:
        ports = []
    if not ports:
        all_ports = [x for x in netinfo.interfaces() if not x.loopback and not x.vpn and x.ipv4]
        ports = [x for x in all_ports if x.default_route] or all_ports
    nets, mine = [], set()
    for port in ports:
        for cidr in port.ipv4:
            try:
                iface = ipaddress.ip_interface(cidr)
            except ValueError:
                continue
            mine.add(str(iface.ip))
            net = iface.network if iface.network.prefixlen >= 24 else ipaddress.ip_network(f"{iface.ip}/24", strict=False)
            if net not in nets:
                nets.append(net)
    out = []
    for net in nets[:MAX_PROBE_NETS]:
        out += [str(h) for h in net.hosts() if str(h) not in mine]
    return out


def probe(seconds: float = 1.5, targets: list[str] | None = None, port: int = p.PORT) -> list[dict]:
    """Send the ATEM hello to each address and list those that answer as a switcher would."""
    targets = probe_targets() if targets is None else targets
    if not targets:
        return []
    try:
        sock = netsock.udp_socket(TRAFFIC)
    except netsock.RouteError as exc:
        log.info("ATEM probe not possible: %s", exc)
        return []
    found: dict[str, dict] = {}
    try:
        sock.settimeout(0.1)
        for host in targets:
            try:
                sock.sendto(p.HELLO, (host, port))
            except OSError:
                continue
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                data, (addr, _) = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                continue
            try:
                head = p.parse_header(data)
            except p.Malformed:
                continue
            if head["flags"] & p.NEW_SESSION and addr in targets and addr not in found:
                found[addr] = {"name": f"ATEM at {addr}", "model": "", "family": "atem", "kind": "ATEM switcher",
                               "atem": True, "service": "probe", "host": "", "address": addr, "port": port,
                               "uid": "", "firmware": ""}
    finally:
        sock.close()
    return sorted(found.values(), key=lambda f: tuple(int(x) for x in f["address"].split(".")))
