"""Dante metering for hardware boxes (#585): ask a device to push its signal levels to us, and read what it sends.

A stage box sends no levels in its heartbeat (see heartbeat.py). Dante Controller gets them by telling each device,
on the device's control-and-monitoring port 8800, "send your meters to this address and port" (id 0x1200, opcode 0x3010,
the device name, the controller's MAC, IP and port 8751), and repeating that about every 3 seconds. The device then
pushes meter packets to that port. Layouts follow chris-ritsen/network-audio-controller (public domain,
`commands/metering.rs`, `metering.rs`) and are proven against real hardware by tools in the notes of #585.
All big-endian. A packet that does not fit is "no reading" (None), never a made-up one.

Two frame versions were seen from real gear on 2026-11-21 (a DT168 stage box sends version 3, another device
version 2). Level byte, the "detailed" scale: 0 is clipping, 1 to 253 is -((byte - 1) / 2) dBFS, 254 is muted, 255 is a framing
marker (no reading).
"""
from __future__ import annotations

import socket
import struct
import threading
import time

PROTOCOL_CMC = 0x1200
OPCODE = 0x3010
PORT = 8751
CONTROL_PORT = 8800
MARKER = b"Audinate"
RENEW = 3.0                 # seconds between requests; the device stops pushing soon after the last one
MUTED = -127
MAX_AGE = 3.0              # seconds a device's last push still counts as its level


def level(byte: int) -> int | None:
    """A detailed-scale level byte as dBFS (0 = clipping, 254 = muted as -127), or None for the framing marker."""
    if byte == 0:
        return 0
    if byte == 254:
        return MUTED
    if byte >= 255:
        return None
    return -round((byte - 1) / 2)


def _frame(protocol: int, message_id: int, body: bytes) -> bytes:
    return struct.pack(">HHH", protocol, 6 + len(body), message_id & 0xFFFF) + body


def request(device_name: str, ip: str, mac: bytes, port: int = PORT, message_id: int = 0) -> bytes:
    """The 8800 message that makes `device_name` push its meters to ip:port (all zeros there stops it)."""
    name = device_name.encode() + b"\0"
    name += b"\0" * (len(name) % 2)
    size = len(name)
    dest = bytes(int(p) for p in ip.split(".")) if ip else b"\0" * 4
    tail = size + 0x0C + 4
    body = (struct.pack(">HHH", OPCODE, 0, 0) + mac[:6].ljust(6, b"\0")
            + struct.pack(">HHHHHH", 0, 4, size + 0x0A, 2, size + 0x0C, 0x000A) + name
            + struct.pack(">HHHHHHH", 1, 1, tail, 1, port, 1, 0) + dest + struct.pack(">H", port)
            + b"\0" * 6 + struct.pack(">H", port) + b"\0\0")
    return _frame(PROTOCOL_CMC, message_id, body)


def stop(device_name: str, mac: bytes, port: int = PORT) -> bytes:
    """The same message with the destination cleared: the device stops pushing."""
    pkt = bytearray(request(device_name, "", mac, port))
    pkt[-16:] = b"\0" * 16
    return bytes(pkt)


def frame(ident: bytes, sequence: int, tx: list[int], rx: list[int] | None = None, version: int = 2) -> bytes:
    """A meter packet as a device pushes it (the fake device in tests builds its packets with this). Version 2 has one
    count byte each for transmit and receive and a trailing zero; version 3 (the DT168 stage boxes) a pad byte and a
    u16 count each, and no trailing zero."""
    rx = rx or []
    head = b"\0\0" + ident.ljust(8, b"\0")[:8] + MARKER
    if version == 3:
        body = head + bytes([3, 0]) + struct.pack(">HH", len(tx), len(rx)) + bytes(tx) + bytes(rx)
    else:
        body = head + bytes([2, len(tx), len(rx)]) + bytes(tx) + bytes(rx) + b"\0"
    return _frame(0xFFFF, sequence, body)


def parse(data: bytes) -> dict | None:
    """{"id", "sequence", "tx": [dBFS|None], "rx": [dBFS|None]} for a pushed meter packet, or None when it is not one
    (a count that does not fit the packet's length is not one)."""
    if len(data) < 28 or struct.unpack(">HH", data[:4]) != (0xFFFF, len(data)) or data[16:24] != MARKER:
        return None
    version = data[24]
    if version == 2:
        tx_n, rx_n, at, tail = data[25], data[26], 27, 1
    elif version == 3 and len(data) >= 30:
        tx_n, rx_n = struct.unpack(">HH", data[26:30])
        at, tail = 30, 0
    else:
        return None
    if at + tx_n + rx_n + tail != len(data):
        return None
    levels = data[at:at + tx_n + rx_n]
    return {"id": data[8:16].hex(), "sequence": struct.unpack(">H", data[4:6])[0],
            "tx": [level(b) for b in levels[:tx_n]], "rx": [level(b) for b in levels[tx_n:]]}


class Meters:
    """Asks devices to push their meters here, and keeps the latest push of each sender by its IP address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, dict] = {}
        self._asked: dict[str, float] = {}
        self._devices: dict[str, tuple[str, list[str]]] = {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._mac = b"\0" * 6
        self._sequence = 0

    def feed(self, data: bytes, ip: str, now: float | None = None) -> bool:
        """Keep a received packet. False when it is not a meter packet Relay can read."""
        got = parse(data)
        if got is None:
            return False
        got["at"] = time.monotonic() if now is None else now
        with self._lock:
            self._latest[ip] = got
        return True

    def levels(self, ips: list[str], now: float | None = None) -> list[int | None] | None:
        """The transmit levels (dBFS, None for no reading) the device at one of `ips` pushed in the last few seconds."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for ip in ips:
                got = self._latest.get(ip)
                if got and now - got["at"] <= MAX_AGE:
                    return list(got["tx"])
        return None

    def ask(self, name: str, ips: list[str], now: float | None = None, port: int = CONTROL_PORT) -> bool:
        """Send the request to a device unless one went out in the last RENEW seconds. True when sent."""
        sock = self._sock
        now = time.monotonic() if now is None else now
        if sock is None or not ips:
            return False
        key = ips[0]
        with self._lock:
            if now - self._asked.get(key, -RENEW) < RENEW:
                return False
            self._asked[key] = now
            self._devices[key] = (name, list(ips))
            self._sequence = (self._sequence + 1) & 0xFFFF
            seq = self._sequence
        mine = sock.getsockname()
        pkt = request(name, mine[0], self._mac, mine[1], seq)
        sent = False
        for ip in ips:
            try:
                sock.sendto(pkt, (ip, port))
                sent = True
            except OSError:
                continue
        return sent

    def release(self, port: int = CONTROL_PORT) -> None:
        """Tell every device that was asked to stop pushing."""
        sock = self._sock
        if sock is None:
            return
        with self._lock:
            devices = list(self._devices.values())
        for name, ips in devices:
            pkt = stop(name, self._mac, sock.getsockname()[1])
            for ip in ips:
                try:
                    sock.sendto(pkt, (ip, port))
                except OSError:
                    continue

    def listen(self, sock: socket.socket, mac: bytes = b"\0" * 6) -> None:
        """Read pushed meters from `sock` (bound to the address the devices should push to) on a thread until stop()."""
        self.stop()
        sock.settimeout(0.5)
        self._sock, self._mac = sock, mac
        self._thread = threading.Thread(target=self._run, args=(sock,), name="dante-meters", daemon=True)
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
            self._asked.clear()
            self._devices.clear()
