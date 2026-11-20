"""SDP and SAP parsing for AES67 streams (#266). Pure: bytes and text in, plain values out, no sockets.

A stream is announced by SAP (RFC 2974) carrying an SDP description (RFC 4566, AES67 profile). Relay reads only what
it needs to receive audio: the multicast group and port, the channel count, the sample rate and the encoding. A stream
it cannot receive (a different encoding, an address that is not multicast, a unicast stream) is still listed, with
the reason, so the operator sees why it cannot be picked.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

MAX_SDP = 4096
ENCODINGS = {"L16": 2, "L24": 3}          # bytes per sample of the linear encodings Relay decodes
RATES = (44100, 48000, 88200, 96000)
MAX_CHANNELS = 64
DANTE_WORDS = ("dante", "audinate")


@dataclass(frozen=True)
class Stream:
    """What one SDP announces. `id` is `<group>:<port>`: it names the stream on the wire and does not change when
    the sender bumps the session version."""
    id: str
    name: str
    group: str
    port: int
    channels: int
    rate: int
    encoding: str
    payload_type: int
    origin: str = ""
    source: str = ""                # the sender's address when the SDP names one (a=source-filter)
    clock: str = ""                 # the a=ts-refclk line, shown as is
    dante: bool = False
    info: str = ""
    labels: tuple[str, ...] = field(default_factory=tuple)   # channel names from the media's i= line, when one per channel
    problem: str = ""               # why Relay cannot receive it, "" when it can

    @property
    def usable(self) -> bool:
        return not self.problem

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "group": self.group, "port": self.port, "channels": self.channels,
                "rate": self.rate, "encoding": self.encoding, "origin": self.origin, "source": self.source,
                "clock": self.clock, "dante": self.dante, "info": self.info, "labels": list(self.labels),
                "problem": self.problem}


def _lines(text: str) -> list[tuple[str, str]]:
    out = []
    for raw in text.replace("\r", "").split("\n"):
        if len(raw) > 2 and raw[1] == "=":
            out.append((raw[0], raw[2:].strip()))
    return out


def _connection(value: str) -> tuple[str, int] | None:
    """`IN IP4 239.69.1.2/32` -> (address, ttl). None for anything but IPv4."""
    parts = value.split()
    if len(parts) < 3 or parts[0] != "IN" or parts[1] != "IP4":
        return None
    addr, _, rest = parts[2].partition("/")
    try:
        ipaddress.IPv4Address(addr)
    except ValueError:
        return None
    return addr, int(rest) if rest.isdigit() else 1


def _rtpmap(value: str) -> tuple[int, str, int, int] | None:
    """`97 L24/48000/2` -> (payload type, encoding, rate, channels)."""
    m = re.fullmatch(r"(\d{1,3})\s+([A-Za-z0-9_-]{1,16})/(\d{4,6})(?:/(\d{1,3}))?", value)
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper(), int(m.group(3)), int(m.group(4) or 1)


def _source_filter(value: str) -> str:
    """`incl IN IP4 239.69.1.2 192.168.1.5` -> the sender's address."""
    parts = value.split()
    if len(parts) >= 5 and parts[0] == "incl" and parts[2] == "IP4":
        try:
            return str(ipaddress.IPv4Address(parts[4]))
        except ValueError:
            return ""
    return ""


def _problem(group: str, ttl_ok: bool, enc: str, rate: int, channels: int) -> str:
    try:
        multicast = ipaddress.IPv4Address(group).is_multicast
    except ValueError:
        multicast = False
    if not multicast:
        return "Unicast stream: Relay receives multicast streams only."
    if enc not in ENCODINGS:
        return f"Encoding {enc} is not supported; Relay decodes L16 and L24."
    if rate not in RATES:
        return f"Sample rate {rate} Hz is not supported."
    if not 1 <= channels <= MAX_CHANNELS:
        return f"{channels} channels is more than Relay reads."
    return ""


def parse(text: str) -> Stream | None:
    """The first audio stream of an SDP, or None when the text is not a usable audio description."""
    if not isinstance(text, str) or len(text) > MAX_SDP or not text.startswith("v=0"):
        return None
    session_name = session_info = origin = ""
    session_conn: tuple[str, int] | None = None
    sess = {"source": "", "clock": "", "keywds": ""}      # attributes above the media line apply to it too
    media: dict | None = None
    for kind, value in _lines(text):
        if kind == "s" and media is None:
            session_name = value
        elif kind == "i":
            if media is None:
                session_info = value
            else:
                media["info"] = value
        elif kind == "o" and media is None:
            origin = value
        elif kind == "m":
            parts = value.split()
            if media is None and len(parts) >= 4 and parts[0] == "audio" and parts[2].startswith("RTP"):
                port = parts[1].split("/")[0]
                if not (port.isdigit() and 0 < int(port) < 65536 and parts[3].isdigit()):
                    return None
                media = {"port": int(port), "pt": int(parts[3]), "conn": None, "map": {}, "source": sess["source"], "clock": sess["clock"],
                         "keywds": sess["keywds"], "info": ""}
            elif media is not None:
                break                                    # a second media section: the first one is the stream
        elif kind == "c":
            conn = _connection(value)
            if media is None:
                session_conn = conn
            else:
                media["conn"] = conn
        elif kind == "a":
            key, _, val = value.partition(":")
            target = media if media is not None else sess
            if key == "rtpmap" and media is not None:
                m = _rtpmap(val)
                if m:
                    media["map"][m[0]] = m[1:]
            elif key == "source-filter":
                target["source"] = _source_filter(val)
            elif key == "ts-refclk":
                target["clock"] = val.strip()[:120]
            elif key == "keywds":
                target["keywds"] = val
    if media is None:
        return None
    conn = media["conn"] or session_conn
    mapped = media["map"].get(media["pt"])
    if conn is None or mapped is None:
        return None
    enc, rate, channels = mapped
    group = conn[0]
    name = session_name[:120] or group
    names = tuple(n.strip()[:60] for n in media["info"].split(",")) if media["info"] else ()
    haystack = " ".join((session_name, session_info, origin, media["keywds"])).lower()
    return Stream(id=f"{group}:{media['port']}", name=name, group=group, port=media["port"], channels=channels,
                  rate=rate, encoding=enc, payload_type=media["pt"], origin=origin[:120], source=media["source"],
                  clock=media["clock"], dante=any(w in haystack for w in DANTE_WORDS), info=session_info[:120],
                  labels=names if len(names) == channels else (),
                  problem=_problem(group, True, enc, rate, channels))


# -- SAP (RFC 2974) -----------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Announcement:
    withdraw: bool          # the sender is deleting the stream
    msg_id: int
    origin: str             # the sender's address as the packet states it
    sdp: str


def parse_sap(packet: bytes) -> Announcement | None:
    """One SAP packet, or None when it is not one Relay can read (version, IPv6, encrypted, compressed, truncated)."""
    if len(packet) < 8:
        return None
    flags = packet[0]
    version, ipv6, withdraw = flags >> 5, bool(flags & 0x10), bool(flags & 0x04)
    if version != 1 or ipv6 or flags & 0x02 or flags & 0x01:        # encrypted or compressed
        return None
    auth_words = packet[1]
    msg_id = int.from_bytes(packet[2:4], "big")
    origin = str(ipaddress.IPv4Address(packet[4:8]))
    body = packet[8 + auth_words * 4:]
    if body.startswith(b"v=0"):
        payload = body
    else:                                                          # an optional "application/sdp\0" type comes first
        end = body.find(b"\0")
        if end < 0 or body[:end] != b"application/sdp":
            return None
        payload = body[end + 1:]
    try:
        text = payload[:MAX_SDP + 1].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return Announcement(withdraw, msg_id, origin, text)
