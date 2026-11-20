"""One connection per desk (#150): each driver keeps its desk's mirror current and sends what an operator asks for.

A driver reconnects by itself, with a growing pause, and reports a fixed sentence when it cannot reach the desk. Every
command returns "" when it was sent, or a sentence saying why not. Nothing here moves a desk on its own: commands only
come from an operator's action, a confirmed name push or a route an operator made.

* Allen & Heath Avantis, dLive, GLD and Qu do not echo what Relay sends, so a mute or fader Relay sends shows in the
  mirror at once; names are asked for again every NAME_POLL_S, which is how a name changed on the desk is noticed.
* The SQ, the Yamaha desks, the X32 and the WING answer every change, so the mirror always shows the desk's own word.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import time
from typing import Any

from ... import netsock
from . import ah, behringer as bh, osc, yamaha
from .mirror import Mirror
from .models import OFF_DB, clean_name, parse_strip

log = logging.getLogger("relay.audio_mixers")

TRAFFIC = "audio_mixers"
CONNECT_TIMEOUT_S = 5.0
BACKOFF_S = (2, 5, 10, 30)
NAME_POLL_S = 30.0               # Allen & Heath: ask every name again (the desk does not send renames)
KEEPALIVE_S = 8.0                # X32 /xremote and WING /*S last about ten seconds
LOST_S = 25.0                    # a UDP desk that said nothing for this long is not reached
PACE = 40                        # requests sent before a short pause, so a desk is not flooded on connect
PACE_S = 0.02

NOT_CONNECTED = "The desk is not connected."
NO_SUCH_STRIP = "The desk has no such channel."
NO_NAMES = "This desk does not take names over its control protocol."
NO_SENDS = "Relay cannot set this desk's sends yet."


class Driver:
    family = ""

    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        self.cfg = cfg
        self.mirror = mirror
        self.model = mirror.model
        self.host: str = cfg.get("host") or ""
        self.port: int = int(cfg.get("port") or self.model.port)
        self.error = ""
        self._task: asyncio.Task | None = None
        self._stopping = False

    # -- life ------------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._stopping = False
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop(), name=f"audio-mixer-{self.cfg.get('id')}")

    async def stop(self) -> None:
        self._stopping = True
        t, self._task = self._task, None
        if t is not None:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self.close()
        self.mirror.link(False)

    async def _loop(self) -> None:
        tries = 0
        while not self._stopping:
            try:
                await self.session()
                tries = 0
            except asyncio.CancelledError:
                raise
            except netsock.RouteError as e:
                self.error = str(e)
            except (OSError, asyncio.TimeoutError, ConnectionError):
                self.error = f"Could not reach the desk at {self.host}:{self.port}."
            except Exception:
                log.exception("audio mixer %s failed", self.cfg.get("id"))
                self.error = "The connection to the desk failed."
            self.close()
            self.mirror.link(False)
            if self._stopping:
                return
            await asyncio.sleep(BACKOFF_S[min(tries, len(BACKOFF_S) - 1)])
            tries += 1

    async def session(self) -> None:        # connect, then follow the desk until the link drops
        raise NotImplementedError

    def close(self) -> None:
        pass

    def connected(self) -> bool:
        return self.mirror.connected

    # -- commands (each returns "" or a sentence) ---------------------------------------------------------------------
    def mute(self, key: str, on: bool) -> str:
        return NOT_CONNECTED

    def level(self, key: str, db: float) -> str:
        return NOT_CONNECTED

    def name(self, key: str, text: str) -> str:
        return NO_NAMES

    def colour(self, key: str, colour: str) -> str:
        return NOT_CONNECTED

    def recall(self, scene: int) -> str:
        return NOT_CONNECTED

    def send(self, inp: int, mix: int, db: float | None) -> str:
        return NO_SENDS

    can_send = False
    reads_sends = False

    def can_name(self) -> bool:
        return self.model.name_len > 0


# -- TCP desks ---------------------------------------------------------------------------------------------------------
class _Tcp(Driver):
    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        super().__init__(cfg, mirror)
        self.writer: asyncio.StreamWriter | None = None

    async def open(self) -> asyncio.StreamReader:
        reader, writer = await netsock.open_tcp(TRAFFIC, self.host, self.port, timeout=CONNECT_TIMEOUT_S)
        self.writer = writer
        self.error = ""
        self.mirror.link(True)
        return reader

    def close(self) -> None:
        w, self.writer = self.writer, None
        if w is not None:
            with contextlib.suppress(Exception):
                w.close()

    def write(self, data: bytes) -> str:
        if self.writer is None or self.writer.is_closing() or not data:
            return NOT_CONNECTED
        try:
            self.writer.write(data)
        except (OSError, RuntimeError):
            return NOT_CONNECTED
        return ""

    async def paced(self, chunks: list[bytes]) -> None:
        for i in range(0, len(chunks), PACE):
            if self.write(b"".join(chunks[i:i + PACE])):
                return
            if self.writer is not None:
                await self.writer.drain()
            await asyncio.sleep(PACE_S)


class AllenHeath(_Tcp):
    """Avantis, dLive, GLD and Qu (MIDI over TCP), with AH-Net for Avantis faders and scene names when turned on."""
    family = "ah"

    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        super().__init__(cfg, mirror)
        self.codec = ah.AH(self.model, int(cfg.get("midi_channel") or 1))
        self.nrpn = ah.NrpnState()
        self.bank: dict[int, int] = {}
        self.ahnet = bool(cfg.get("ahnet")) and self.model.id == "avantis"
        self.ahnet_state = "off" if not self.ahnet else "connecting"
        self._ahnet_task: asyncio.Task | None = None

    @property
    def can_send(self) -> bool:  # type: ignore[override]
        return self.model.id in ("avantis", "dlive", "gld")

    def _addr(self, key: str) -> ah.Address | None:
        got = parse_strip(key)
        return self.codec.address(*got) if got else None

    async def session(self) -> None:
        reader = await self.open()
        parser = ah.MidiParser()
        if self.ahnet and (self._ahnet_task is None or self._ahnet_task.done()):
            self._ahnet_task = asyncio.get_running_loop().create_task(self._ahnet_loop())
        await self._ask_names()
        next_poll = time.monotonic() + NAME_POLL_S
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=max(0.5, next_poll - time.monotonic()))
            except asyncio.TimeoutError:
                await self._ask_names()
                next_poll = time.monotonic() + NAME_POLL_S
                continue
            if not data:
                raise ConnectionError("closed")
            for ev in parser.feed(data):
                self.apply(ev)

    async def _ask_names(self) -> None:
        asks = []
        for key in self.mirror.strips:
            a = self._addr(key)
            if a is not None and self.model.name_len:
                asks += [self.codec.ask_name(a), self.codec.ask_colour(a)]
        await self.paced(asks)

    def apply(self, ev: tuple) -> None:
        m, c = self.mirror, self.codec
        if ev[0] == "note":
            _, ch, note, vel = ev
            key = c.strip_at(ch, note)
            if key and vel:
                m.set(key, muted=vel >= 0x40)
        elif ev[0] == "cc":
            _, ch, ctl, val = ev
            if ctl == 0x00:
                self.bank[ch] = val
                return
            got = self.nrpn.cc(ch, ctl, val, fine=False)
            if got is None:
                return
            num, param, value, _ = got
            key = c.strip_at(ch, num)
            if key and param == 0x17:
                m.set(key, db=ah.ah_db(value))
            elif key and key.startswith("in") and self.model.id == "gld" and 0x20 <= param < 0x20 + self.model.count("mix"):
                m.set_send(key, param - 0x20 + 1, db=ah.ah_db(value))
        elif ev[0] == "pc":
            _, ch, prog = ev
            if ch == c.base:
                n = self.bank.get(ch, 0) * 128 + prog + 1
                if 1 <= n <= self.model.scenes:
                    m.set_scene(n)
        elif ev[0] == "sysex":
            got = c.sysex(ev[1])
            if got:
                what, key, value = got
                m.set(key, **{"name" if what == "name" else "colour": value})

    def close(self) -> None:
        super().close()
        t, self._ahnet_task = self._ahnet_task, None
        if t is not None:
            t.cancel()

    # commands
    def mute(self, key: str, on: bool) -> str:
        a = self._addr(key)
        if a is None:
            return NO_SUCH_STRIP
        said = self.write(self.codec.mute(a, on))
        if not said:
            self.mirror.set(key, muted=on)
        return said

    def level(self, key: str, db: float) -> str:
        a = self._addr(key)
        if a is None:
            return NO_SUCH_STRIP
        said = self.write(self.codec.fader(a, db))
        if not said:
            self.mirror.set(key, db=db)
        return said

    def name(self, key: str, text: str) -> str:
        a = self._addr(key)
        if a is None:
            return NO_SUCH_STRIP
        said = self.write(self.codec.name(a, clean_name(text, self.model.name_len)) + self.codec.ask_name(a))
        return said

    def colour(self, key: str, colour: str) -> str:
        a = self._addr(key)
        if a is None:
            return NO_SUCH_STRIP
        return self.write(self.codec.colour(a, colour) + self.codec.ask_colour(a))

    def recall(self, scene: int) -> str:
        said = self.write(self.codec.scene(scene))
        if not said:
            self.mirror.set_scene(scene)
        return said

    def send(self, inp: int, mix: int, db: float | None) -> str:
        msg = self.codec.send(inp, mix, db)
        if msg is None:
            return NO_SENDS
        said = self.write(msg)
        if not said:
            self.mirror.set_send(f"in{inp}", mix, on=db is not None, db=OFF_DB if db is None else db)
        return said

    # AH-Net: every input fader and the scene names (Avantis, when the operator turned it on)
    async def _ahnet_loop(self) -> None:
        first = True
        while self.writer is not None:
            try:
                await self._ahnet_session(first)
                first = False
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError, ConnectionError, netsock.RouteError):
                self.ahnet_state = "unreachable"
                await asyncio.sleep(10)

    async def _ahnet_session(self, read_scenes: bool) -> None:
        reader, writer = await netsock.open_tcp(TRAFFIC, self.host, ah.AHNET_PORT, timeout=CONNECT_TIMEOUT_S)
        buf = bytearray()
        try:
            writer.write(ah.ahnet_hello())
            ok = False
            deadline = time.monotonic() + 2.0
            while not ok:
                data = await asyncio.wait_for(reader.read(4096), timeout=max(0.1, deadline - time.monotonic()))
                if not data:
                    raise ConnectionError("closed")
                buf += data
                ok = any(ah.ahnet_hello_ok(f) for f in ah.ahnet_frames(buf))
            self.ahnet_state = "ready"
            for n in range(1, self.model.count("in") + 1):
                writer.write(ah.ahnet_fader_subscribe(n))
                if n % PACE == 0:
                    await asyncio.sleep(PACE_S)
            if read_scenes:
                for i in range(int(self.cfg.get("scene_names") or 0)):
                    writer.write(ah.ahnet_scene_ask(i))
                    name = await self._ahnet_wait(reader, buf, 0.4)
                    if name:
                        self.mirror.name_scene(i + 1, name)
            end = time.monotonic() + ah.AHNET_RENEW_S
            while time.monotonic() < end:
                await self._ahnet_wait(reader, buf, end - time.monotonic())
        finally:
            writer.close()

    async def _ahnet_wait(self, reader: asyncio.StreamReader, buf: bytearray, timeout: float) -> str | None:
        """Read frames for up to `timeout`, applying fader values; the first scene name seen ends the wait."""
        end = time.monotonic() + max(0.05, timeout)
        while time.monotonic() < end:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=max(0.05, end - time.monotonic()))
            except asyncio.TimeoutError:
                return None
            if not data:
                raise ConnectionError("closed")
            buf += data
            for f in ah.ahnet_frames(buf):
                got = ah.ahnet_fader(f)
                if got:
                    self.mirror.set(f"in{got[0]}", db=got[1])
                    continue
                name = ah.ahnet_scene_name(f)
                if name is not None:
                    return name
        return None


class SQ(_Tcp):
    """Allen & Heath SQ (NRPN over TCP). The SQ answers get requests, so the mirror starts complete."""
    family = "sq"
    can_send = True
    reads_sends = True

    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        super().__init__(cfg, mirror)
        self.codec = ah.SQ(self.model, int(cfg.get("midi_channel") or 1))
        self.nrpn = ah.NrpnState()
        self.bank = 0

    def can_name(self) -> bool:
        return False

    async def session(self) -> None:
        reader = await self.open()
        parser = ah.MidiParser()
        asks = self.codec.ask_all()
        await self.paced([asks[i:i + 9] for i in range(0, len(asks), 9)])
        while True:
            data = await reader.read(4096)
            if not data:
                raise ConnectionError("closed")
            for ev in parser.feed(data):
                self.apply(ev)

    def apply(self, ev: tuple) -> None:
        if ev[0] == "cc":
            _, ch, ctl, val = ev
            if ch != self.codec.ch:
                return
            if ctl == 0x00:
                self.bank = val
                return
            got = self.nrpn.cc(ch, ctl, val, fine=True)
            if got:
                said = self.codec.value(*got)
                if said is None:
                    return
                what, key, value = said
                if what == "mute":
                    self.mirror.set(key, muted=bool(value))
                elif what == "level":
                    self.mirror.set(key, db=float(value))  # type: ignore[arg-type]
                else:
                    mix, on, db = value  # type: ignore[misc]
                    self.mirror.set_send(key, mix, on=on, db=db)
        elif ev[0] == "pc" and ev[1] == self.codec.ch:
            n = self.bank * 128 + ev[2] + 1
            if 1 <= n <= self.model.scenes:
                self.mirror.set_scene(n)

    def _strip(self, key: str) -> tuple[str, int] | None:
        got = parse_strip(key)
        return got if got and got[1] <= self.model.count(got[0]) else None

    def mute(self, key: str, on: bool) -> str:
        s = self._strip(key)
        return self.write(self.codec.mute(s[0], s[1], on)) if s else NO_SUCH_STRIP

    def level(self, key: str, db: float) -> str:
        s = self._strip(key)
        return self.write(self.codec.fader(s[0], s[1], db)) if s else NO_SUCH_STRIP

    def colour(self, key: str, colour: str) -> str:
        return "The SQ takes no colours over MIDI."

    def recall(self, scene: int) -> str:
        said = self.write(self.codec.scene(scene))
        if not said:
            self.mirror.set_scene(scene)
        return said

    def send(self, inp: int, mix: int, db: float | None) -> str:
        if not (1 <= inp <= self.model.count("in") and 1 <= mix <= self.model.count("mix")):
            return NO_SUCH_STRIP
        return self.write(self.codec.send(inp, mix, db))


class Yamaha(_Tcp):
    """Yamaha CL, QL, TF and DM over RCP."""
    family = "yamaha"
    can_send = True
    reads_sends = True

    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        super().__init__(cfg, mirror)
        self.lib = yamaha.library(self.model)

    async def session(self) -> None:
        reader = await self.open()
        asks = ["devinfo productname\n", f"sscurrent_ex {self.lib}\n"]
        for key in self.mirror.strips:
            kind, n = parse_strip(key)  # type: ignore[misc]
            asks += [yamaha.get_cmd(kind, n, w) for w in ("Label/Name", "Label/Color", "Fader/On", "Fader/Level")]
        for i in range(self.model.count("in")):
            for m in range(self.model.count("mix")):
                asks += [f"get {yamaha.SEND_ON} {i} {m}\n", f"get {yamaha.SEND_LEVEL} {i} {m}\n"]
        await self.paced([a.encode() for a in asks])
        last = time.monotonic()
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                if time.monotonic() - last > LOST_S:
                    raise ConnectionError("silent")
                self.write(b"devstatus runmode\n")
                continue
            if not line:
                raise ConnectionError("closed")
            last = time.monotonic()
            self.apply(line.decode("utf-8", "replace"))

    def apply(self, line: str) -> None:
        got = yamaha.parse(line)
        if not got:
            if line.startswith("OK") or line.startswith("NOTIFY"):
                self.mirror.heard()
            return
        m = self.mirror
        if got[0] == "strip":
            _, key, what, value = got
            if what == "level":
                m.set(key, db=value)
            elif what == "on":
                m.set(key, muted=not value)
            elif what == "name":
                m.set(key, name=value)
            else:
                m.set(key, colour=value)
        elif got[0] == "send":
            _, i, mix, what, value = got
            m.set_send(f"in{i}", mix, **({"db": value} if what == "level" else {"on": value}))
        elif got[0] == "scene":
            m.set_scene(got[1])
            self.write(f"ssinfo_ex {self.lib} {got[1]}\n".encode())
        elif got[0] == "scene_name":
            m.name_scene(got[1], got[2])
        elif got[0] == "product":
            m.product = got[1][:40]

    def _set(self, key: str, what: str, value: int | str) -> str:
        got = parse_strip(key)
        if not got or got[1] > self.model.count(got[0]):
            return NO_SUCH_STRIP
        return self.write(yamaha.set_cmd(got[0], got[1], what, value).encode())

    def mute(self, key: str, on: bool) -> str:
        return self._set(key, "Fader/On", 0 if on else 1)

    def level(self, key: str, db: float) -> str:
        return self._set(key, "Fader/Level", yamaha.level(db))

    def name(self, key: str, text: str) -> str:
        return self._set(key, "Label/Name", clean_name(text, self.model.name_len))

    def colour(self, key: str, colour: str) -> str:
        return self._set(key, "Label/Color", yamaha.COLOURS.get(colour, "Off"))

    def recall(self, scene: int) -> str:
        return self.write(f"ssrecall_ex {self.lib} {scene}\n".encode())

    def send(self, inp: int, mix: int, db: float | None) -> str:
        if not (1 <= inp <= self.model.count("in") and 1 <= mix <= self.model.count("mix")):
            return NO_SUCH_STRIP
        x, y = inp - 1, mix - 1
        out = f"set {yamaha.SEND_ON} {x} {y} {0 if db is None else 1}\n"
        if db is not None:
            out += f"set {yamaha.SEND_LEVEL} {x} {y} {yamaha.level(db)}\n"
        return self.write(out.encode())


# -- UDP desks (OSC) -------------------------------------------------------------------------------------------------
class _Proto(asyncio.DatagramProtocol):
    def __init__(self, driver: "_Osc") -> None:
        self.driver = driver

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if isinstance(addr, tuple) and self.driver.peer and addr[0] == self.driver.peer[0]:
            self.driver.packet(data)

    def error_received(self, exc: Exception) -> None:
        pass


class _Osc(Driver):
    can_send = True
    reads_sends = True

    def __init__(self, cfg: dict, mirror: Mirror) -> None:
        super().__init__(cfg, mirror)
        self.transport: asyncio.DatagramTransport | None = None
        self.peer: tuple[str, int] | None = None

    async def open(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(self.host, self.port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except OSError:
            raise netsock.RouteError(f"Could not find {self.host}.") from None
        self.peer = infos[0][4][:2]
        self.transport, _ = await netsock.udp_endpoint(TRAFFIC, lambda: _Proto(self), 0, toward=self.peer)

    def close(self) -> None:
        t, self.transport = self.transport, None
        if t is not None:
            t.close()

    def osc(self, address: str, *args: str | int | float) -> str:
        if self.transport is None or self.peer is None:
            return NOT_CONNECTED
        try:
            self.transport.sendto(osc.message(address, *args), self.peer)
        except OSError:
            return NOT_CONNECTED
        return ""

    def connected(self) -> bool:
        return self.mirror.connected and time.time() - self.mirror.heard_at < LOST_S

    def packet(self, data: bytes) -> None:
        msgs = osc.parse(data)
        if not msgs:
            return
        self.mirror.heard()
        if not self.mirror.connected:
            self.mirror.link(True)
            self.error = ""
        for address, args in msgs:
            try:
                self.apply(address, args)
            except (TypeError, ValueError, IndexError):
                continue

    def apply(self, address: str, args: list[Any]) -> None:
        raise NotImplementedError

    def queries(self) -> list[str]:
        raise NotImplementedError

    def keepalive(self) -> None:
        raise NotImplementedError

    async def session(self) -> None:
        await self.open()
        self.keepalive()
        qs = self.queries()
        for i, q in enumerate(qs):
            self.osc(q)
            if i % PACE == PACE - 1:
                await asyncio.sleep(PACE_S)
        while True:
            await asyncio.sleep(KEEPALIVE_S)
            self.keepalive()
            if self.mirror.connected and time.time() - self.mirror.heard_at > LOST_S:
                self.mirror.link(False)
                self.error = f"The desk at {self.host} stopped answering."
                raise ConnectionError("silent")
            if not self.mirror.connected and not self.mirror.heard_at:
                self.error = f"No answer from the desk at {self.host}:{self.port}."


class X32(_Osc):
    family = "x32"

    def keepalive(self) -> None:
        self.osc("/xremote")
        self.osc("/xinfo")
        self.osc("/meters", bh.X32_METERS, 1)

    def queries(self) -> list[str]:
        qs = ["/-show/prepos/current"]
        for key in self.mirror.strips:
            kind, n = parse_strip(key)  # type: ignore[misc]
            qs += list(bh.x32_paths(kind, n).values())
        for i in range(1, self.model.count("in") + 1):
            for m in range(1, self.model.count("mix") + 1):
                qs += [f"/ch/{i:02d}/mix/{m:02d}/on", f"/ch/{i:02d}/mix/{m:02d}/level"]
        qs += [f"/-show/showfile/scene/{s:03d}/name" for s in range(self.model.scenes)]
        return qs

    def apply(self, address: str, args: list[Any]) -> None:
        m = self.mirror
        a0 = args[0] if args else None
        if address == bh.X32_METERS and isinstance(a0, bytes):
            vals = bh.x32_meters(a0)
            if vals:
                for key in m.strips:
                    kind, n = parse_strip(key)  # type: ignore[misc]
                    i = bh.x32_meter_index(kind, n)
                    if i is not None and i < len(vals):
                        m.set_meter(key, vals[i] if vals[i] is not None else -90.0)
                m.meters_read()
            return
        if address == "/xinfo" and len(args) >= 3:
            m.product = str(args[2])[:40]
            return
        if address == "/-show/prepos/current" and isinstance(a0, int):
            if a0 >= 0:
                m.set_scene(a0 + 1)
            return
        n = bh.x32_scene_name(address)
        if n is not None and isinstance(a0, str):
            m.name_scene(n + 1, a0)
            return
        got = bh.x32_strip(address)
        if got and a0 is not None:
            key, what = got
            if what == "fader" and isinstance(a0, float):
                m.set(key, db=bh.x32_db(a0))
            elif what == "on" and isinstance(a0, int):
                m.set(key, muted=a0 == 0)
            elif what == "name" and isinstance(a0, str):
                m.set(key, name=a0)
            elif what == "colour" and isinstance(a0, int):
                m.set(key, colour=bh.x32_colour(a0))
            return
        s = bh.x32_send(address)
        if s and a0 is not None:
            i, mix, what = s
            if what == "level" and isinstance(a0, float):
                m.set_send(f"in{i}", mix, db=bh.x32_db(a0))
            elif what == "on" and isinstance(a0, int):
                m.set_send(f"in{i}", mix, on=a0 == 1)

    def _paths(self, key: str) -> dict[str, str]:
        got = parse_strip(key)
        return bh.x32_paths(*got) if got and got[1] <= self.model.count(got[0]) else {}

    def _set(self, key: str, what: str, value: str | int | float) -> str:
        p = self._paths(key).get(what)
        if not p:
            return NO_SUCH_STRIP
        return self.osc(p, value) or self.osc(p)        # then ask, so the desk's answer updates the mirror

    def mute(self, key: str, on: bool) -> str:
        return self._set(key, "on", 0 if on else 1)

    def level(self, key: str, db: float) -> str:
        return self._set(key, "fader", bh.x32_level(db))

    def name(self, key: str, text: str) -> str:
        return self._set(key, "name", clean_name(text, self.model.name_len))

    def colour(self, key: str, colour: str) -> str:
        return self._set(key, "colour", bh.x32_colour_value(colour))

    def recall(self, scene: int) -> str:
        return self.osc("/-action/goscene", scene - 1) or self.osc("/-show/prepos/current")

    def send(self, inp: int, mix: int, db: float | None) -> str:
        if not (1 <= inp <= self.model.count("in") and 1 <= mix <= self.model.count("mix")):
            return NO_SUCH_STRIP
        base = f"/ch/{inp:02d}/mix/{mix:02d}"
        said = self.osc(f"{base}/on", 0 if db is None else 1)
        if not said and db is not None:
            said = self.osc(f"{base}/level", bh.x32_level(db))
        self.osc(f"{base}/on")
        self.osc(f"{base}/level")
        return said


class Wing(_Osc):
    family = "wing"

    def keepalive(self) -> None:
        self.osc("/*S")

    def queries(self) -> list[str]:
        qs = ["/$ctl/lib/$actidx", "/$ctl/lib"]
        for key in self.mirror.strips:
            kind, n = parse_strip(key)  # type: ignore[misc]
            qs += list(bh.wing_paths(kind, n).values())
        for i in range(1, self.model.count("in") + 1):
            for m in range(1, self.model.count("mix") + 1):
                qs += [f"/ch/{i}/send/{m}/on", f"/ch/{i}/send/{m}/lvl"]
        return qs

    def apply(self, address: str, args: list[Any]) -> None:
        m = self.mirror
        if address == "/$ctl/lib/$actidx":
            v = bh.wing_number(args)
            if v is not None and v >= 1:
                m.set_scene(int(v))
            return
        if address.startswith("/$ctl/lib"):
            names = bh.wing_scenes(args)
            if names is not None:
                m.set_scenes(names)
            return
        got = bh.wing_strip(address)
        if got:
            key, what = got
            if what == "name":
                t = bh.wing_text(args)
                if t is not None:
                    m.set(key, name=t)
                return
            v = bh.wing_number(args)
            if v is None:
                return
            if what == "fader":
                m.set(key, db=v)
            elif what == "mute":
                m.set(key, muted=v >= 0.5)
            else:
                m.set(key, colour=bh.wing_colour(v))
            return
        s = bh.wing_send(address)
        if s:
            i, mix, what = s
            v = bh.wing_number(args)
            if v is not None:
                m.set_send(f"in{i}", mix, **({"db": v} if what == "lvl" else {"on": v >= 0.5}))

    def _set(self, key: str, what: str, value: str | int | float) -> str:
        got = parse_strip(key)
        p = bh.wing_paths(*got).get(what) if got and got[1] <= self.model.count(got[0]) else None
        if not p:
            return NO_SUCH_STRIP
        return self.osc(p, value) or self.osc(p)

    def mute(self, key: str, on: bool) -> str:
        return self._set(key, "mute", 1 if on else 0)

    def level(self, key: str, db: float) -> str:
        return self._set(key, "fader", float(max(-144.0, db)))

    def name(self, key: str, text: str) -> str:
        return self._set(key, "name", clean_name(text, self.model.name_len))

    def colour(self, key: str, colour: str) -> str:
        return self._set(key, "colour", bh.wing_colour_value(colour))

    def recall(self, scene: int) -> str:
        return self.osc("/$ctl/lib/$actionidx", scene) or self.osc("/$ctl/lib/$action", "GO") \
            or self.osc("/$ctl/lib/$actidx")

    def send(self, inp: int, mix: int, db: float | None) -> str:
        if not (1 <= inp <= self.model.count("in") and 1 <= mix <= self.model.count("mix")):
            return NO_SUCH_STRIP
        base = f"/ch/{inp}/send/{mix}"
        said = self.osc(f"{base}/on", 0 if db is None else 1)
        if not said and db is not None:
            said = self.osc(f"{base}/lvl", float(max(-144.0, db)))
        self.osc(f"{base}/on")
        self.osc(f"{base}/lvl")
        return said


DRIVERS: dict[str, type[Driver]] = {"ah": AllenHeath, "sq": SQ, "yamaha": Yamaha, "x32": X32, "wing": Wing}


def make(cfg: dict, mirror: Mirror) -> Driver:
    return DRIVERS[mirror.model.family](cfg, mirror)
