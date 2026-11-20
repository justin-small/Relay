"""The shared part of Blackmagic's two text protocols over TCP (HyperDeck on 9993, Videohub on 9990).

A client keeps one connection to the device, reconnects with a growing wait when it drops or goes quiet, asks the
device something now and then so a dead link is noticed, and hands every chunk it reads to the subclass. The
connection goes through `netsock.open_tcp` on the module's traffic class, so it follows the network port the operator
chose (#348). What a device says is kept in memory only; the admin page shows it, never an exception's text.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ... import netsock

log = logging.getLogger("relay.blackmagic")

TRAFFIC = "blackmagic"
QUIET_S = 30.0               # nothing heard for this long: the link is dead, start again
CONNECT_S = 5.0
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_BUFFER = 1 << 20         # a device that never ends a message is cut off, not stored
READ_S = 1.0


class TextClient:
    """One device's connection. Subclasses set `NAME`, `PING_S` and override `opened`, `consume` and `tick`."""

    NAME = "device"
    PING_S = 10.0

    def __init__(self, host: str, port: int, on_change: Callable[[], None] | None = None) -> None:
        self.host, self.port = host, int(port)
        self.on_change = on_change or (lambda: None)
        self.connected = False
        self.error = ""
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._heard = time.monotonic()
        self._buf = b""

    # ----------------------------------------------------------------------------------------- run
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name=f"blackmagic-{self.NAME}")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._drop()

    def _drop(self) -> None:
        was = self.connected
        self.connected = False
        writer, self._writer = self._writer, None
        if writer is not None:
            try:
                writer.close()
            except Exception:  # already closed
                pass
        self._buf = b""
        if was:
            self.on_change()

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except OSError:
                # A fixed sentence naming the chosen port (#348), never the exception's text.
                self.error = netsock.failure(TRAFFIC, f"Could not reach the {self.NAME}")
            except Exception:  # a bad message must not end the loop
                log.exception("Blackmagic %s session failed", self.NAME)
                self.error = "Lost the connection"
            self._drop()
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def _session(self) -> None:
        self._reset()
        reader, writer = await netsock.open_tcp(TRAFFIC, self.host, self.port, CONNECT_S)
        self._writer = writer
        self._heard = time.monotonic()
        last_ping = self._heard
        self.opened()
        while not self._stop.is_set():
            try:
                data = await asyncio.wait_for(reader.read(8192), READ_S)
            except asyncio.TimeoutError:
                data = None
            now = time.monotonic()
            if data == b"":
                self.error = "The " + self.NAME + " closed the connection"
                return
            if data:
                self._heard = now
                self._buf += data
                if len(self._buf) > MAX_BUFFER:
                    self.error = "The " + self.NAME + " sent something Relay could not read"
                    return
                self._buf = self.consume(self._buf)
            if now - self._heard > QUIET_S:
                self.error = f"The {self.NAME} stopped answering" if self.connected \
                    else f"No answer from the {self.NAME}"
                return
            if self.connected and now - last_ping >= self.PING_S:
                last_ping = now
                self.ping()
            self.tick(now)

    # ----------------------------------------------------------------------------------------- for subclasses
    def _reset(self) -> None:
        """Forget what the last connection said."""

    def opened(self) -> None:
        """The socket is open; the device is about to talk."""

    def consume(self, buf: bytes) -> bytes:
        """Take whole messages off the front of `buf`; return what is left (an unfinished message)."""
        return b""

    def ping(self) -> None:
        """Something harmless, so a dead link is noticed."""

    def tick(self, now: float) -> None:
        """Called about once a second while the socket is open."""

    # ----------------------------------------------------------------------------------------- sending
    def send_text(self, text: str) -> bool:
        """Send text now. False when there is no open connection to send on."""
        writer = self._writer
        if writer is None or not self.connected:
            return False
        try:
            writer.write(text.encode("utf-8", "replace"))
        except Exception:
            return False
        return True
