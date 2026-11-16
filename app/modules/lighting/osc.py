"""A minimal OSC 1.0 message encoder for the lighting backends that speak OSC (Vista, Lightkey): string and float32
arguments, over UDP. Part of the lighting module, so no other module reaches into it."""
from __future__ import annotations

import struct


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)


def message(address: str, *args: str | float) -> bytes:
    """An OSC message. A `str` argument is an OSC string (`s`), a `float` is a 32-bit float (`f`)."""
    tags = "".join("s" if isinstance(a, str) else "f" for a in args)
    out = _pad(address.encode("ascii")) + _pad(("," + tags).encode("ascii"))
    for a in args:
        out += _pad(a.encode("utf-8")) if isinstance(a, str) else struct.pack(">f", a)
    return out
