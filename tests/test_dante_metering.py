"""Dante metering for hardware boxes: the 8800 request that makes a device push its meters, and the pushed packets.
Run: .venv/bin/python tests/test_dante_metering.py"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.dante import metering as m  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

MAC = bytes.fromhex("00155d0a8b1d")

with section("levels are read from the detailed scale"):
    eq("0 is clipping", m.level(0), 0)
    eq("1 is 0 dBFS", m.level(1), 0)
    eq("0x9C (156) is about -77 dBFS", m.level(0x9C), -78)
    eq("254 is muted", m.level(254), m.MUTED)
    eq("255 is a framing marker, no reading", m.level(255), None)

with section("the request matches the one a real stage box accepted"):
    # Sent on 2026-11-21 from a computer on the church's Dante network to a real DT168 (port 8800): the box answered
    # with its own copy (result 1) and pushed meters at once. Only the device name and addresses are changed here.
    real = bytes.fromhex("1200004c000130100000000000155d0a8b1d00000004001a0002001c000a"
                         "5374616765426f782d312d546f7000000001000100200001222f00010000c0a8fdfd222f000000000000222f0000")
    eq("StageBox-1-Top to 192.168.253.253:8751", m.request("StageBox-1-Top", "192.168.253.253", MAC, 8751, 1), real)
    check("the length field is the packet length", int.from_bytes(real[2:4], "big") == len(real))
    off = m.stop("StageBox-1-Top", MAC)
    eq("stop is the same size with the destination cleared", (len(off), off[-16:]), (len(real), b"\0" * 16))

with section("packets captured from real devices are read"):
    # Real pushes (2026-11-21). A DT168 stage box: version 3, 16 transmit and 8 receive levels (identity replaced).
    box = bytes.fromhex("FFFF003641D800000004C408450A0000417564696E6174650300001000089CB4CCB0AEAFBAC09995C8B9C7C8D2C7FEFEFEFEFEFEFEFE")
    got = m.parse(box)
    eq("sequence and identity", (got["sequence"], got["id"]), (0x41D8, "0004c408450a0000"))
    eq("16 transmit levels", len(got["tx"]), 16)
    eq("the first transmit channel", got["tx"][0], -78)
    eq("unused receive channels are muted", got["rx"], [m.MUTED] * 8)
    # Another device: version 2, 64 transmit and 64 receive levels, 156 bytes (identity replaced).
    v2 = bytes.fromhex("FFFF009CB02900000102030405060000417564696E6174650240408E8CFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEA1B3C4C0ADAC9B97B7C1CEA0CCFED9C6D0FEFEFEFEFEFEFEFEFEFED1FEFED2C6C7C6CBCDC7C7A7FEA1A0D9C6C7FEFEFED9B2C1D1C7C9D7C9D6FEFEA7FEFEA6C600")
    got = m.parse(v2)
    eq("version 2: 64 and 64", (len(v2), len(got["tx"]), len(got["rx"])), (156, 64, 64))
    eq("version 2: the first receive level", got["rx"][0], -80)

with section("what does not fit is no reading"):
    eq("a wrong marker", m.parse(box[:16] + b"Audinatf" + box[24:]), None)
    eq("a wrong length", m.parse(box[:-1]), None)
    eq("an unknown version", m.parse(box[:24] + b"\x09" + box[25:]), None)
    eq("a count that runs off the end", m.parse(box[:26] + b"\x01\x00" + box[28:]), None)
    eq("a short packet", m.parse(b"\xff\xff"), None)
    eq("built and read back", m.parse(m.frame(b"\x01" * 8, 7, [1, 3, 255], [254], version=3))["tx"], [0, -1, None])

with section("a device pushes meters after one request and they are kept by address"):
    box_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    box_sock.bind(("127.0.0.1", 0))
    box_sock.settimeout(2)
    mine = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    mine.bind(("127.0.0.1", 0))
    meters = m.Meters()
    meters.listen(mine, MAC)
    port = box_sock.getsockname()[1]               # the fake box listens where the request goes
    check("the first request goes out", meters.ask("Test-Box", ["127.0.0.1"], now=100.0, port=port))
    check("not again within the renewal time", not meters.ask("Test-Box", ["127.0.0.1"], now=101.0, port=port))
    data, _ = box_sock.recvfrom(2048)
    eq("the box got a request for it", (data[:2], data[6:8], b"Test-Box" in data), (b"\x12\x00", b"\x30\x10", True))
    check("our address and port are in it", bytes([127, 0, 0, 1]) + mine.getsockname()[1].to_bytes(2, "big") in data)
    box_sock.sendto(m.frame(b"\x05" * 8, 1, [1, 3, 255], [2], version=3), mine.getsockname())
    wait_for(lambda: meters.levels(["127.0.0.1"]) is not None)
    eq("levels", meters.levels(["127.0.0.1"]), [0, -1, None])
    eq("another address has none", meters.levels(["10.9.9.9"]), None)
    meters.feed(m.frame(b"\x05" * 8, 2, [1]), "10.0.0.7", now=50.0)
    eq("a push that has gone quiet is no reading", meters.levels(["10.0.0.7"], now=50.0 + m.MAX_AGE + 1), None)
    check("it asks again after the renewal time", meters.ask("Test-Box", ["127.0.0.1"], now=104.0, port=port))
    box_sock.recvfrom(2048)
    meters.release(port)
    data, _ = box_sock.recvfrom(2048)
    eq("release tells the box to stop", data[-16:], b"\0" * 16)
    meters.stop()
    box_sock.close()

done()
