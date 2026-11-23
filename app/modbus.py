"""Modbus client for modules (#629): Modbus TCP and Modbus RTU over a serialio.Link.

    link = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "10.10.0.50", "port": 502}), traffic="mymodule")
    await link.start()
    modbus = ModbusClient(link, framing="tcp", unit=1)           # RTU over a serial port or a serial-over-TCP link:
    regs = await modbus.read_holding_registers(0, 10)            # ModbusClient(link, framing="rtu")
    await modbus.write_coil(3, True)

The client does the framing and nothing else: the Link opens the socket or the port (through netsock, so traffic stays
on the network port the operator chose), keeps it up, and lets one request at a time use the line. Written here, not
taken from pymodbus, because pymodbus opens its own sockets, which would bypass that routing (#345).

Functions: read coils (1), read discrete inputs (2), read holding registers (3), read input registers (4), write one
coil (5), write one register (6), write several coils (15) and several registers (16). Addresses are the protocol's,
from 0. Every failure is a ModbusError with a fixed sentence.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Iterable

from . import serialio

log = logging.getLogger("relay.modbus")

FRAMINGS = ("tcp", "rtu")
READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT = 1, 2, 3, 4
WRITE_COIL, WRITE_REGISTER, WRITE_COILS, WRITE_REGISTERS = 5, 6, 15, 16
MAX_BITS = 2000               # a read of coils or discrete inputs
MAX_REGISTERS = 125           # a read of registers
MAX_WRITE_BITS = 1968
MAX_WRITE_REGISTERS = 123

EXCEPTIONS = {
    1: "The device does not support that function.",
    2: "The device does not have that address.",
    3: "The device refused that value.",
    4: "The device failed while doing it.",
    5: "The device accepted the request and is still working on it.",
    6: "The device is busy.",
    8: "The device had a memory error.",
    10: "The gateway has no path to the device.",
    11: "The device behind the gateway did not answer.",
}


class ModbusError(OSError):
    """A Modbus request that failed. The message is a fixed sentence; `code` is the device's exception code, if it sent
    one."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


def crc16(data: bytes) -> int:
    """The Modbus RTU CRC of `data` (appended low byte first)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def clean_modbus(raw: object) -> dict:
    """Modbus settings from what an operator typed (`framing`, `unit`, `timeout`, `retries`), or serialio.SettingsError."""
    raw = raw if isinstance(raw, dict) else {}
    framing = str(raw.get("framing") or "tcp").lower()
    if framing not in FRAMINGS:
        raise serialio.SettingsError("Modbus framing is tcp or rtu.")
    try:
        unit = int(raw.get("unit", 1))
        timeout = float(raw.get("timeout", 1.0))
        retries = int(raw.get("retries", 1))
    except (TypeError, ValueError):
        raise serialio.SettingsError("Unit, timeout and retries must be numbers.") from None
    if not 0 <= unit <= 255:
        raise serialio.SettingsError("The unit id is from 0 to 255.")
    if not 0.05 <= timeout <= 30:
        raise serialio.SettingsError("The timeout is from 0.05 to 30 seconds.")
    if not 0 <= retries <= 5:
        raise serialio.SettingsError("Retries are from 0 to 5.")
    return {"framing": framing, "unit": unit, "timeout": timeout, "retries": retries}


def _address(address: int, count: int = 1) -> None:
    if not isinstance(address, int) or isinstance(address, bool) or not 0 <= address <= 0xFFFF:
        raise ModbusError("A Modbus address is from 0 to 65535.")
    if address + count - 1 > 0xFFFF:
        raise ModbusError("That runs past the last Modbus address.")


def _count(count: int, most: int) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= most:
        raise ModbusError(f"Ask for 1 to {most} at a time.")


def _pack_bits(bits: list[bool]) -> bytes:
    out = bytearray((len(bits) + 7) // 8)
    for i, bit in enumerate(bits):
        if bit:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def _unpack_bits(data: bytes, count: int) -> list[bool]:
    return [bool(data[i // 8] >> (i % 8) & 1) for i in range(count)]


class ModbusClient:
    """Modbus over one Link. `framing` is "tcp" (a TCP header, no CRC) or "rtu" (a CRC, as on a serial line, including
    over a serial-over-TCP converter). `unit` is the device's id; pass `unit=` to a call to talk to another device on the
    same line. A request that times out or comes back damaged is sent again `retries` times."""

    def __init__(self, link: serialio.Link, *, framing: str = "tcp", unit: int = 1, timeout: float = 1.0,
                 retries: int = 1, gap: float = 0.005):
        if framing not in FRAMINGS:
            raise ValueError("framing is tcp or rtu")
        self.link = link
        self.framing = framing
        self.unit = unit
        self.timeout = timeout
        self.retries = retries
        self.gap = gap                 # quiet time before a request on a serial line (RTU wants 3.5 characters)
        self._tid = 0

    # -- the wire

    def _frame(self, unit: int, pdu: bytes) -> tuple[bytes, int]:
        if self.framing == "tcp":
            self._tid = (self._tid + 1) & 0xFFFF
            return struct.pack(">HHHB", self._tid, 0, len(pdu) + 1, unit) + pdu, self._tid
        body = bytes([unit]) + pdu
        return body + struct.pack("<H", crc16(body)), 0

    async def _read_tcp(self, tid: int, unit: int) -> bytes:
        while True:
            head = await self.link.read_exactly(7, self.timeout)
            rtid, proto, length, runit = struct.unpack(">HHHB", head)
            if proto != 0 or not 2 <= length <= 254:
                self.link.clear()
                raise ModbusError("The device sent something that is not Modbus.")
            pdu = await self.link.read_exactly(length - 1, self.timeout)
            if rtid != tid:
                continue                   # an answer to an earlier request that was given up on
            if runit != unit:
                raise ModbusError("A different device answered.")
            return pdu

    async def _read_rtu(self, unit: int) -> bytes:
        first = await self.link.read_exactly(2, self.timeout)
        runit, function = first
        if function & 0x80:
            rest = await self.link.read_exactly(3, self.timeout)
        elif function in (READ_COILS, READ_DISCRETE, READ_HOLDING, READ_INPUT):
            count = await self.link.read_exactly(1, self.timeout)
            rest = count + await self.link.read_exactly(count[0] + 2, self.timeout)
        else:
            rest = await self.link.read_exactly(6, self.timeout)
        frame = first + rest
        if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
            self.link.clear()
            raise ModbusError("The reply was damaged on the way.")
        if runit != unit:
            raise ModbusError("A different device answered.")
        return frame[1:-2]

    async def _once(self, unit: int, pdu: bytes) -> bytes:
        async with self.link.exchange():
            self.link.clear()
            if self.gap and self.framing == "rtu":
                await asyncio.sleep(self.gap)
            frame, tid = self._frame(unit, pdu)
            await self.link.send(frame)
            reply = await self._read_tcp(tid, unit) if self.framing == "tcp" else await self._read_rtu(unit)
        if not reply:
            raise ModbusError("The reply was empty.")
        if reply[0] & 0x80:
            code = reply[1] if len(reply) > 1 else 0
            raise ModbusError(EXCEPTIONS.get(code, "The device refused the request."), code)
        if reply[0] != pdu[0]:
            raise ModbusError("The device answered a different question.")
        return reply

    async def _call(self, pdu: bytes, unit: int | None) -> bytes:
        unit = self.unit if unit is None else unit
        last: Exception | None = None
        for _ in range(self.retries + 1):
            try:
                return await self._once(unit, pdu)
            except serialio.LinkError as exc:
                last = exc
                if exc.message in ("Not connected.", "The connection was lost."):
                    break                  # nothing to retry until the link is back
            except ModbusError as exc:
                if exc.code is not None:
                    raise                  # the device said no; asking again will not change its mind
                last = exc
        raise ModbusError(getattr(last, "message", "The request failed."))

    # -- reads

    async def _read_bits(self, function: int, address: int, count: int, unit: int | None) -> list[bool]:
        _address(address, count)
        _count(count, MAX_BITS)
        reply = await self._call(struct.pack(">BHH", function, address, count), unit)
        if len(reply) < 2 or reply[1] != len(reply) - 2 or reply[1] < (count + 7) // 8:
            raise ModbusError("The reply was the wrong size.")
        return _unpack_bits(reply[2:], count)

    async def _read_words(self, function: int, address: int, count: int, unit: int | None) -> list[int]:
        _address(address, count)
        _count(count, MAX_REGISTERS)
        reply = await self._call(struct.pack(">BHH", function, address, count), unit)
        if len(reply) != 2 + count * 2 or reply[1] != count * 2:
            raise ModbusError("The reply was the wrong size.")
        return list(struct.unpack(f">{count}H", reply[2:]))

    async def read_coils(self, address: int, count: int = 1, unit: int | None = None) -> list[bool]:
        return await self._read_bits(READ_COILS, address, count, unit)

    async def read_discrete_inputs(self, address: int, count: int = 1, unit: int | None = None) -> list[bool]:
        return await self._read_bits(READ_DISCRETE, address, count, unit)

    async def read_holding_registers(self, address: int, count: int = 1, unit: int | None = None) -> list[int]:
        return await self._read_words(READ_HOLDING, address, count, unit)

    async def read_input_registers(self, address: int, count: int = 1, unit: int | None = None) -> list[int]:
        return await self._read_words(READ_INPUT, address, count, unit)

    # -- writes

    async def write_coil(self, address: int, on: bool, unit: int | None = None) -> None:
        _address(address)
        reply = await self._call(struct.pack(">BHH", WRITE_COIL, address, 0xFF00 if on else 0), unit)
        if len(reply) != 5 or struct.unpack(">H", reply[1:3])[0] != address:
            raise ModbusError("The reply was the wrong size.")

    async def write_register(self, address: int, value: int, unit: int | None = None) -> None:
        _address(address)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF:
            raise ModbusError("A register holds 0 to 65535.")
        reply = await self._call(struct.pack(">BHH", WRITE_REGISTER, address, value), unit)
        if len(reply) != 5 or struct.unpack(">H", reply[1:3])[0] != address:
            raise ModbusError("The reply was the wrong size.")

    async def write_coils(self, address: int, values: Iterable[bool], unit: int | None = None) -> None:
        bits = [bool(v) for v in values]
        _address(address, max(1, len(bits)))
        _count(len(bits), MAX_WRITE_BITS)
        data = _pack_bits(bits)
        reply = await self._call(struct.pack(">BHHB", WRITE_COILS, address, len(bits), len(data)) + data, unit)
        if len(reply) != 5 or struct.unpack(">HH", reply[1:5]) != (address, len(bits)):
            raise ModbusError("The reply was the wrong size.")

    async def write_registers(self, address: int, values: Iterable[int], unit: int | None = None) -> None:
        words = list(values)
        _address(address, max(1, len(words)))
        _count(len(words), MAX_WRITE_REGISTERS)
        if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 0xFFFF for v in words):
            raise ModbusError("A register holds 0 to 65535.")
        pdu = struct.pack(">BHHB", WRITE_REGISTERS, address, len(words), len(words) * 2) + struct.pack(f">{len(words)}H", *words)
        reply = await self._call(pdu, unit)
        if len(reply) != 5 or struct.unpack(">HH", reply[1:5]) != (address, len(words)):
            raise ModbusError("The reply was the wrong size.")
