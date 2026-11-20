"""Dante heartbeat: the packets a device multicasts about once a second, read for levels and clock error.
Run: .venv/bin/python tests/test_dante_heartbeat.py"""
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.dante import heartbeat as hb  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

ID = bytes.fromhex("001dc1fffe19862a")

with section("levels are read from the signal presence scale"):
    eq("0 is clipping", hb.level(0), 0)
    eq("2 is -1 dBFS", hb.level(2), -1)
    eq("40 is -20 dBFS", hb.level(40), -20)
    eq("252 is -126 dBFS", hb.level(252), -126)
    eq("253 and up is silence", (hb.level(253), hb.level(255)), (-127, -127))

with section("a packet is read, and what does not fit is no reading"):
    got = hb.parse(hb.packet(ID, tx=[2, 40, 255], rx=[9, 9], offset_ppb=-120))
    eq("identity", got["id"], "001dc1fffe19862a")
    eq("transmit and receive levels", (got["presence"]["tx"], got["presence"]["rx"]), ([2, 40, 255], [9, 9]))
    eq("clock error in parts per billion", got["offset_ppb"], -120)
    eq("a packet with only a clock error has no levels", hb.parse(hb.packet(ID, offset_ppb=7))["presence"], None)
    good = hb.packet(ID, tx=[1, 2, 3])
    eq("a wrong protocol", hb.parse(b"\x00\x00" + good[2:]), None)
    eq("a wrong length", hb.parse(good[:-4]), None)
    eq("an all-zero identity", hb.parse(hb.packet(b"", tx=[1])), None)
    eq("an all-ones identity", hb.parse(hb.packet(b"\xff" * 8, tx=[1])), None)
    eq("a record that runs off the end", hb.parse(good[:2] + struct.pack(">H", len(good) - 4) + good[4:-4]), None)
    eq("a level count that disagrees with the record has no levels", hb.parse(good[:0x20 + 12] + struct.pack(">H", 9) + good[0x20 + 14:])["presence"], None)
    eq("a short packet", hb.parse(b"\xff\xfe"), None)

with section("records captured from a real Dante Virtual Soundcard are read"):
    # Real 0x8001 and 0x8002 records (2026-11-20); only the sender's identity is replaced.
    real_clock = bytes.fromhex("003480010004002855310000fffff170000000ff0001570500000007000007fd0015253500000020000000000000000f00000000")
    real_presence = bytes.fromhex("001c800200040010d7560000000200000002000000180000c1c1ffff")
    body = real_clock + real_presence
    packet = struct.pack(">HH", 0xFFFE, 32 + len(body)) + bytes.fromhex("0f100000") + bytes.fromhex("001122fffe334455") + bytes.fromhex("417564696e6174650008000110000000") + body
    got = hb.parse(packet)
    eq("the clock error is -3728 ppb", got["offset_ppb"], -3728)
    eq("two transmit and two receive channels", (got["presence"]["tx"], got["presence"]["rx"]), ([193, 193], [255, 255]))
    eq("a transmit byte of 193 is about -96 dBFS, and 255 is silence", (hb.level(193), hb.level(255)), (-96, -127))

with section("the latest heartbeat of each sender is kept for a few seconds"):
    beats = hb.Heartbeats()
    check("a heartbeat is kept", beats.feed(hb.packet(ID, tx=[2, 40], offset_ppb=15), "10.0.0.5", now=100.0))
    check("junk is not", not beats.feed(b"junk", "10.0.0.5", now=100.0))
    eq("levels by channel", beats.levels(["10.0.0.5"], now=101.0), [-1, -20])
    eq("the clock error", beats.offset_ppb(["10.0.0.5"], now=101.0), 15)
    eq("another address has none", beats.levels(["10.0.0.6"], now=101.0), None)
    eq("a stale heartbeat is no reading", beats.levels(["10.0.0.5"], now=100.0 + hb.MAX_AGE + 1), None)
    beats.feed(hb.packet(ID, tx=[2], tx_first=2), "10.0.0.7", now=100.0)
    eq("channels before the first are unknown", beats.levels(["10.0.0.7"], now=100.5), [None, None, -1])

with section("a device multicasting to a real socket is heard"):
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    port = rx.getsockname()[1]
    live = hb.Heartbeats()
    live.listen(rx)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.sendto(b"not a heartbeat", ("127.0.0.1", port))
    tx.sendto(hb.packet(ID, tx=[4, 6]), ("127.0.0.1", port))
    check("the heartbeat arrives", wait_for(lambda: live.levels(["127.0.0.1"]) is not None))
    eq("read off the wire", live.levels(["127.0.0.1"]), [-2, -3])
    tx.close()
    live.stop()
    eq("stopping forgets it", live.levels(["127.0.0.1"]), None)

done()
