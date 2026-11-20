"""The Blackmagic ATEM network protocol (#243): UDP packets on port 9910, and the commands inside them.

Written from the open description in atem-connection, the library the Bitfocus Companion ATEM module uses
(MIT, https://github.com/Sofie-Automation/sofie-atem-connection). Pure functions on bytes: no sockets, no
asyncio, so the tests run them without a switcher. Only what Relay uses is here: tally, input names, the
program and preview of each mix effect, macros, and the commands that cut, take and fade.

A packet is a 12-byte header then commands. Byte 0-1: the flags (top 5 bits) and the packet length (low 11
bits). 2-3 the session id the switcher chose, 4-5 the id being acknowledged, 6-7 the id to resend from, 10-11
this packet's id. A command is: length (2 bytes, with its 8-byte header), 2 unused, a 4-letter name, the data.
"""
from __future__ import annotations

import struct

PORT = 9910
HEADER = 12
MAX_ID = 1 << 15

# Packet flags.
ACK_REQUEST = 0x01
NEW_SESSION = 0x02
IS_RETRANSMIT = 0x04
RETRANSMIT_REQUEST = 0x08
ACK_REPLY = 0x10

# The first packet a client sends: "new session".
HELLO = bytes([0x10, 0x14, 0x53, 0xAB, 0, 0, 0, 0, 0, 0x3A, 0, 0, 0x01, 0, 0, 0, 0, 0, 0, 0])

# The first protocol version that sends a keyer index and direction with an auto downstream key (2.29).
V8_0_1 = 0x0002001D

# Input ids 1..40 are the switcher's physical inputs; larger ids are black, colour bars, media, a mix's own output.
MAX_CAMERA_INPUT = 40

MACRO_RUN, MACRO_STOP = 0, 1


class Malformed(Exception):
    """A packet or command that does not parse. The caller drops it."""


def packet(flags: int, session: int, *, ack: int = 0, resend_from: int = 0, packet_id: int = 0,
           payload: bytes = b"") -> bytes:
    length = HEADER + len(payload)
    if length > 0x07FF:
        raise ValueError("packet too long")
    return struct.pack(">HHHHHH", (flags << 11) | length, session, ack, resend_from, 0, packet_id) + payload


def parse_header(data: bytes) -> dict:
    if len(data) < HEADER:
        raise Malformed("short packet")
    first, session, ack, resend_from, _, packet_id = struct.unpack(">HHHHHH", data[:HEADER])
    length = first & 0x07FF
    if length != len(data):
        raise Malformed("length does not match")
    return {"flags": first >> 11, "length": length, "session": session, "ack": ack,
            "resend_from": resend_from, "id": packet_id, "payload": data[HEADER:]}


def command(name: str, data: bytes) -> bytes:
    raw = name.encode("ascii")
    if len(raw) != 4:
        raise ValueError("a command name is four letters")
    return struct.pack(">HH", 8 + len(data), 0) + raw + data


def split_commands(payload: bytes) -> list[tuple[str, bytes]]:
    """The commands in a packet's payload, in order. A bad length ends the list instead of raising."""
    out: list[tuple[str, bytes]] = []
    at = 0
    while at + 8 <= len(payload):
        (length,) = struct.unpack_from(">H", payload, at)
        if length < 8 or at + length > len(payload):
            break
        out.append((payload[at + 4:at + 8].decode("ascii", "replace"), payload[at + 8:at + length]))
        at += length
    return out


def _text(data: bytes, start: int, length: int) -> str:
    raw = data[start:start + length].split(b"\0", 1)[0]
    return raw.decode("utf-8", "replace").strip()


# ----------------------------------------------------------------------------------------------- incoming
def parse_version(data: bytes) -> int:
    if len(data) < 4:
        raise Malformed("_ver")
    return struct.unpack_from(">I", data, 0)[0]


def parse_product(data: bytes) -> str:
    return _text(data, 0, 44)


def parse_topology(data: bytes) -> dict:
    """How many mix effects and downstream keyers the switcher has."""
    if len(data) < 3:
        raise Malformed("_top")
    return {"mix_effects": data[0], "downstream_keyers": data[2]}


def parse_input(data: bytes) -> dict:
    """InPr: an input's id and its long and short names."""
    if len(data) < 26:
        raise Malformed("InPr")
    return {"id": struct.unpack_from(">H", data, 0)[0], "long": _text(data, 2, 20), "short": _text(data, 22, 4)}


def parse_tally(data: bytes) -> dict[int, tuple[bool, bool]]:
    """TlSr: for each source id, (on program, on preview), across every mix effect and key."""
    if len(data) < 2:
        raise Malformed("TlSr")
    count = struct.unpack_from(">H", data, 0)[0]
    if len(data) < 2 + 3 * count:
        raise Malformed("TlSr")
    out: dict[int, tuple[bool, bool]] = {}
    for i in range(count):
        src = struct.unpack_from(">H", data, 2 + 3 * i)[0]
        flags = data[4 + 3 * i]
        out[src] = (bool(flags & 1), bool(flags & 2))
    return out


def parse_me_source(data: bytes) -> tuple[int, int]:
    """PrgI or PrvI: (mix effect, source id)."""
    if len(data) < 4:
        raise Malformed("PrgI")
    return data[0], struct.unpack_from(">H", data, 2)[0]


def parse_macro(data: bytes) -> dict:
    """MPrp: a macro's slot, whether it holds one, and its name."""
    if len(data) < 8:
        raise Malformed("MPrp")
    slot, used = struct.unpack_from(">H", data, 0)[0], bool(data[2])
    name_len = struct.unpack_from(">H", data, 4)[0]
    return {"slot": slot, "used": used, "name": _text(data, 8, name_len) if used else ""}


# ----------------------------------------------------------------------------------------------- outgoing
def cut(me: int) -> bytes:
    return command("DCut", bytes([me, 0, 0, 0]))


def auto(me: int) -> bytes:
    return command("DAut", bytes([me, 0, 0, 0]))


def set_program(me: int, source: int) -> bytes:
    return command("CPgI", struct.pack(">BBH", me, 0, source))


def set_preview(me: int, source: int) -> bytes:
    return command("CPvI", struct.pack(">BBH", me, 0, source))


def fade_to_black(me: int) -> bytes:
    return command("FtbA", bytes([me, 0, 0, 0]))


def downstream_auto(keyer: int, version: int) -> bytes:
    """Auto-mix a downstream key. A newer switcher also wants the direction left to it (a toggle)."""
    if version >= V8_0_1:
        return command("DDsA", bytes([0, keyer, 0, 0]))
    return command("DDsA", bytes([keyer, 0, 0, 0]))


def downstream_on_air(keyer: int, on_air: bool) -> bytes:
    return command("CDsL", bytes([keyer, 1 if on_air else 0, 0, 0]))


def macro_run(slot: int) -> bytes:
    return command("MAct", struct.pack(">HBB", slot, MACRO_RUN, 0))


def macro_stop() -> bytes:
    return command("MAct", struct.pack(">HBB", 0xFFFF, MACRO_STOP, 0))
