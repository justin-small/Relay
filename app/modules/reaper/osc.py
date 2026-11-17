"""OSC 1.0 for the REAPER module (#413): encode a message and decode what REAPER sends back (messages and bundles).

Only the argument types REAPER's stock pattern file uses are understood: int32 (`i`), float32 (`f`) and string (`s`).
A packet that does not parse is dropped whole; nothing here raises on bad input. Part of this module, so no other
module reaches into it (the lighting and alert-events modules keep their own encoders).
"""
from __future__ import annotations

import struct
from typing import Any

MAX_DEPTH = 4          # bundles inside bundles; REAPER sends one level


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)


def message(address: str, *args: str | int | float) -> bytes:
    """An OSC message. `str` is an OSC string, `int` an int32, `float` a float32 (a bool is refused)."""
    tags = ""
    body = b""
    for a in args:
        if isinstance(a, bool):
            raise TypeError("OSC arguments are str, int or float")
        if isinstance(a, str):
            tags += "s"
            body += _pad(a.encode("utf-8"))
        elif isinstance(a, int):
            tags += "i"
            body += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        else:
            raise TypeError("OSC arguments are str, int or float")
    return _pad(address.encode("ascii")) + _pad(("," + tags).encode("ascii")) + body


def _string(data: bytes, i: int) -> tuple[str, int]:
    end = data.index(b"\0", i)
    return data[i:end].decode("utf-8", "replace"), (end // 4 + 1) * 4


def _message(data: bytes) -> tuple[str, list[Any]] | None:
    address, i = _string(data, 0)
    if not address.startswith("/"):
        return None
    if i >= len(data):
        return address, []
    tags, i = _string(data, i)
    if not tags.startswith(","):
        return None
    args: list[Any] = []
    for t in tags[1:]:
        if t == "s":
            v, i = _string(data, i)
        elif t in "if":
            if i + 4 > len(data):
                return None
            v = struct.unpack(">i" if t == "i" else ">f", data[i:i + 4])[0]
            i += 4
        else:
            return None
        args.append(v)
    return address, args


def parse(data: bytes, depth: int = 0) -> list[tuple[str, list[Any]]]:
    """Every message in a packet, in order. [] for anything malformed."""
    try:
        if data.startswith(b"#bundle\0"):
            if depth >= MAX_DEPTH:
                return []
            out: list[tuple[str, list[Any]]] = []
            i = 16
            while i + 4 <= len(data):
                n = struct.unpack(">i", data[i:i + 4])[0]
                if n <= 0 or i + 4 + n > len(data):
                    return out
                out += parse(data[i + 4:i + 4 + n], depth + 1)
                i += 4 + n
            return out
        m = _message(data)
        return [m] if m else []
    except (ValueError, struct.error, IndexError):
        return []
