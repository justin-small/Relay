"""Dante's heartbeat: what every Dante device announces about itself on multicast 224.0.0.233, port 8708.

Each device sends a packet about once a second with no request from anyone: a 0x20-byte header (protocol 0xfffe,
total length, the device's 8-byte identity at offset 8) followed by records. Relay reads two of them:
  0x8002 signal presence: a level byte for every transmit channel, then every receive channel.
  0x8001 clock frequency offset: the device's clock error against its leader, in parts per billion (a signed u32).
Layouts follow chris-ritsen/network-audio-controller (public domain, `heartbeat.rs`, `signal_presence.rs`,
`heartbeat_clock.rs`) and avstreamlens (MIT, `conmon.rs`). Checked on 2026-11-20 against 25 packets from 10 real
senders on the church's two Dante networks (a Dante Virtual Soundcard, a Dante Via-style PC and four hardware boxes
heard on both networks): every packet's records read, and the two records below match byte for byte. A packet that
does not fit those layouts is "no reading" (None), never a made-up one. All big-endian.

What real devices send: each sends a heartbeat about once a second, but only software devices and some others carry
0x8002 signal presence (2 of 10 senders did; the four hardware boxes did not). Their levels need the metering
requests (ports 8751 and 8752), which Relay does not make yet. 0x8001 comes from every sender.

Level byte, the "signal presence" scale: 0 is clipping, 1 to 252 is -(byte / 2) dBFS, 253 and up is silence or mute.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

PROTOCOL = 0xFFFE
HEADER = 0x20
GROUP = "224.0.0.233"
PORT = 8708
SIGNAL_PRESENCE = 0x8002
CLOCK_OFFSET = 0x8001
FIXED = 0x18               # a signal presence record before its level bytes
SILENCE = -127
MAX_AGE = 3.0              # seconds a device's last heartbeat still counts as its level


def level(byte: int) -> int:
    """A level byte as dBFS: 0 (clipping) is 0, then -(byte / 2) rounded, and silence is -127."""
    return 0 if byte == 0 else SILENCE if byte >= 253 else -round(byte / 2)


def records(data: bytes) -> list[tuple[int, bytes]] | None:
    """[(record type, record bytes)] of a heartbeat packet, or None when it is not one."""
    if len(data) < HEADER or struct.unpack(">HH", data[:4]) != (PROTOCOL, len(data)):
        return None
    out, at = [], HEADER
    while at < len(data):
        if at + 4 > len(data):
            return None
        size, kind = struct.unpack(">HH", data[at:at + 4])
        if size < 4 or size % 4 or at + size > len(data):
            return None
        out.append((kind, data[at:at + size]))
        at += size
    return out


def _presence(rec: bytes) -> dict | None:
    if len(rec) < FIXED:
        return None
    _, _, ext, plen, _, zero1, tx_n, tx_first, rx_n, rx_first, start, zero2 = struct.unpack(">HHHHHHHHHHHH", rec[:FIXED])
    if ext != 4 or plen % 4 or 8 + ext + plen != len(rec) or zero1 or zero2:
        return None
    tx_end, rx_end = start + tx_n, start + tx_n + rx_n
    if start < FIXED or rx_end > len(rec) or (rx_end + 3) & ~3 != len(rec):
        return None
    return {"tx_first": tx_first, "tx": list(rec[start:tx_end]), "rx_first": rx_first, "rx": list(rec[tx_end:rx_end])}


def parse(data: bytes) -> dict | None:
    """{"id", "presence": {"tx_first", "tx", "rx_first", "rx"} | None, "offset_ppb": int | None}, or None."""
    recs = records(data)
    ident = data[8:16]
    if recs is None or not any(ident) or ident == b"\xff" * 8:
        return None
    out = {"id": ident.hex(), "presence": None, "offset_ppb": None}
    for kind, rec in recs:
        if kind == SIGNAL_PRESENCE:
            out["presence"] = _presence(rec)
        elif kind == CLOCK_OFFSET and len(rec) >= 16 and struct.unpack(">H", rec[4:6])[0] >= 2:
            at = 8 + struct.unpack(">H", rec[4:6])[0]
            if at + 4 <= len(rec):
                out["offset_ppb"] = struct.unpack(">i", rec[at:at + 4])[0]
    return out


def packet(ident: bytes, tx: list[int] | None = None, rx: list[int] | None = None, offset_ppb: int | None = None,
           tx_first: int = 0, rx_first: int = 0) -> bytes:
    """A heartbeat packet as a device sends it (the fake device in tests builds its packets with this)."""
    body = b""
    if tx is not None or rx is not None:
        tx, rx = tx or [], rx or []
        raw = bytes(tx) + bytes(rx)
        size = (FIXED + len(raw) + 3) & ~3
        body += struct.pack(">HHHHHHHHHHHH", size, SIGNAL_PRESENCE, 4, size - 12, 1, 0, len(tx), tx_first, len(rx),
                            rx_first, FIXED, 0) + raw + b"\0" * (size - FIXED - len(raw))
    if offset_ppb is not None:
        body += struct.pack(">HHHHHHi", 16, CLOCK_OFFSET, 4, 4, 1, 0, offset_ppb)
    head = struct.pack(">HH", PROTOCOL, HEADER + len(body)) + b"\0" * 4 + ident.ljust(8, b"\0")[:8] + b"\0" * 16
    return head + body


class Heartbeats:
    """The latest heartbeat of each sender, by its IP address, kept as packets arrive."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def feed(self, data: bytes, ip: str, now: float | None = None) -> bool:
        """Keep a received packet. False when it is not a heartbeat Relay can read."""
        got = parse(data)
        if got is None:
            return False
        got["at"] = time.monotonic() if now is None else now
        with self._lock:
            self._latest[ip] = got
        return True

    def levels(self, ips: list[str], now: float | None = None) -> list[int | None] | None:
        """The transmit levels (dBFS, indexed from channel 0, None where the device sent none) of the device that
        sent from one of `ips` in the last few seconds, or None."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for ip in ips:
                got = self._latest.get(ip)
                if got and now - got["at"] <= MAX_AGE and got["presence"]:
                    p = got["presence"]
                    return [None] * p["tx_first"] + [level(b) for b in p["tx"]]
        return None

    def offset_ppb(self, ips: list[str], now: float | None = None) -> int | None:
        now = time.monotonic() if now is None else now
        with self._lock:
            for ip in ips:
                got = self._latest.get(ip)
                if got and now - got["at"] <= MAX_AGE and got["offset_ppb"] is not None:
                    return got["offset_ppb"]
        return None

    def listen(self, sock: socket.socket) -> None:
        """Read heartbeats from `sock` on a thread until stop()."""
        self.stop()
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._run, args=(sock,), name="dante-heartbeat", daemon=True)
        self._thread.start()

    def _run(self, sock: socket.socket) -> None:
        while self._sock is sock:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                return
            self.feed(data, addr[0])

    def stop(self) -> None:
        sock, thread, self._sock, self._thread = self._sock, self._thread, None, None
        if thread:
            thread.join(2)
        if sock:
            sock.close()
        with self._lock:
            self._latest.clear()
