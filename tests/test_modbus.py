"""Modbus client (#629): the CRC, the exact bytes on the wire, every function over Modbus TCP, over RTU on a TCP
link (a serial-over-TCP converter) and over RTU on a local serial port (a pty), against a fake Modbus device on
loopback. Run: python tests/test_modbus.py

The fake follows the Modbus Application Protocol spec; no real Modbus device was used here.
"""
import asyncio
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import modbus, serialio  # noqa: E402
from app.modbus import ModbusError  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

FAST = (0.02, 0.05)
SIZE = 100                    # the fake has addresses 0 to 99 of each table


async def fails_with(coro):
    """(sentence, device exception code) of the ModbusError a coroutine raises, or None."""
    try:
        await coro
    except ModbusError as exc:
        return str(exc), exc.code
    return None


class Device:
    """A fake Modbus device with four tables. `feed(bytes)` takes what a client sent and returns the frames to send back,
    in RTU or TCP framing, so the same device sits behind a TCP server and behind a pty."""

    def __init__(self, framing: str, unit: int = 1):
        self.framing, self.unit = framing, unit
        self.coils = [False] * SIZE
        self.discrete = [i % 3 == 0 for i in range(SIZE)]
        self.holding = [i * 10 for i in range(SIZE)]
        self.input = [1000 + i for i in range(SIZE)]
        self.buf = bytearray()
        self.requests: list[bytes] = []         # raw frames as they arrived
        self.drop = 0                           # swallow this many requests
        self.damage = 0                         # answer this many with a bad CRC
        self.stale = 0                          # answer this many twice: an old transaction first, then the right one

    # -- the protocol

    def pdu(self, p: bytes) -> bytes:
        fc = p[0]
        try:
            if fc in (1, 2, 3, 4):
                addr, count = struct.unpack(">HH", p[1:5])
                if addr + count > SIZE:
                    return bytes([fc | 0x80, 2])
                if fc in (1, 2):
                    bits = (self.coils if fc == 1 else self.discrete)[addr:addr + count]
                    data = modbus._pack_bits(bits)
                else:
                    data = struct.pack(f">{count}H", *(self.holding if fc == 3 else self.input)[addr:addr + count])
                return bytes([fc, len(data)]) + data
            if fc == 5:
                addr, value = struct.unpack(">HH", p[1:5])
                if addr >= SIZE:
                    return bytes([fc | 0x80, 2])
                if value not in (0, 0xFF00):
                    return bytes([fc | 0x80, 3])
                self.coils[addr] = value == 0xFF00
                return p[:5]
            if fc == 6:
                addr, value = struct.unpack(">HH", p[1:5])
                if addr >= SIZE:
                    return bytes([fc | 0x80, 2])
                self.holding[addr] = value
                return p[:5]
            if fc == 15:
                addr, count, n = struct.unpack(">HHB", p[1:6])
                if addr + count > SIZE:
                    return bytes([fc | 0x80, 2])
                self.coils[addr:addr + count] = modbus._unpack_bits(p[6:6 + n], count)
                return p[:5]
            if fc == 16:
                addr, count, n = struct.unpack(">HHB", p[1:6])
                if addr + count > SIZE:
                    return bytes([fc | 0x80, 2])
                self.holding[addr:addr + count] = struct.unpack(f">{count}H", p[6:6 + n])
                return p[:5]
        except struct.error:
            return bytes([fc | 0x80, 3])
        return bytes([fc | 0x80, 1])

    def take(self) -> tuple[int, bytes, bytes] | None:
        """One whole request from the buffer: (tid, unit, pdu), or None until more arrives."""
        b = self.buf
        if self.framing == "tcp":
            if len(b) < 7:
                return None
            tid, _proto, length, unit = struct.unpack(">HHHB", bytes(b[:7]))
            if len(b) < 6 + length:
                return None
            pdu, frame = bytes(b[7:6 + length]), bytes(b[:6 + length])
            del b[:6 + length]
            self.requests.append(frame)
            return tid, bytes([unit]), pdu
        if len(b) < 2:
            return None
        fc = b[1]
        if fc in (15, 16):
            if len(b) < 7:
                return None
            total = 7 + b[6] + 2
        else:
            total = 8
        if len(b) < total:
            return None
        frame = bytes(b[:total])
        del b[:total]
        self.requests.append(frame)
        if modbus.crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            return None
        return 0, frame[:1], frame[1:-2]

    def wrap(self, tid: int, unit: bytes, pdu: bytes, bad: bool = False) -> bytes:
        if self.framing == "tcp":
            return struct.pack(">HHH", tid, 0, len(pdu) + 1) + unit + pdu
        body = unit + pdu
        crc = modbus.crc16(body) ^ (0x1234 if bad else 0)
        return body + struct.pack("<H", crc)

    def feed(self, data: bytes) -> list[bytes]:
        self.buf += data
        out: list[bytes] = []
        while (req := self.take()) is not None:
            tid, unit, pdu = req
            if self.drop:
                self.drop -= 1
                continue
            if unit[0] != self.unit:
                continue
            reply = self.pdu(pdu)
            if self.stale and self.framing == "tcp":
                self.stale -= 1
                out.append(self.wrap((tid - 1) & 0xFFFF, unit, reply))
            if self.damage:
                self.damage -= 1
                out.append(self.wrap(tid, unit, reply, bad=True))
                continue
            out.append(self.wrap(tid, unit, reply))
        return out


async def serve(device: Device):
    async def client(reader, writer):
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    return
                for frame in device.feed(data):
                    writer.write(frame)
                await writer.drain()
        except ConnectionError:
            pass

    server = await asyncio.start_server(client, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def link_to(port: int) -> serialio.Link:
    link = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "127.0.0.1", "port": port}), backoff=FAST)
    await link.start()
    assert await link.wait_connected(2)
    return link


with section("CRC and settings"):
    eq("the classic CRC vector: 01 03 00 00 00 0A is C5 CD", modbus.crc16(bytes.fromhex("01030000000A")), 0xCDC5)
    eq("another: 01 04 02 FF FF is B8 80", modbus.crc16(bytes.fromhex("010402FFFF")), 0x80B8)
    eq("bits pack LSB first", modbus._pack_bits([True, False, True, True, False, False, False, False, True]),
       b"\x0d\x01")
    eq("and unpack", modbus._unpack_bits(b"\x0d\x01", 9), [True, False, True, True] + [False] * 4 + [True])
    eq("defaults", modbus.clean_modbus({}), {"framing": "tcp", "unit": 1, "timeout": 1.0, "retries": 1})
    eq("rtu, a unit and retries", modbus.clean_modbus({"framing": "RTU", "unit": "7", "retries": 0, "timeout": "2"}),
       {"framing": "rtu", "unit": 7, "timeout": 2.0, "retries": 0})
    for bad in ({"framing": "ascii"}, {"unit": 300}, {"unit": "x"}, {"timeout": 0}, {"retries": 9}):
        try:
            modbus.clean_modbus(bad)
            check(f"{bad} is refused", False)
        except serialio.SettingsError as exc:
            check(f"{bad} is refused with a sentence", str(exc))


async def run_framing(framing: str) -> None:
    dev = Device(framing)
    server, port = await serve(dev)
    link = await link_to(port)
    mb = modbus.ModbusClient(link, framing=framing, unit=1, timeout=0.5, retries=1, gap=0)
    label = "Modbus TCP" if framing == "tcp" else "Modbus RTU over a TCP link"

    with section(f"{label}: bytes on the wire"):
        await mb.read_holding_registers(0, 10)
        want = "000100000006010300000 00A".replace(" ", "") if framing == "tcp" else "01030000000AC5CD"
        eq("the read-holding request is the textbook frame", dev.requests[-1].hex().upper(), want.upper())

    with section(f"{label}: reads"):
        eq("holding registers", await mb.read_holding_registers(5, 4), [50, 60, 70, 80])
        eq("input registers", await mb.read_input_registers(0, 3), [1000, 1001, 1002])
        eq("discrete inputs", await mb.read_discrete_inputs(0, 7), [True, False, False, True, False, False, True])
        eq("coils start off", await mb.read_coils(0, 10), [False] * 10)
        eq("the whole table", len(await mb.read_holding_registers(0, 100)), 100)

    with section(f"{label}: writes"):
        await mb.write_coil(3, True)
        eq("one coil on", dev.coils[3], True)
        eq("and read back", (await mb.read_coils(2, 3)), [False, True, False])
        await mb.write_coil(3, False)
        eq("one coil off", dev.coils[3], False)
        await mb.write_register(7, 4660)
        eq("one register", dev.holding[7], 4660)
        await mb.write_coils(10, [True, False, True, True, False, False, False, False, True, True])
        eq("several coils, across a byte", dev.coils[10:20], [True, False, True, True, False, False, False, False, True, True])
        await mb.write_registers(20, [1, 2, 65535])
        eq("several registers", dev.holding[20:23], [1, 2, 65535])
        eq("read back the registers", await mb.read_holding_registers(20, 3), [1, 2, 65535])

    with section(f"{label}: the device says no"):
        eq("an address it does not have", await fails_with(mb.read_holding_registers(98, 5)),
           ("The device does not have that address.", 2))
        eq("a write to one", await fails_with(mb.write_register(200, 1)),
           ("The device does not have that address.", 2))
        sent = len(dev.requests)
        await fails_with(mb.read_coils(99, 5))
        eq("a device exception is not retried: one request", len(dev.requests) - sent, 1)

    with section(f"{label}: what the client refuses before sending"):
        sent = len(dev.requests)
        for what, coro in (("address -1", mb.read_coils(-1, 1)), ("address 65536", mb.read_coils(65536, 1)),
                           ("zero registers", mb.read_holding_registers(0, 0)),
                           ("126 registers", mb.read_holding_registers(0, 126)),
                           ("2001 coils", mb.read_coils(0, 2001)),
                           ("a read past the end of the address space", mb.read_holding_registers(65535, 2)),
                           ("a register of 70000", mb.write_register(0, 70000)),
                           ("124 registers written", mb.write_registers(0, [0] * 124)),
                           ("a bool as an address", mb.read_coils(True, 1))):
            check(f"{what} is refused", await fails_with(coro))
        eq("and none of them was sent", len(dev.requests), sent)

    with section(f"{label}: retries"):
        dev.drop = 1
        eq("a request that goes unanswered once is sent again", await mb.read_holding_registers(1, 1), [10])
        dev.drop = 2
        eq("two unanswered is one too many", await fails_with(mb.read_holding_registers(1, 1)),
           ("The device did not answer in time.", None))
        eq("and the next one works", await mb.read_holding_registers(1, 1), [10])
        if framing == "rtu":
            dev.damage = 1
            eq("a damaged reply is sent for again", await mb.read_holding_registers(2, 1), [20])
            dev.damage = 2
            eq("two damaged replies fail with a sentence", await fails_with(mb.read_holding_registers(2, 1)),
               ("The reply was damaged on the way.", None))
        else:
            dev.stale = 1
            eq("an answer to an old transaction is skipped", await mb.read_holding_registers(3, 1), [30])
        eq("a different unit id sees nothing", await fails_with(mb.read_holding_registers(0, 1, unit=9)),
           ("The device did not answer in time.", None))

    await link.close()
    with section(f"{label}: no link"):
        eq("a request on a closed link says so", await fails_with(mb.read_coils(0, 1)),
           ("Not connected.", None))
    server.close()
    await server.wait_closed()


async def serial_rtu() -> None:
    with section("Modbus RTU over a local serial port (a pty)"):
        if sys.platform == "win32":
            print("  (skipped: no pty on Windows)")
            return
        import tty

        master, slave = os.openpty()
        tty.setraw(slave)
        dev = Device("rtu")
        loop = asyncio.get_running_loop()

        def device() -> None:
            try:
                data = os.read(master, 512)
            except OSError:
                return
            for frame in dev.feed(data):
                os.write(master, frame)

        os.set_blocking(master, False)
        loop.add_reader(master, device)
        link = serialio.Link(serialio.clean_link({"kind": "serial", "device": os.ttyname(slave), "baud": 19200}),
                             backoff=FAST)
        await link.start()
        check("the port opens", await link.wait_connected(3), link.state())
        mb = modbus.ModbusClient(link, framing="rtu", unit=1, timeout=1.0, retries=1)
        eq("read holding registers", await mb.read_holding_registers(0, 3), [0, 10, 20])
        await mb.write_register(5, 99)
        eq("write a register", dev.holding[5], 99)
        await mb.write_coils(0, [True, True, False])
        eq("write coils", await mb.read_coils(0, 3), [True, True, False])
        await link.close()
        loop.remove_reader(master)
        os.close(master)
        os.close(slave)


async def main() -> None:
    await run_framing("tcp")
    await run_framing("rtu")
    await serial_rtu()


asyncio.run(main())
done()
