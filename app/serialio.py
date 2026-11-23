"""Byte links for modules (#629): serial over TCP or UDP, and a local serial port, with framing and reconnect.

A module that talks to a serial device (a Furman sequencer behind an RS-232 to Ethernet converter, a projector's
RS-232 port) never opens a socket or a serial port itself. It builds a `Link` from settings and uses it:

    link = serialio.Link(serialio.clean_link({"kind": "tcp", "host": "10.10.0.181", "port": 4196, "baud": 19200}),
                         traffic="furman")
    await link.start()
    reply = await link.request(b"?ID 0\\r", until=b"\\x04", timeout=1.0)

TCP and UDP open through app/netsock.py, so the traffic stays on the network port the operator chose for the class
(#345); a local port is opened with pyserial. A Link keeps itself connected (backing off between tries), says whether
it is connected and why not (`state()`), and runs one request at a time, because a serial line is half-duplex.

The sentences in `last_error` are fixed, never built from an exception, so they are safe to show on a page.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from typing import Any, Callable, Iterable

import serial

from . import netsock

log = logging.getLogger("relay.serialio")

KINDS = ("tcp", "udp", "serial")
TERMINATORS = {"cr": b"\r", "lf": b"\n", "nul": b"\x00", "crlf": b"\r\n"}
PARITIES = {"none": serial.PARITY_NONE, "even": serial.PARITY_EVEN, "odd": serial.PARITY_ODD,
            "mark": serial.PARITY_MARK, "space": serial.PARITY_SPACE}
FLOWS = ("none", "rtscts", "xonxoff")
BAUDS = (300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200, 230400)
DEFAULT_TRAFFIC = "connections"
BUFFER_MAX = 65536            # bytes kept for a reader that has not asked yet; the oldest go first
CONNECT_TIMEOUT = 5.0
BACKOFF = (0.5, 30.0)         # seconds before the first retry, and the most it grows to


class LinkError(OSError):
    """A link that cannot do what was asked. `message` is a fixed sentence, safe to show an operator."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class SettingsError(ValueError):
    """Settings that cannot make a link, a Modbus client or an MQTT client. `message` is a fixed sentence."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ settings

def _int(raw: Any, key: str, low: int, high: int, default: int | None = None) -> int:
    if raw in (None, "") and default is not None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise SettingsError(f"Enter a whole number for {key}.") from None
    if not low <= value <= high:
        raise SettingsError(f"{key.capitalize()} must be from {low} to {high}.")
    return value


def clean_link(raw: Any) -> dict:
    """Settings for a Link from what an operator typed, or SettingsError. Only the keys of the chosen kind are kept."""
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or "tcp").lower()
    if kind not in KINDS:
        raise SettingsError("Choose serial over TCP, serial over UDP or a local serial port.")
    if kind in ("tcp", "udp"):
        host = " ".join(str(raw.get("host") or "").split())
        if not host or len(host) > 253 or any(c in host for c in "/\\ @"):
            raise SettingsError("Enter the address of the serial server.")
        return {"kind": kind, "host": host, "port": _int(raw.get("port"), "port", 1, 65535)}
    device = " ".join(str(raw.get("device") or "").split())
    if not device or len(device) > 200:
        raise SettingsError("Choose a serial port.")
    parity = str(raw.get("parity") or "none").lower()
    flow = str(raw.get("flow") or "none").lower()
    if parity not in PARITIES:
        raise SettingsError("Parity is none, even, odd, mark or space.")
    if flow not in FLOWS:
        raise SettingsError("Flow control is none, rtscts or xonxoff.")
    stop = raw.get("stopbits")
    stop = 1 if stop in (None, "", 1, "1") else 2 if str(stop) == "2" else None
    if stop is None:
        raise SettingsError("Stop bits are 1 or 2.")
    return {"kind": "serial", "device": device, "baud": _int(raw.get("baud"), "baud rate", 50, 4000000, 9600),
            "databits": _int(raw.get("databits"), "data bits", 5, 8, 8), "parity": parity, "stopbits": stop, "flow": flow}


def list_ports() -> list[dict]:
    """The serial ports this computer has: [{"device", "description"}]."""
    from serial.tools import list_ports as lp

    try:
        found = lp.comports()
    except Exception:  # noqa: BLE001 - a port listing the OS cannot make is an empty list, not an error page
        log.warning("could not list the serial ports")
        return []
    return [{"device": p.device, "description": p.description or ""} for p in sorted(found, key=lambda p: p.device)]


# ------------------------------------------------------------------ framing

def split_frames(buf: bytes, terminators: Iterable[bytes] = (b"\r", b"\n", b"\x00")) -> tuple[list[bytes], bytes]:
    """Cut `buf` into frames ended by any terminator; returns the frames (terminator and empty frames dropped) and
    what is left over. A terminator that is a prefix of a longer one (CR, and CRLF) is taken as the longer one when
    both are there, so a CRLF is one boundary, not two."""
    terms = sorted({t for t in terminators if t}, key=len, reverse=True)
    frames: list[bytes] = []
    start = i = 0
    while i < len(buf):
        hit = next((t for t in terms if buf.startswith(t, i)), None)
        if hit is None:
            i += 1
            continue
        if i > start:
            frames.append(bytes(buf[start:i]))
        i += len(hit)
        start = i
    return frames, bytes(buf[start:])


def _find(buf: bytearray, terminators: tuple[bytes, ...]) -> tuple[int, int] | None:
    """(index, length) of the earliest terminator in buf, the longest one where several start at the same place."""
    best: tuple[int, int] | None = None
    for t in terminators:
        at = buf.find(t)
        if at >= 0 and (best is None or at < best[0] or (at == best[0] and len(t) > best[1])):
            best = (at, len(t))
    return best


# ------------------------------------------------------------------ channels: one open connection each

class _Tcp:
    def __init__(self, settings: dict, traffic: str):
        self.host, self.port, self.traffic = settings["host"], settings["port"], traffic
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def open(self, feed: Callable[[bytes], None]) -> None:
        self.reader, self.writer = await netsock.open_tcp(self.traffic, self.host, self.port, CONNECT_TIMEOUT)

    async def pump(self, feed: Callable[[bytes], None]) -> str:
        """Feed what arrives until the connection ends; returns the sentence for why it ended."""
        assert self.reader is not None
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    return "The serial server closed the connection."
                feed(data)
        except OSError:
            return "The connection to the serial server was lost."

    async def write(self, data: bytes) -> None:
        if self.writer is None:
            raise LinkError("Not connected.")
        try:
            self.writer.write(data)
            await asyncio.wait_for(self.writer.drain(), 5.0)
        except (OSError, asyncio.TimeoutError):
            raise LinkError("Could not send to the serial server.") from None

    async def close(self) -> None:
        w, self.writer, self.reader = self.writer, None, None
        if w is not None:
            w.close()
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                await asyncio.wait_for(w.wait_closed(), 1.0)


class _Udp:
    def __init__(self, settings: dict, traffic: str):
        self.host, self.port, self.traffic = settings["host"], settings["port"], traffic
        self.transport: asyncio.DatagramTransport | None = None
        self._ended: asyncio.Future[str] | None = None

    async def open(self, feed: Callable[[bytes], None]) -> None:
        loop = asyncio.get_running_loop()
        self._ended = loop.create_future()
        ended = self._ended

        class Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: Any, addr: Any) -> None:
                # Windows' proactor loop, on a connected socket it was handed, can pass (data, address) as `data`
                # (netsock.udp_endpoint tells it the peer only after the first read is queued).
                feed(data[0] if isinstance(data, tuple) else data)

            def error_received(self, exc: Exception) -> None:
                if not ended.done():
                    ended.set_result("The serial server could not be reached.")

            def connection_lost(self, exc: Exception | None) -> None:
                if not ended.done():
                    ended.set_result("The UDP socket closed.")

        self.transport, _ = await netsock.udp_endpoint(self.traffic, Proto, remote=(self.host, self.port))

    async def pump(self, feed: Callable[[bytes], None]) -> str:
        assert self._ended is not None
        return await self._ended

    async def write(self, data: bytes) -> None:
        if self.transport is None:
            raise LinkError("Not connected.")
        self.transport.sendto(data)

    async def close(self) -> None:
        t, self.transport = self.transport, None
        if t is not None:
            t.close()


class _Serial:
    """A local serial port. pyserial is blocking, so a thread reads it and hands bytes to the event loop."""

    def __init__(self, settings: dict, traffic: str):
        self.s = settings
        self.port: serial.Serial | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ended: asyncio.Future[str] | None = None

    def _open_port(self) -> serial.Serial:
        s = self.s
        try:
            return serial.Serial(
                port=s["device"], baudrate=s["baud"], bytesize=s["databits"], parity=PARITIES[s["parity"]],
                stopbits=s["stopbits"], xonxoff=s["flow"] == "xonxoff", rtscts=s["flow"] == "rtscts",
                timeout=0.1, write_timeout=2.0)
        except (serial.SerialException, OSError, ValueError):
            raise LinkError(f"Could not open the serial port {s['device']}.") from None

    async def open(self, feed: Callable[[bytes], None]) -> None:
        loop = asyncio.get_running_loop()
        self.port = await asyncio.to_thread(self._open_port)
        self._ended = loop.create_future()
        self._stop.clear()
        port, ended, stop = self.port, self._ended, self._stop

        def read() -> None:
            reason = "The serial port was closed."
            while not stop.is_set():
                try:
                    data = port.read(port.in_waiting or 1)
                except (serial.SerialException, OSError, TypeError, ValueError):
                    reason = "The serial port stopped answering."
                    break
                try:
                    if data:
                        loop.call_soon_threadsafe(feed, data)
                except RuntimeError:           # the event loop has closed
                    return
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(lambda: ended.done() or ended.set_result(reason))

        self._thread = threading.Thread(target=read, name="serialio-read", daemon=True)
        self._thread.start()

    async def pump(self, feed: Callable[[bytes], None]) -> str:
        assert self._ended is not None
        return await self._ended

    async def write(self, data: bytes) -> None:
        port = self.port
        if port is None:
            raise LinkError("Not connected.")
        try:
            await asyncio.to_thread(lambda: (port.write(data), port.flush()))
        except (serial.SerialException, OSError, TypeError, ValueError):
            raise LinkError("Could not write to the serial port.") from None

    async def close(self) -> None:
        self._stop.set()
        port, self.port = self.port, None
        if port is not None:
            with contextlib.suppress(serial.SerialException, OSError):
                await asyncio.to_thread(port.close)
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join, 1.0)


_CHANNELS = {"tcp": _Tcp, "udp": _Udp, "serial": _Serial}


# ------------------------------------------------------------------ the link

class Link:
    """One serial connection a module reads and writes. Start it, use it, close it.

    `settings` come from clean_link(). `traffic` is the module's network traffic class (netsock pins the socket to the
    port chosen for it); a local serial port ignores it. `on_data(chunk)` hears every chunk as it arrives, before a
    reader sees it, for devices that speak unasked. `on_state()` runs when it connects or loses the connection."""

    def __init__(self, settings: dict, *, traffic: str = DEFAULT_TRAFFIC, name: str = "",
                 on_data: Callable[[bytes], None] | None = None, on_state: Callable[[], None] | None = None,
                 backoff: tuple[float, float] = BACKOFF):
        self.settings = settings
        self.traffic = traffic
        self.name = name
        self.on_data = on_data
        self.on_state = on_state
        self._backoff = backoff
        self._chan: Any = None
        self._task: asyncio.Task | None = None
        self._buf = bytearray()
        self._more: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._up: asyncio.Event | None = None
        self.connected = False
        self.last_error = ""
        self.last_error_at = 0.0
        self.since = 0.0              # when it last connected
        self.attempts = 0             # tries since it last connected
        self.bytes_in = 0
        self.bytes_out = 0

    # -- lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        self._more, self._lock, self._up = asyncio.Event(), asyncio.Lock(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"serialio-{self.name or self.settings['kind']}")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._drop()

    def reconnect(self) -> None:
        """Drop the connection and try again now (the network port it uses changed)."""
        if self._task is None:
            return
        self._task.cancel()           # _run drops its channel as it ends
        self._task = asyncio.create_task(self._run(), name=f"serialio-{self.name or self.settings['kind']}")

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        if self.connected:
            return True
        if self._up is None:
            return False
        try:
            await asyncio.wait_for(self._up.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return self.connected

    def state(self) -> dict:
        return {"connected": self.connected, "last_error": self.last_error, "last_error_at": self.last_error_at or None,
                "connected_since": self.since if self.connected else None, "attempts": self.attempts,
                "bytes_in": self.bytes_in, "bytes_out": self.bytes_out}

    # -- the connection loop

    def _changed(self) -> None:
        if self.on_state is not None:
            try:
                self.on_state()
            except Exception:  # noqa: BLE001 - a module's callback must not stop the link
                log.exception("serial link state callback failed")

    def _fail(self, sentence: str) -> None:
        self.last_error, self.last_error_at = sentence, time.time()

    async def _drop(self) -> None:
        chan, self._chan = self._chan, None
        was, self.connected = self.connected, False
        if self._up is not None:
            self._up.clear()
        if chan is not None:
            await chan.close()
        if was:
            self._changed()

    def _feed(self, data: bytes) -> None:
        self.bytes_in += len(data)
        if self.on_data is not None:
            try:
                self.on_data(data)
            except Exception:  # noqa: BLE001
                log.exception("serial link data callback failed")
        self._buf += data
        if len(self._buf) > BUFFER_MAX:
            del self._buf[:len(self._buf) - BUFFER_MAX]
        if self._more is not None:
            self._more.set()

    async def _run(self) -> None:
        low, high = self._backoff
        wait = low
        try:
            while True:
                self.attempts += 1
                chan = _CHANNELS[self.settings["kind"]](self.settings, self.traffic)
                try:
                    await chan.open(self._feed)
                except netsock.RouteError as exc:
                    self._fail(exc.message)        # netsock's sentences are fixed
                except LinkError as exc:
                    self._fail(exc.message)
                except (OSError, asyncio.TimeoutError):
                    self._fail("Could not connect.")
                except KeyError:
                    log.error("serial link %s names a traffic class Relay does not have", self.name)
                    self._fail("This connection uses a kind of traffic Relay does not know.")
                else:
                    self._chan = chan
                    self._buf.clear()
                    self.connected, self.since, self.attempts, self.last_error = True, time.time(), 0, ""
                    wait = low
                    if self._up is not None:
                        self._up.set()
                    self._changed()
                    self._fail(await chan.pump(self._feed))
                    await self._drop()
                await asyncio.sleep(wait)
                wait = min(high, wait * 2)
        finally:
            await self._drop()

    # -- reading and writing

    def clear(self) -> None:
        """Forget what has arrived and not been read."""
        self._buf.clear()

    def exchange(self) -> asyncio.Lock:
        """`async with link.exchange():` holds the line for one request and its reply. request() does this itself."""
        if self._lock is None:
            raise LinkError("The link is not started.")
        return self._lock

    async def send(self, data: bytes) -> None:
        chan = self._chan
        if not self.connected or chan is None:
            raise LinkError("Not connected.")
        await chan.write(data)
        self.bytes_out += len(data)

    async def _read(self, ready: Callable[[], int | None], timeout: float) -> bytes:
        """Wait until `ready()` returns how many bytes of the buffer to take, then take them."""
        assert self._more is not None
        deadline = time.monotonic() + timeout
        while True:
            n = ready()
            if n is not None:
                out = bytes(self._buf[:n])
                del self._buf[:n]
                return out
            if not self.connected:
                raise LinkError("The connection was lost.")
            left = deadline - time.monotonic()
            if left <= 0:
                raise LinkError("The device did not answer in time.")
            self._more.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._more.wait(), left)

    async def read_exactly(self, n: int, timeout: float = 1.0) -> bytes:
        return await self._read(lambda: n if len(self._buf) >= n else None, timeout)

    async def read_until(self, terminators: bytes | Iterable[bytes] = b"\r", timeout: float = 1.0,
                         limit: int = 4096) -> bytes:
        """Everything up to and including the first terminator. `limit` bytes with none is an error."""
        terms = (terminators,) if isinstance(terminators, (bytes, bytearray)) else tuple(terminators)

        def ready() -> int | None:
            hit = _find(self._buf, terms)
            if hit is not None:
                return hit[0] + hit[1]
            if len(self._buf) > limit:
                raise LinkError("The reply had no end.")
            return None

        return await self._read(ready, timeout)

    async def request(self, data: bytes, *, until: bytes | Iterable[bytes] = b"\r", timeout: float = 1.0,
                      limit: int = 4096) -> bytes:
        """Send `data` and wait for a reply ended by `until`. One request is in flight at a time: another waits its turn
        (the line is half-duplex). Anything unread from before is dropped first."""
        async with self.exchange():
            self.clear()
            await self.send(data)
            return await self.read_until(until, timeout, limit)
