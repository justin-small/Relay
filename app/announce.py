#!/usr/bin/env python3
"""Announce this Relay on the LAN so caption panels find it (mDNS / DNS-SD, standard library only).

The service is `_relay-captions._tcp` on the viewer port, with TXT `v=<version>`, `name=<install name>`,
`path=/`. It answers PTR, SRV, TXT and A questions for that one service, says so unprompted at start-up and
whenever the LAN address changes, and says goodbye (TTL 0) when stopped.

The app starts it on launch (Announcer, below), in a background thread. `RELAY_ANNOUNCE=0` turns it off and
`RELAY_INSTALL_NAME` names the install. Failing to announce (port 5353 taken, no network) is logged and never
stops Relay: an unannounced Relay still works at its address.

Standalone: python -m app.announce [--name NAME] [--port 8000] [--ip A.B.C.D]
"""
import argparse
import contextlib
import logging
import os
import re
import signal
import socket
import struct
import sys
import threading
import time

log = logging.getLogger("relay.announce")

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353

SERVICE = "_relay-captions._tcp.local"
META = "_services._dns-sd._udp.local"

TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV, TYPE_ANY = 1, 12, 16, 33, 255
CLASS_IN = 1
CACHE_FLUSH = 0x8000

TTL_HOST = 120
TTL_SERVICE = 4500

# How often the LAN address is re-read. The machine moves between venues.
RECHECK_S = 30


def lan_ip():
    """The address to announce: the first LAN address (app/netinfo.py), or None with no network."""
    from . import netinfo
    ips = netinfo.lan_ips()
    return ips[0] if ips else None


def clean_label(text, limit=63):
    """One DNS label's worth of an install name: printable, no dots, bounded."""
    text = "".join(c for c in text if c.isprintable()).replace(".", " ").strip()
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore").strip()


def host_label(name):
    """A host name for the A record: letters, digits, hyphens."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return "relay-" + (slug[:40] or "install")


def encode_name(name):
    out = b""
    for label in name.split("."):
        raw = label.encode("utf-8")
        if not raw or len(raw) > 63:
            raise ValueError("bad DNS label: %r" % label)
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def decode_name(data, pos):
    """Read a possibly compressed name. Returns (name, position after it)."""
    labels, jumped, end, hops = [], False, pos, 0
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
            jumped, pos, hops = True, ptr, hops + 1
            continue
        pos += 1
        labels.append(data[pos:pos + n].decode("utf-8", "replace"))
        pos += n
    return ".".join(labels), (end if jumped else pos)


def rr(name, rtype, ttl, rdata, flush=True):
    cls = CLASS_IN | (CACHE_FLUSH if flush else 0)
    return encode_name(name) + struct.pack(">HHIH", rtype, cls, ttl, len(rdata)) + rdata


class Service:
    """The records for one announced Relay."""

    def __init__(self, name, port, ip, version):
        self.name = clean_label(name) or "Relay"
        self.port = port
        self.ip = ip
        self.version = version
        self.instance = "%s.%s" % (self.name, SERVICE)
        self.host = "%s.local" % host_label(self.name)

    def txt(self):
        items = ["v=" + self.version, "name=" + self.name, "path=/"]
        out = b""
        for item in items:
            raw = item.encode("utf-8")[:255]
            out += bytes([len(raw)]) + raw
        return out

    def records(self, ttl_scale=1):
        """(ptr, srv, txt, a) as encoded records. ttl_scale=0 is a goodbye."""
        ttl_s, ttl_h = TTL_SERVICE * ttl_scale, TTL_HOST * ttl_scale
        ptr = rr(SERVICE, TYPE_PTR, ttl_s, encode_name(self.instance), flush=False)
        srv = rr(self.instance, TYPE_SRV, ttl_h,
                 struct.pack(">HHH", 0, 0, self.port) + encode_name(self.host))
        txt = rr(self.instance, TYPE_TXT, ttl_s, self.txt())
        a = rr(self.host, TYPE_A, ttl_h, socket.inet_aton(self.ip))
        return ptr, srv, txt, a

    def packet(self, answers, additional=(), ttl_scale=1):
        recs = self.records(ttl_scale)
        ans = [recs[i] for i in answers]
        add = [recs[i] for i in additional]
        header = struct.pack(">HHHHHH", 0, 0x8400, 0, len(ans), 0, len(add))
        return header + b"".join(ans) + b"".join(add)

    def announcement(self, ttl_scale=1):
        return self.packet((0, 1, 2, 3), ttl_scale=ttl_scale)

    def meta_answer(self):
        rec = rr(META, TYPE_PTR, TTL_SERVICE, encode_name(SERVICE), flush=False)
        return struct.pack(">HHHHHH", 0, 0x8400, 0, 1, 0, 0) + rec

    def answer_for(self, query):
        """The reply to one mDNS query packet, or None if it asks about nothing of ours."""
        if len(query) < 12:
            return None
        _, flags, qd = struct.unpack(">HHH", query[:6])
        if flags & 0x8000:  # a response, not a question
            return None
        pos, want = 12, set()
        try:
            for _ in range(qd):
                qname, pos = decode_name(query, pos)
                qtype, _qclass = struct.unpack(">HH", query[pos:pos + 4])
                pos += 4
                qname = qname.lower().rstrip(".")
                if qname == META.lower() and qtype in (TYPE_PTR, TYPE_ANY):
                    return self.meta_answer()
                if qname == SERVICE.lower() and qtype in (TYPE_PTR, TYPE_ANY):
                    want.add("ptr")
                elif qname == self.instance.lower() and qtype in (TYPE_SRV, TYPE_TXT, TYPE_ANY):
                    want.add("inst")
                elif qname == self.host.lower() and qtype in (TYPE_A, TYPE_ANY):
                    want.add("a")
        except (ValueError, struct.error):
            return None
        if "ptr" in want:
            return self.packet((0,), (1, 2, 3))  # PTR, with the rest alongside
        if "inst" in want:
            return self.packet((1, 2), (3,))
        if "a" in want:
            return self.packet((3,))
        return None


def open_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("", MDNS_PORT))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    return sock


def join(sock, ip):
    mreq = socket.inet_aton(MDNS_GROUP) + socket.inet_aton(ip)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as e:
        if e.errno not in (98, 48, 10048):  # already a member
            raise
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))


def send(sock, data):
    sock.sendto(data, (MDNS_GROUP, MDNS_PORT))


class Announcer:
    """Runs the announcement loop in a daemon thread until stop()."""

    def __init__(self, name, port, version="dev", ip=None):
        self.name, self.port, self.version, self.fixed_ip = name, port, version, ip
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._guarded, name="relay-announce", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _guarded(self):
        try:
            self._run()
        except Exception:
            log.warning("Panel announcement stopped: %s", sys.exc_info()[1])

    def _run(self):
        ip = self.fixed_ip or lan_ip()
        while ip is None:
            if self._stop.wait(5):
                return
            ip = lan_ip()
        svc = Service(self.name, self.port, ip, self.version)
        sock = open_socket()
        try:
            join(sock, ip)
            send(sock, svc.announcement())
            log.info("Announcing %s on the LAN at %s:%d (panels find Relay by this)", svc.instance, svc.ip, svc.port)
            self._stop.wait(1)
            send(sock, svc.announcement())
            sock.settimeout(1.0)
            next_check = time.monotonic() + RECHECK_S
            while not self._stop.is_set():
                try:
                    data, _addr = sock.recvfrom(9000)
                    reply = svc.answer_for(data)
                    if reply:
                        send(sock, reply)
                except socket.timeout:
                    pass
                except OSError:
                    self._stop.wait(1)
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + RECHECK_S
                    now = self.fixed_ip or lan_ip()
                    if now and now != svc.ip:
                        send(sock, svc.announcement(ttl_scale=0))
                        svc.ip = now
                        with contextlib.suppress(OSError):
                            join(sock, now)
                        send(sock, svc.announcement())
                        log.info("LAN address changed: announcing %s:%d", svc.ip, svc.port)
            send(sock, svc.announcement(ttl_scale=0))
        finally:
            sock.close()


def enabled():
    return (os.environ.get("RELAY_ANNOUNCE") or "1").strip().lower() not in ("0", "false", "no", "off")


def install_name():
    return os.environ.get("RELAY_INSTALL_NAME") or socket.gethostname().split(".")[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", default=install_name())
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--ip", default=None)
    ap.add_argument("--version", default="dev")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    announcer = Announcer(args.name, args.port, args.version, args.ip)
    announcer.start()
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    done.wait()
    announcer.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
