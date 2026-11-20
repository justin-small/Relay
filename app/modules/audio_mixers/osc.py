"""OSC 1.0 for the Behringer X32 / Midas M32 and WING drivers (#150).

Arguments: int32 (`i`), float32 (`f`), string (`s`) and blob (`b`, the X32's meter frames), plus the no-data tags
`T`, `F`, `N` and `I` and the array brackets `[` `]` a WING may answer with (an array's items are flattened into the
argument list). A packet that does not parse is dropped whole; nothing here raises on bad input. Part of this module
(modules do not import each other; REAPER and lighting keep their own).
"""
from __future__ import annotations

import struct
from typing import Any

MAX_DEPTH = 4


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)


def message(address: str, *args: str | int | float | bytes) -> bytes:
    """An OSC message. `str` is a string, `int` an int32, `float` a float32, `bytes` a blob (a bool is refused)."""
    tags, body = "", b""
    for a in args:
        if isinstance(a, bool):
            raise TypeError("OSC arguments are str, int, float or bytes")
        if isinstance(a, str):
            tags, body = tags + "s", body + _pad(a.encode("utf-8"))
        elif isinstance(a, int):
            tags, body = tags + "i", body + struct.pack(">i", a)
        elif isinstance(a, float):
            tags, body = tags + "f", body + struct.pack(">f", a)
        elif isinstance(a, bytes):
            blob = struct.pack(">i", len(a)) + a
            tags, body = tags + "b", body + blob + b"\0" * (-len(blob) % 4)
        else:
            raise TypeError("OSC arguments are str, int, float or bytes")
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
        elif t == "b":
            if i + 4 > len(data):
                return None
            n = struct.unpack(">i", data[i:i + 4])[0]
            if n < 0 or i + 4 + n > len(data):
                return None
            v = data[i + 4:i + 4 + n]
            i += 4 + n + (-(4 + n) % 4)
        elif t in "TF":
            v = t == "T"
        elif t in "NI[]":
            continue
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
