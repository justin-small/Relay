"""The Dante control requests monitoring sends (#258): clock status, subscriptions and meter levels.

The subscription list (0x3000), set subscription (0x3010) and clear subscription (0x3014) follow the layouts two
independent open projects use against real devices (chris-ritsen/network-audio-controller, public domain, and
dewiweb/aes67_visualizer, MIT). On 2026-11-20 the read side was checked on a real Allen & Heath DT168 stage box
(StageBox-1-Top, ARC version 2.7.41): channel count 0x1000, name 0x1002, info 0x1003, transmit lists 0x2000/0x2010
and the receive list 0x3000 all answered, and parse_rx_page read all 8 receive channels with their `<channel>@<device>`
sources. Its status word was 1 for every channel, which fits subscriptions to a device that is off the network
(remembered, not resolved); 9 and 10 for a live subscription are from the open projects, not yet seen. The write side
(0x3010, 0x3014) has not been sent to any real device.

Levels are no longer asked for here: they come from the multicast heartbeat (heartbeat.py), because 0x3400 is a channel
listing, not meter levels (Dante-BabelBox's capture notes). UNVERIFIED: the clock (0x1200) layout, which neither open
project uses; the heartbeat carries only the clock's frequency error, not leader or lock. Every parser
refuses what it cannot read (returns None) rather than guessing, so a device that answers differently shows as "no
reading" instead of a wrong one. Nothing else in the module knows a byte offset.

Requests are an 8-byte header (protocol 0x27ff, length, sequence, command) then a payload that starts with a u16 zero
(the slot a device fills with its result in an answer). Answers carry the result there: 1 is success, 0x8112 is
success with more pages. All big-endian. Payloads after the 10-byte header:
  clock (0x1200):         flags u8 (bit0 leader, bit1 locked), pad u8, follower errors u16, latency us u32, interface errors u16
  subscriptions (0x3000): asked a page at a time: u16 1, u16 first channel + 1, u16 0. The answer is space u8
                          (records per page, 16), count u8, then `space` records of 20 bytes, then a string heap. A
                          record is channel u16, flags u16, u16, transmit-channel pointer u16, transmit-device pointer
                          u16, name pointer u16, u16, status u16 (low byte 0 none, 9/10 subscribed, else unresolved),
                          u32. A pointer is an offset from the start of the whole packet to a NUL-terminated string
                          (0 = none).
  set subscription (0x3010): u8 2, count u8, then per record 00, receive channel u8, transmit-channel pointer u16,
                          transmit-device pointer u16, zero padding so the records block is at least 44 bytes, then the NUL-terminated strings. Device "." means the device itself.
                          Up to 16 records and receive channels up to 255. Accepted when the result is 1; a network
                          under Dante Domain Manager refuses writes unless Relay has its credentials.
  clear subscription (0x3014): count u16, then one u32 receive channel per record.
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
CMD_SET_SUBSCRIPTION = 0x3010
CMD_CLEAR_SUBSCRIPTION = 0x3014
MORE_PAGES = 0x8112
PAGE = 16                      # receive channels a device answers per page
RX_RECORD = 20
POINTER_BASE = 44              # a write's records block is padded to at least this many bytes
MAX_CHANNELS = 512
SUB_NONE, SUB_OK = 0, 1       # any other status is a subscription that does not resolve
WAIT = 0.6


def request(command: int, sequence: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HHHHH", PROTOCOL, HEADER + len(payload), sequence & 0xFFFF, command, 0) + payload


def reply(command: int, sequence: int, payload: bytes, result: int = 1) -> bytes:
    """What a device answers (the fake device in tests builds its answers with this)."""
    return struct.pack(">HHHHH", PROTOCOL, HEADER + len(payload), sequence & 0xFFFF, command, result) + payload


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


def page_request(first: int) -> bytes:
    """The payload that asks for the receive channels from `first` (0-based) on."""
    return struct.pack(">HHH", 1, first + 1, 0)


def rx_page(channels: list[tuple[int, str, int]], more: bool = False) -> tuple[bytes, int]:
    """What a device answers to a page request: (payload, result). `channels` is (channel, source, status low byte)
    with `<transmit channel>@<device>` as the source ("" when not subscribed). Used by the fake device in tests."""
    heap, recs = b"", b""
    base = HEADER + 2 + PAGE * RX_RECORD

    def point(text: str) -> int:
        nonlocal heap
        if not text:
            return 0
        at = base + len(heap)
        heap += text.encode("utf-8") + b"\0"
        return at

    for ch, source, status in channels[:PAGE]:
        chan, _, dev = source.rpartition("@") if "@" in source else (source, "", "")
        recs += struct.pack(">HHHHHHHHI", ch, 0, 0, point(chan), point(dev), 0, 0, status, 0)
    recs += b"\0" * (RX_RECORD * (PAGE - len(channels[:PAGE])))
    return bytes([PAGE, len(channels[:PAGE])]) + recs + heap, MORE_PAGES if more else 1


def _text(payload: bytes, pointer: int) -> str:
    at = pointer - HEADER
    if pointer == 0 or at < 0 or at >= len(payload):
        return ""
    end = payload.find(b"\0", at)
    return payload[at:end if end >= 0 else None].decode("utf-8", "replace")


def parse_rx_page(payload: bytes | None) -> list[dict] | None:
    """The receive channels in one page: [{"channel", "status", "source"}], or None when it is not readable."""
    if payload is None or len(payload) < 2:
        return None
    count = payload[1]
    if count > PAGE or len(payload) < 2 + count * RX_RECORD:
        return None
    out = []
    for i in range(count):
        at = 2 + i * RX_RECORD
        ch, _, _, tx_chan, tx_dev, _, _, status, _ = struct.unpack(">HHHHHHHHI", payload[at:at + RX_RECORD])
        chan, dev = _text(payload, tx_chan), _text(payload, tx_dev)
        low = status & 0xFF
        out.append({"channel": ch, "source": f"{chan}@{dev}" if chan else "",
                    "status": "none" if low == 0 else "ok" if low in (9, 10) else "unresolved"})
    return out


def read_subscriptions(ip: str, port: int, sequence: int = 3, wait: float = WAIT) -> list[dict] | None:
    """Every receive channel of a device, a page at a time, or None when it does not answer or answers unreadably."""
    out: list[dict] = []
    for page in range(MAX_CHANNELS // PAGE + 1):
        data = request(CMD_SUBSCRIPTIONS, sequence + page, page_request(len(out)))
        got = exchange(ip, port, data, CMD_SUBSCRIPTIONS, sequence + page, wait)
        if got is None or got[0] not in (1, MORE_PAGES):
            return None
        rows = parse_rx_page(got[1])
        if rows is None:
            return None
        out += rows
        if got[0] != MORE_PAGES or not rows:
            return out
    return None


def subscribe_request(channel: int, source: str, sequence: int) -> bytes | None:
    """A request to subscribe receive `channel` (1-255) to `source` (`<transmit channel>@<device>`; no `@` means a
    channel of the device itself). An empty `source` is a clear (clear_request). None when it cannot be written."""
    if not source:
        return clear_request(channel, sequence)
    if not 1 <= channel <= 255:
        return None
    chan, _, dev = source.rpartition("@") if "@" in source else (source, "", ".")
    if not chan or not dev:
        return None
    block = 4 + 6
    pad = max(0, POINTER_BASE - block)
    start = 8 + block + pad                      # packet offset of the strings
    chan_b, dev_b = chan.encode("utf-8") + b"\0", dev.encode("utf-8") + b"\0"
    payload = (struct.pack(">HBB", 0, 2, 1) + struct.pack(">BBHH", 0, channel, start, start + len(chan_b))
               + b"\0" * pad + chan_b + dev_b)
    return struct.pack(">HHHH", PROTOCOL, 8 + len(payload), sequence & 0xFFFF, CMD_SET_SUBSCRIPTION) + payload


def clear_request(channel: int, sequence: int) -> bytes:
    """A request to clear receive `channel`'s subscription."""
    payload = struct.pack(">HHI", 0, 1, channel)
    return struct.pack(">HHHH", PROTOCOL, 8 + len(payload), sequence & 0xFFFF, CMD_CLEAR_SUBSCRIPTION) + payload


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
    """Write one subscription (an empty `source` clears it). Returns ("ok", ""), ("refused", why) or ("silent", why).
    Not yet run on real Dante gear; the caller reads the device back to confirm."""
    data = subscribe_request(channel, source, sequence)
    if data is None:
        return "refused", "Relay cannot write that channel or source."
    command = CMD_SET_SUBSCRIPTION if source else CMD_CLEAR_SUBSCRIPTION
    got = exchange(ip, port, data, command, sequence, wait)
    if got is None:
        return "silent", "The device did not answer the change."
    if got[0] in (1, MORE_PAGES):
        return "ok", ""
    return "refused", ("The device refused the change. On a network under Dante Domain Manager, changes are refused "
                       "unless Relay is given its credentials, which Relay does not support yet.")
