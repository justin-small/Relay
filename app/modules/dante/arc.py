"""The Dante control requests monitoring sends (#258): clock status, subscriptions and meter levels.

The subscription list (0x3000), set subscription (0x3010) and clear subscription (0x3014) follow the layouts two
independent open projects use against real devices (chris-ritsen/network-audio-controller, public domain, and
dewiweb/aes67_visualizer, MIT). On 2026-11-20 the read side was checked on a real Allen & Heath DT168 stage box
(StageBox-1-Top, ARC version 2.7.41): channel count 0x1000, name 0x1002, info 0x1003, transmit lists 0x2000/0x2010
and the receive list 0x3000 all answered, and parse_rx_page read all 8 receive channels with their `<channel>@<device>`
sources. Its status word was 1 for every channel, which fits subscriptions to a device that is off the network
(remembered, not resolved); 9 and 10 for a live subscription are from the open projects, not yet seen. The write
packets match Dante Controller's byte for byte (tests/test_dante_routing.py) but Relay has not sent one to a
real device yet.

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
  set subscription (0x3010), as Dante Controller sends it (captured 2026-11-20, protocol 0x2729): u16 0, 0x08, count
                          (one), then per record 00, receive channel u8, transmit-channel pointer u16, transmit-
                          device pointer u16, zero padding so the strings start at byte 172 of the packet, then
                          `<transmit channel>\\0<transmit device>\\0`. The answer is 10 bytes with result 1. Receive
                          channels up to 255. Clearing is the same packet with both pointers 0 (and no strings), 172
                          bytes long: Controller never sent 0x3014. A network under Dante Domain Manager refuses
                          writes unless Relay has its credentials.
"""
from __future__ import annotations

import select
import struct
import time

from ... import netsock

CLASS_ID = "dante"
HEADER = 10
PROTOCOL = 0x27FF
PROTOCOL_WRITE = 0x2729       # what Dante Controller uses for writes to the older (2.7.x) devices
REPLY_PROTOCOLS = (0x27FF, 0x2729, 0x2809)
CMD_COUNTS = 0x1000
CMD_NAME = 0x1002
CMD_INFO = 0x1003
CMD_RATES = 0x1100
CMD_CLOCK = 0x1200
CMD_SUBSCRIPTIONS = 0x3000
CMD_SET_SUBSCRIPTION = 0x3010
MORE_PAGES = 0x8112
PAGE = 16                      # receive channels a device answers per page
RX_RECORD = 20
STRINGS_AT = 172               # a write's strings start here
MAX_CHANNELS = 512
SUB_NONE, SUB_OK = 0, 1       # any other status is a subscription that does not resolve
WAIT = 0.6


def request(command: int, sequence: int, payload: bytes = b"") -> bytes:
    return struct.pack(">HHHHH", PROTOCOL, HEADER + len(payload), sequence & 0xFFFF, command, 0) + payload


def reply(command: int, sequence: int, payload: bytes, result: int = 1, protocol: int = PROTOCOL) -> bytes:
    """What a device answers (the fake device in tests builds its answers with this)."""
    return struct.pack(">HHHHH", protocol, HEADER + len(payload), sequence & 0xFFFF, command, result) + payload


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


SAMPLE_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
RATE_TAG = 0x8020              # in the 0x1100 table: points at the u32 sample rate


def parse_counts(payload: bytes | None) -> dict | None:
    """Transmit and receive channel counts from 0x1000 (checked on a DT168: 16 and 8)."""
    if payload is None or len(payload) < 6:
        return None
    tx, rx = struct.unpack(">HH", payload[2:6])
    return {"tx": tx, "rx": rx} if tx <= MAX_CHANNELS and rx <= MAX_CHANNELS else None


def parse_name(payload: bytes | None) -> str | None:
    """The device's Dante name from 0x1002: a NUL-terminated string."""
    if not payload:
        return None
    end = payload.find(b"\0")
    name = payload[:end if end >= 0 else None].decode("utf-8", "replace")
    return name or None


def parse_info(payload: bytes | None) -> dict | None:
    """Board, Dante name and hostname from 0x1003: string pointers at payload bytes 6, 12 and 14 (packet offsets, as
    in the receive list). Checked on a DT168: "Audinate DCM", its Dante name, and "DT168-xxxxxx"."""
    if payload is None or len(payload) < 16:
        return None
    board, name, host = struct.unpack(">HHH", payload[6:8] + payload[12:14] + payload[14:16])
    out = {"board": _text(payload, board), "name": _text(payload, name), "hostname": _text(payload, host)}
    return out if any(out.values()) else None


def parse_sample_rate(payload: bytes | None) -> int | None:
    """The sample rate from 0x1100: a table of (tag u16, pointer u16) pairs from payload byte 2, where tag 0x8020
    points at a u32. Only the rates Dante runs at are accepted, so a layout that differs shows "no reading"."""
    if payload is None:
        return None
    for at in range(2, len(payload) - 3, 4):
        tag, pointer = struct.unpack(">HH", payload[at:at + 4])
        if tag == RATE_TAG:
            where = pointer - HEADER
            if 0 <= where and where + 4 <= len(payload):
                rate = struct.unpack(">I", payload[where:where + 4])[0]
                return rate if rate in SAMPLE_RATES else None
            return None
    return None


def read_info(ip: str, port: int, sequence: int = 10, wait: float = WAIT) -> dict:
    """What a device says about itself: counts, name, board, hostname and sample rate. A request that gets no
    readable answer leaves its fields out."""
    out: dict = {}
    for i, (command, parse) in enumerate(((CMD_COUNTS, parse_counts), (CMD_INFO, parse_info), (CMD_RATES, parse_sample_rate))):
        got = parse(ask(ip, port, command, sequence + i, wait))
        if isinstance(got, dict):
            out.update(got)
        elif got is not None:
            out["sample_rate"] = got
    return out


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
    channel of the device itself), byte for byte what Dante Controller sends. An empty `source` clears the
    subscription. None when it cannot be written."""
    if not 1 <= channel <= 255:
        return None
    chan_b = dev_b = b""
    if source:
        chan, _, dev = source.rpartition("@") if "@" in source else (source, "", ".")
        if not chan or not dev:
            return None
        chan_b, dev_b = chan.encode("utf-8") + b"\0", dev.encode("utf-8") + b"\0"
    chan_at = STRINGS_AT if source else 0
    dev_at = STRINGS_AT + len(chan_b) if source else 0
    head = struct.pack(">HHHHHBBBBHH", PROTOCOL_WRITE, STRINGS_AT + len(chan_b) + len(dev_b), sequence & 0xFFFF,
                       CMD_SET_SUBSCRIPTION, 0, 0x08, 1, 0, channel, chan_at, dev_at)
    return head + b"\0" * (STRINGS_AT - len(head)) + chan_b + dev_b


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
            if proto in REPLY_PROTOCOLS and cmd == command and seq == sequence & 0xFFFF and length == len(got):
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
    got = exchange(ip, port, data, CMD_SET_SUBSCRIPTION, sequence, wait)
    if got is None:
        return "silent", "The device did not answer the change."
    if got[0] in (1, MORE_PAGES):
        return "ok", ""
    return "refused", ("The device refused the change. On a network under Dante Domain Manager, changes are refused "
                       "unless Relay is given its credentials, which Relay does not support yet.")
