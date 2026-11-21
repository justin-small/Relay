"""Shure wireless receivers over TCP 2202 (QLX-D, ULX-D, SLX-D): UNPROVEN ON HARDWARE (#153).

Written from Shure's published command-string bulletins and the MIT-licensed notes in stoatworks-labs/Dante-BabelBox,
and tested only against a fake receiver (tests/test_wireless_mics.py). No Shure receiver has been available to check it
against. Messages are ASCII between `<` and `>`:

    client  < GET 1 BATT_CHARGE >           ask for one value
    client  < SET 1 METER_RATE 00500 >      push RF and audio meters every 500 ms
    device  < REP 1 BATT_CHARGE 085 >       the answer; also sent unprompted when a value changes
    device  < SAMPLE 1 ALL AX 090 030 >     meters: antenna (AX, XB, XX), RF 0-115 (dBm = n - 128), audio 0-50

Unproven points, to check on a real receiver: whether these keys are spelled as here on every model, whether a receiver
answers `GET` for a channel it does not have, and the meter scale of audio (0-50, taken as a straight 0-100 after x2).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ... import netsock

log = logging.getLogger("relay.wireless_mics")

TRAFFIC = "wireless_mics"
DEFAULT_PORT = 2202
CONNECT_S = 5.0
READ_S = 1.0
QUIET_S = 30.0               # nothing heard for this long: the link is dead
POLL_S = 10.0                # asked something harmless this often so a dead link is noticed
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_BUFFER = 1 << 16
METER_RATE = "00500"
KEYS = ("TX_TYPE", "BATT_CHARGE", "BATT_RUN_TIME", "AUDIO_MUTE", "CHAN_NAME")
RF_FLOOR_DBM, RF_CEIL_DBM = -100, -30      # the range shown as 0-100


def parse(buf: bytes) -> tuple[list[list[str]], bytes]:
    """Whole messages off the front of `buf` as lists of words, and what is left (an unfinished message)."""
    out: list[list[str]] = []
    while True:
        start = buf.find(b"<")
        if start < 0:
            return out, b""
        end = buf.find(b">", start)
        if end < 0:
            return out, buf[start:]
        words = buf[start + 1:end].decode("ascii", "replace").split()
        if words:
            out.append(words)
        buf = buf[end + 1:]


def blank() -> dict:
    return {"present": False, "tx": "", "name": "", "battery": None, "minutes": None, "muted": False,
            "rf_dbm": None, "audio": None, "antenna": ""}


def apply(chans: dict[int, dict], words: list[str]) -> bool:
    """Fold one message into the per-channel state. True when something it knows about changed."""
    if len(words) < 3 or words[0] not in ("REP", "SAMPLE") or not words[1].isdigit():
        return False
    n = int(words[1])
    if n not in chans:
        return False
    c, before = chans[n], dict(chans[n])
    if words[0] == "SAMPLE":
        # SAMPLE n ALL <antenna> <rf> <audio>
        if len(words) >= 6 and words[2] == "ALL" and words[4].isdigit() and words[5].isdigit():
            c["antenna"] = words[3] if words[3] in ("AX", "XB", "XX") else ""
            c["rf_dbm"] = min(115, int(words[4])) - 128
            c["audio"] = min(50, int(words[5]))
        return c != before
    key, value = words[2], " ".join(words[3:])
    if key == "TX_TYPE":
        c["tx"] = "" if value == "UNKN" else value
        c["present"] = value not in ("", "UNKN")
        if not c["present"]:
            c.update(battery=None, minutes=None, rf_dbm=None, audio=None)
    elif key == "BATT_CHARGE":
        c["battery"] = int(value) if value.isdigit() and int(value) <= 100 else None      # 255: unknown
    elif key == "BATT_RUN_TIME":
        c["minutes"] = int(value) if value.isdigit() and int(value) < 65535 else None     # 65535: unknown
    elif key == "AUDIO_MUTE":
        c["muted"] = value == "ON"
    elif key == "CHAN_NAME":
        c["name"] = value.strip("{} ")[:31]
    return c != before


def rf_percent(dbm: int | None) -> int | None:
    if dbm is None:
        return None
    span = RF_CEIL_DBM - RF_FLOOR_DBM
    return max(0, min(100, round((dbm - RF_FLOOR_DBM) * 100 / span)))


def audio_percent(level: int | None) -> int | None:
    return None if level is None else max(0, min(100, level * 2))


class Receiver:
    """One Shure receiver: a connection that keeps itself alive and the state of each of its channels."""

    def __init__(self, rid: str, name: str, host: str, port: int, channels: int,
                 on_change: Callable[[], None] | None = None) -> None:
        self.id, self.name, self.host, self.port = rid, name, host, int(port)
        self.chans = {n: blank() for n in range(1, channels + 1)}
        self.on_change = on_change or (lambda: None)
        self.connected = False
        self.error = ""
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name=f"wireless-mics-{self.id}")

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
        for n in self.chans:
            self.chans[n] = blank()
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
                self.error = netsock.failure(TRAFFIC, "Could not reach the receiver")
            except Exception:  # a bad message must not end the loop
                log.exception("receiver %s session failed", self.id)
                self.error = "Lost the connection"
            self._drop()
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    def _send(self, text: str) -> None:
        if self._writer is not None:
            self._writer.write(f"< {text} >".encode("ascii", "replace"))

    def _ask(self) -> None:
        for n in self.chans:
            self._send(f"SET {n} METER_RATE {METER_RATE}")      # per channel: "channel 0" for SET is not shown in Shure's docs
            for key in KEYS:
                self._send(f"GET {n} {key}")

    async def _session(self) -> None:
        reader, writer = await netsock.open_tcp(TRAFFIC, self.host, self.port, CONNECT_S)
        self._writer = writer
        heard = last_poll = time.monotonic()
        buf = b""
        self.connected, self.error = True, ""
        self.on_change()
        self._ask()
        while not self._stop.is_set():
            try:
                data = await asyncio.wait_for(reader.read(4096), READ_S)
            except asyncio.TimeoutError:
                data = None
            now = time.monotonic()
            if data == b"":
                self.error = "The receiver closed the connection"
                return
            if data:
                heard = now
                buf += data
                msgs, buf = parse(buf)
                if len(buf) > MAX_BUFFER:
                    self.error = "The receiver sent something Relay could not read"
                    return
                if any([apply(self.chans, m) for m in msgs]):
                    self.on_change()
            if now - heard > QUIET_S:
                self.error = "The receiver stopped answering"
                return
            if now - last_poll >= POLL_S:
                last_poll = now
                self._send("GET 1 TX_TYPE")
