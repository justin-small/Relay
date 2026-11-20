"""The Dante control requests monitoring sends (#258): clock status, subscriptions and meter levels.

UNVERIFIED. Without real Dante gear or a packet capture, the payload layouts below are Relay's assumption of what a
device answers; only the 10-byte header (protocol 0x27ff, length, sequence, command, result) matches #257's probe.
Every parser refuses what it cannot read (returns None) rather than guessing, so a device that answers differently
shows as "no reading" instead of a wrong one. Check each layout against a capture and fix it here: nothing else in
the module knows a byte offset.

Payload layouts, all big-endian, after the header:
  clock (0x1200):         flags u8 (bit0 leader, bit1 locked), pad u8, follower errors u16, latency us u32, interface errors u16
  subscriptions (0x3000): count u16, then per receive channel: channel u16, status u16, name length u8, name
                          (`<transmit channel>@<device>`, empty when not subscribed)
  levels (0x3400):        count u16, then one byte per channel: the level as -dBFS (0 = full scale, 255 = silence)
  set subscription (0x3010, #259): receive channel u16, name length u8, name (`<transmit channel>@<device>`; empty
                          name clears the subscription). A device that accepts answers result 1 and the same
                          channel u16; any other result is a refusal (a network under Dante Domain Manager refuses
                          writes unless Relay has its credentials).
"""
from __future__ import annotations

import select
import struct
import time

from ... import netsock

CLASS_ID = "dante"
HEADER = 10
PROTOCOL = 0x27FF
CMD_CLOCK = 0x1200
CMD_SUBSCRIPTIONS = 0x3000
CMD_LEVELS = 0x3400
CMD_SET_SUBSCRIPTION = 0x3010
MAX_CHANNELS = 512
SUB_NONE, SUB_OK = 0, 1       # any other status is a subscription that does not resolve
WAIT = 0.6


def request(command: int, sequence: int) -> bytes:
    return struct.pack(">HHHHH", PROTOCOL, HEADER, sequence & 0xFFFF, command, 0)


def reply(command: int, sequence: int, payload: bytes) -> bytes:
    """What a device answers (the fake device in tests builds its answers with this)."""
    return struct.pack(">HHHHH", PROTOCOL, HEADER + len(payload), sequence & 0xFFFF, command, 1) + payload


def body(data: bytes, command: int, sequence: int) -> bytes | None:
    """The payload of an answer to `command`/`sequence`, or None when it is not that answer."""
    if len(data) < HEADER:
        return None
    proto, length, seq, cmd, result = struct.unpack(">HHHHH", data[:HEADER])
    if proto != PROTOCOL or cmd != command or seq != sequence & 0xFFFF or result != 1 or length != len(data):
        return None
    return data[HEADER:]


def parse_clock(payload: bytes | None) -> dict | None:
    if payload is None or len(payload) != 10:
        return None
    flags, _, follower, latency, iface = struct.unpack(">BBHIH", payload)
    return {"leader": bool(flags & 1), "locked": bool(flags & 2), "follower_errors": follower,
            "latency_us": latency, "interface_errors": iface}


def parse_subscriptions(payload: bytes | None) -> list[dict] | None:
    """[{"channel", "status": "none"|"ok"|"unresolved", "source"}], or None when the answer is not readable."""
    if payload is None or len(payload) < 2:
        return None
    (count,) = struct.unpack(">H", payload[:2])
    if count > MAX_CHANNELS:
        return None
    out, at = [], 2
    for _ in range(count):
        if at + 5 > len(payload):
            return None
        channel, status, n = struct.unpack(">HHB", payload[at:at + 5])
        at += 5
        if at + n > len(payload):
            return None
        name = payload[at:at + n].decode("utf-8", "replace")
        at += n
        out.append({"channel": channel, "source": name,
                    "status": "none" if status == SUB_NONE else "ok" if status == SUB_OK else "unresolved"})
    return out if at == len(payload) else None


def parse_levels(payload: bytes | None) -> list[int] | None:
    """dBFS per channel (0 is full scale, -255 is silence), or None when the answer is not readable."""
    if payload is None or len(payload) < 2:
        return None
    (count,) = struct.unpack(">H", payload[:2])
    if count > MAX_CHANNELS or len(payload) != 2 + count:
        return None
    return [-b for b in payload[2:]]


def subscribe_request(channel: int, source: str, sequence: int) -> bytes:
    """A request to subscribe receive `channel` to `source` (`<transmit channel>@<device>`); "" clears it."""
    name = source.encode("utf-8")[:255]
    payload = struct.pack(">HB", channel, len(name)) + name
    return struct.pack(">HHHHH", PROTOCOL, HEADER + len(payload), sequence & 0xFFFF, CMD_SET_SUBSCRIPTION, 0) + payload


def exchange(ip: str, port: int, data: bytes, command: int, sequence: int, wait: float = WAIT) -> tuple[int, bytes] | None:
    """Send `data` and return (result, payload) of the device's answer to `command`/`sequence`, or None when none
    comes. Blocks up to `wait` seconds: call through to_thread. Raises netsock.RouteError when the Dante network is
    not there."""
    sock = netsock.udp_socket(CLASS_ID)
    try:
        sock.sendto(data, (ip, port))
        end = time.monotonic() + wait
        while time.monotonic() < end:
            ready, _, _ = select.select([sock], [], [], max(0.0, end - time.monotonic()))
            if not ready:
                break
            try:
                got, addr = sock.recvfrom(4096)
            except OSError:
                continue
            if addr[0] != ip or len(got) < HEADER:
                continue
            proto, length, seq, cmd, result = struct.unpack(">HHHHH", got[:HEADER])
            if proto == PROTOCOL and cmd == command and seq == sequence & 0xFFFF and length == len(got):
                return result, got[HEADER:]
        return None
    finally:
        sock.close()


def ask(ip: str, port: int, command: int, sequence: int = 1, wait: float = WAIT) -> bytes | None:
    """Send one request and return the payload of its answer, or None when none comes (or it is a refusal)."""
    got = exchange(ip, port, request(command, sequence), command, sequence, wait)
    return got[1] if got and got[0] == 1 else None


def set_subscription(ip: str, port: int, channel: int, source: str, sequence: int = 5, wait: float = 1.5) -> tuple[str, str]:
    """Write one subscription. Returns ("ok", ""), ("refused", why) or ("silent", why). UNVERIFIED like the rest."""
    got = exchange(ip, port, subscribe_request(channel, source, sequence), CMD_SET_SUBSCRIPTION, sequence, wait)
    if got is None:
        return "silent", "The device did not answer the change."
    result, payload = got
    if result == 1 and len(payload) >= 2 and struct.unpack(">H", payload[:2])[0] == channel:
        return "ok", ""
    return "refused", ("The device refused the change. On a network under Dante Domain Manager, changes are refused "
                       "unless Relay is given its credentials, which Relay does not support yet.")
