"""RTP packets and linear audio payloads for AES67 (#266). Pure: bytes in, numpy blocks out."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HEADER = 12


@dataclass(frozen=True)
class Packet:
    payload_type: int
    seq: int
    timestamp: int
    ssrc: int
    payload: bytes


def parse(data: bytes) -> Packet | None:
    """An RTP packet's header fields and payload, or None for anything that is not RTP version 2."""
    if len(data) <= HEADER or data[0] >> 6 != 2:
        return None
    cc = data[0] & 0x0F
    start = HEADER + 4 * cc
    if data[0] & 0x10:                                  # a header extension
        if len(data) < start + 4:
            return None
        start += 4 + 4 * int.from_bytes(data[start + 2:start + 4], "big")
    end = len(data)
    if data[0] & 0x20 and end > start:                  # padding
        end -= data[-1]
    if end <= start:
        return None
    return Packet(data[1] & 0x7F, int.from_bytes(data[2:4], "big"), int.from_bytes(data[4:8], "big"),
                  int.from_bytes(data[8:12], "big"), data[start:end])


def decode(payload: bytes, encoding: str, channels: int) -> np.ndarray | None:
    """Big-endian L16 or L24 as float32 (frames, channels) in -1..1. None when the payload is not a whole number of
    frames."""
    width = 2 if encoding == "L16" else 3
    frame = width * channels
    if not payload or len(payload) % frame:
        return None
    if width == 2:
        samples = np.frombuffer(payload, dtype=">i2").astype(np.float32) / 32768.0
    else:
        raw = np.frombuffer(payload, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        value = (raw[:, 0] << 16) | (raw[:, 1] << 8) | raw[:, 2]
        value = np.where(value & 0x800000, value - 0x1000000, value)
        samples = value.astype(np.float32) / 8388608.0
    return samples.reshape(-1, channels)


def seq_gap(expected: int, got: int) -> int:
    """How far `got` is ahead of `expected` in 16-bit sequence numbers: 0 in order, positive when packets are
    missing, negative when it is late or a repeat."""
    d = (got - expected) & 0xFFFF
    return d if d < 0x8000 else d - 0x10000
