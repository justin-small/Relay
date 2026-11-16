"""Spike: find every NDI and Dante device and the UniFi switch port it is plugged into.

Standard library only, no judgement: a device counts as NDI or Dante only because of
something it does or is (it answers an mDNS query for the service, or its MAC belongs to
Audinate), never because of its name. Run it on a computer on the show network:

    python av_ports.py --key-file C:\\Users\\lv1\\unifi.key [--udm 10.10.0.1] [--json]

The UniFi API key is read from a file so it never appears on a command line.

How each device is found:
  mdns     answered a multicast mDNS query on this computer's network
  unicast  answered an mDNS query sent straight to its IP (port 5353), which reaches
           devices on other VLANs when the gateway routes and allows it
  oui      its MAC address starts with an Audinate prefix (Dante hardware)
Then its IP or MAC is looked up in UniFi's client list (stat/sta), which gives the
switch MAC and port number; the switch MAC is turned into its name and the port's name
with UniFi's device list (stat/device).
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import socket
import ssl
import struct
import sys
import time
import urllib.request

SERVICES = {
    "_ndi._tcp.local": "NDI",
    "_netaudio-arc._udp.local": "Dante",
}
# Audinate's registered MAC prefixes (IEEE OUI). Dante modules (Brooklyn, Ultimo, Broadway)
# ship with these, so hardware that uses them is Dante whatever it is called.
DANTE_OUIS = {"00:1d:c1"}
MDNS = ("224.0.0.251", 5353)


# --- DNS wire format ---------------------------------------------------------------------

def _query(names: list[str], unicast_reply: bool) -> bytes:
    """A DNS query for the PTR records of `names`; QU bit asks for a unicast answer."""
    out = struct.pack("!HHHHHH", random.randint(0, 0xFFFF), 0, len(names), 0, 0, 0)
    for name in names:
        for label in name.split("."):
            out += bytes([len(label)]) + label.encode()
        out += b"\0" + struct.pack("!HH", 12, 0x8001 if unicast_reply else 1)
    return out


def _name(buf: bytes, off: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        n = buf[off]
        if n & 0xC0 == 0xC0:
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | buf[off + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError("pointer loop")
            continue
        if n == 0:
            return ".".join(labels), (end if end is not None else off + 1)
        labels.append(buf[off + 1: off + 1 + n].decode("utf-8", "replace"))
        off += 1 + n


def parse(buf: bytes) -> list[tuple[str, int, object]]:
    """Every answer/authority/additional record as (name, type, value)."""
    _, flags, qd, an, ns, ar = struct.unpack("!HHHHHH", buf[:12])
    off = 12
    for _ in range(qd):
        _, off = _name(buf, off)
        off += 4
    records = []
    for _ in range(an + ns + ar):
        name, off = _name(buf, off)
        rtype, _, _, rdlen = struct.unpack("!HHIH", buf[off: off + 10])
        off += 10
        rdata = buf[off: off + rdlen]
        if rtype == 12:  # PTR
            value = _name(buf, off)[0]
        elif rtype == 33:  # SRV
            port = struct.unpack("!H", rdata[4:6])[0]
            value = (_name(buf, off + 6)[0], port)
        elif rtype == 1 and rdlen == 4:  # A
            value = socket.inet_ntoa(rdata)
        elif rtype == 16:  # TXT
            items, i = [], 0
            while i < len(rdata):
                n = rdata[i]
                items.append(rdata[i + 1: i + 1 + n].decode("utf-8", "replace"))
                i += 1 + n
            value = items
        else:
            value = None
        records.append((name.lower(), rtype, value))
        off += rdlen
    return records


def _collect(sock: socket.socket, seconds: float) -> list[tuple[str, list]]:
    """(sender IP, records) for every reply that arrives within `seconds`."""
    replies, deadline = [], time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            data, (ip, _) = sock.recvfrom(9000)
        except (socket.timeout, ConnectionResetError, OSError):
            break
        try:
            replies.append((ip, parse(data)))
        except (ValueError, IndexError, struct.error):
            continue
    return replies


def _services(replies: list[tuple[str, list]], how: str) -> list[dict]:
    """Turn mDNS replies into one row per announced NDI/Dante instance."""
    found = []
    for sender, records in replies:
        a = {n: v for n, t, v in records if t == 1}
        srv = {n: v for n, t, v in records if t == 33}
        txt = {n: v for n, t, v in records if t == 16}
        for n, t, v in records:
            if t != 12 or n not in SERVICES:
                continue
            inst = v.lower()
            host, port = srv.get(inst, (None, None))
            ip = a.get(host.lower()) if host else None
            found.append({
                "kind": SERVICES[n], "how": how, "instance": v.split("." + n.split(".")[0])[0],
                "ip": ip or sender, "service_port": port, "txt": txt.get(inst, []),
            })
    return found


def mdns_multicast(seconds: float = 3.0) -> list[dict]:
    """Ask the local network for NDI and Dante services, as a one-shot (legacy) querier."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    s.bind(("", 0))
    s.sendto(_query(list(SERVICES), unicast_reply=True), MDNS)
    return _services(_collect(s, seconds), "mdns")


def mdns_unicast(ips: list[str], seconds: float = 1.5) -> list[dict]:
    """Send the same query to each IP's port 5353 (RFC 6762 §5.5): crosses VLANs if routed."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    q = _query(list(SERVICES), unicast_reply=True)
    for ip in ips:
        try:
            s.sendto(q, (ip, 5353))
        except OSError:
            pass
    return _services(_collect(s, seconds), "unicast")


# --- UniFi -------------------------------------------------------------------------------

def unifi(udm: str, key: str, path: str) -> list[dict]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # the UDM's certificate is self-signed
    req = urllib.request.Request(f"https://{udm}/proxy/network/api/s/default/{path}",
                                 headers={"X-API-KEY": key, "Accept": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:  # nosec B310: fixed https URL
        return json.load(r)["data"]


# --- Correlate ---------------------------------------------------------------------------

def correlate(found: list[dict], clients: list[dict], devices: list[dict]) -> list[dict]:
    by_ip = {c["ip"]: c for c in clients if c.get("ip")}
    switches = {d["mac"]: d for d in devices}
    rows, seen = [], set()
    for f in found:
        c = by_ip.get(f["ip"]) or next((x for x in clients if x["mac"] == f.get("mac")), None)
        key = (f["kind"], (c or {}).get("mac") or f["ip"])
        if key in seen:
            # same device found twice (e.g. mdns and unicast): keep the first, note the other
            for r in rows:
                if (r["kind"], r["mac"] or r["ip"]) == key and f["how"] not in r["how"]:
                    r["how"] += "+" + f["how"]
                    r["instance"] = r["instance"] or f.get("instance")
            continue
        seen.add(key)
        sw = switches.get((c or {}).get("sw_mac"), {})
        port = (c or {}).get("sw_port")
        pname = next((p.get("name") for p in sw.get("port_table", []) if p.get("port_idx") == port), None)
        rows.append({
            "kind": f["kind"], "how": f["how"], "instance": f.get("instance"),
            "ip": f["ip"] or (c or {}).get("ip"), "mac": (c or {}).get("mac") or f.get("mac"),
            "unifi_name": (c or {}).get("name") or (c or {}).get("hostname"),
            "vlan": (c or {}).get("vlan"), "switch": sw.get("name"), "port": port, "port_name": pname,
            "wired": (c or {}).get("is_wired"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--udm", default="10.10.0.1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    with open(args.key_file, encoding="utf-8") as f:
        key = f.read().strip()

    with concurrent.futures.ThreadPoolExecutor() as pool:
        clients_f = pool.submit(unifi, args.udm, key, "stat/sta")
        devices_f = pool.submit(unifi, args.udm, key, "stat/device")
        multicast_f = pool.submit(mdns_multicast)
        clients, devices, found = clients_f.result(), devices_f.result(), multicast_f.result()

    found += mdns_unicast(sorted({c["ip"] for c in clients if c.get("ip")}))
    for c in clients:
        if c.get("mac", "")[:8].lower() in DANTE_OUIS:
            found.append({"kind": "Dante", "how": "oui", "instance": None, "ip": c.get("ip"), "mac": c["mac"]})

    rows = sorted(correlate(found, clients, devices), key=lambda r: (r["kind"], r["switch"] or "~", r["port"] or 0))
    if args.json:
        json.dump(rows, sys.stdout, indent=2)
        return 0
    cols = ["kind", "how", "instance", "ip", "mac", "unifi_name", "switch", "port", "port_name"]
    width = {c: max(len(c), *(len(str(r[c] or "")) for r in rows)) for c in cols} if rows else {}
    print("  ".join(c.ljust(width[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "-").ljust(width[c]) for c in cols))
    print(f"\n{len(rows)} devices; {len(clients)} UniFi clients, {len(devices)} UniFi devices")
    return 0


if __name__ == "__main__":
    sys.exit(main())
