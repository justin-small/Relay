"""The ATEM connection (#243): an asyncio UDP client that follows a switcher and sends it commands.

It opens a session, acknowledges what the switcher sends (in order, holding back anything that arrives early),
sends commands reliably (resent until acknowledged), and starts again with a growing wait when the switcher goes
quiet or a packet is lost for good. The switcher's state is what it has told us: input names, tally, what each mix
effect has on program and preview, and its macros.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from ... import netsock
from . import atem_protocol as p

log = logging.getLogger("relay.blackmagic")

QUIET_S = 5.0            # nothing heard from the switcher for this long: start again
RESEND_S = 0.06
MAX_RESENDS = 10
MAX_AHEAD = 256          # how far ahead of the expected packet one may be and still be held
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_QUEUE = 64
MAX_INPUTS = 200
MAX_MACROS = 100


class State:
    def __init__(self) -> None:
        self.product = ""
        self.version = 0
        self.mix_effects = 1
        self.downstream_keyers = 0
        self.inputs: dict[int, dict] = {}
        self.tally: dict[int, tuple[bool, bool]] = {}
        self.program: dict[int, int] = {}
        self.preview: dict[int, int] = {}
        self.macros: dict[int, str] = {}
        self.ready = False


class AtemClient:
    def __init__(self, host: str, port: int = p.PORT, on_change: Callable[[], None] | None = None) -> None:
        self.host, self.port = host, port
        self.on_change = on_change or (lambda: None)
        self.state = State()
        self.connected = False
        self.error = ""
        self._transport: asyncio.DatagramTransport | None = None
        self._session = 0
        self._next_id = 1
        self._expected = 0          # the id of the last packet taken in order
        self._held: dict[int, bytes] = {}
        self._inflight: list[dict] = []
        self._heard = time.monotonic()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._established = asyncio.Event()

    # ----------------------------------------------------------------------------------------- run
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="atem-client")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._drop()

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._session_once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except OSError:
                # A fixed sentence (the page never shows an exception's text), naming the chosen port (#348).
                self.error = netsock.failure("blackmagic", "Could not reach the switcher")
            except Exception:  # a bad packet must not end the loop
                log.exception("ATEM session failed")
                self.error = "Lost the connection"
            self._drop()
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def _session_once(self) -> None:
        loop = asyncio.get_running_loop()
        self._reset()
        # Through the port the operator chose for the switcher (#348); the system default when there is none.
        transport, _ = await netsock.udp_endpoint("blackmagic", lambda: _Proto(self), remote=(self.host, self.port))
        self._transport = transport
        self._heard = time.monotonic()
        hello_at = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - self._heard > QUIET_S:
                self.error = "The switcher stopped answering" if self.connected else "No answer from the switcher"
                return
            if not self.connected and now - hello_at >= 1.0:
                transport.sendto(p.HELLO)
                hello_at = now
            if not self._resend():
                self.error = "Lost the connection"
                return
            await asyncio.sleep(0.02)

    def _reset(self) -> None:
        self._session = 0
        self._next_id = 1
        self._expected = 0
        self._held.clear()
        self._inflight.clear()
        self.state = State()
        self.connected = False

    def _drop(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        was = self.connected
        self.connected = False
        self.state = State()
        if was:
            self.on_change()

    # ----------------------------------------------------------------------------------------- receive
    def datagram(self, data: bytes) -> None:
        try:
            head = p.parse_header(data)
        except p.Malformed:
            return
        self._heard = time.monotonic()
        self._session = head["session"]
        flags = head["flags"]
        if flags & p.NEW_SESSION:
            self.connected = True
            self.error = ""
            self._expected = head["id"]
            self._held.clear()
            self._ack(head["id"])
            return
        if not self.connected:
            return
        if flags & p.RETRANSMIT_REQUEST:
            self._resend_from(head["resend_from"] % p.MAX_ID)
        if flags & p.ACK_REQUEST and head["id"] < p.MAX_ID:
            self._take(head["id"], head["payload"])
        if flags & p.ACK_REPLY:
            self._acked(head["ack"])

    def _take(self, pid: int, payload: bytes) -> None:
        distance = (pid - self._expected) % p.MAX_ID
        if distance == 1:
            self._expected = pid
            self._apply(payload)
            nxt = (self._expected + 1) % p.MAX_ID
            while nxt in self._held:
                self._expected = nxt
                self._apply(self._held.pop(nxt))
                nxt = (nxt + 1) % p.MAX_ID
            self._ack(self._expected)
        elif 1 < distance <= MAX_AHEAD:
            self._held.setdefault(pid, payload)
        else:  # something already taken, sent again
            self._ack(self._expected)

    def _apply(self, payload: bytes) -> None:
        changed = False
        for name, data in p.split_commands(payload):
            try:
                changed |= self._command(name, data)
            except p.Malformed:
                continue
        if changed:
            self.on_change()

    def _command(self, name: str, data: bytes) -> bool:
        s = self.state
        if name == "_ver":
            s.version = p.parse_version(data)
        elif name == "_pin":
            s.product = p.parse_product(data)
        elif name == "_top":
            top = p.parse_topology(data)
            s.mix_effects, s.downstream_keyers = max(1, top["mix_effects"]), top["downstream_keyers"]
        elif name == "InPr":
            inp = p.parse_input(data)
            if inp["id"] in s.inputs or len(s.inputs) < MAX_INPUTS:
                s.inputs[inp["id"]] = inp
            return True
        elif name == "TlSr":
            s.tally = p.parse_tally(data)
            return True
        elif name == "PrgI":
            me, src = p.parse_me_source(data)
            s.program[me] = src
            return True
        elif name == "PrvI":
            me, src = p.parse_me_source(data)
            s.preview[me] = src
            return True
        elif name == "MPrp":
            macro = p.parse_macro(data)
            if macro["used"] and macro["slot"] < MAX_MACROS:
                s.macros[macro["slot"]] = macro["name"]
            else:
                s.macros.pop(macro["slot"], None)
            return True
        elif name == "InCm":
            s.ready = True
            return True
        return False

    # ----------------------------------------------------------------------------------------- send
    def _send(self, data: bytes) -> None:
        if self._transport is not None:
            self._transport.sendto(data)

    def _ack(self, pid: int) -> None:
        self._send(p.packet(p.ACK_REPLY, self._session, ack=pid))

    def send(self, *commands: bytes) -> bool:
        """Send commands (built with protocol.cut and the like) reliably. False when not connected or backed up."""
        if not self.connected or len(self._inflight) + len(commands) > MAX_QUEUE:
            return False
        for cmd in commands:
            pid = self._next_id
            self._next_id = (self._next_id + 1) % p.MAX_ID or 1
            raw = p.packet(p.ACK_REQUEST, self._session, packet_id=pid, payload=cmd)
            self._inflight.append({"id": pid, "raw": raw, "sent": time.monotonic(), "tries": 0})
            self._send(raw)
        return True

    def _acked(self, ack: int) -> None:
        def covered(pid: int) -> bool:
            return (ack - pid) % p.MAX_ID < p.MAX_ID // 2
        self._inflight = [f for f in self._inflight if not covered(f["id"])]

    def _resend_from(self, pid: int) -> None:
        index = next((i for i, f in enumerate(self._inflight) if f["id"] == pid), None)
        if index is None:
            return
        now = time.monotonic()
        for f in self._inflight[index:]:
            f["sent"], f["tries"] = now, f["tries"] + 1
            self._send(f["raw"])

    def _resend(self) -> bool:
        """Resend what has waited too long. False when a packet has been resent for good measure and never answered."""
        now = time.monotonic()
        for f in self._inflight:
            if now - f["sent"] >= RESEND_S:
                if f["tries"] >= MAX_RESENDS:
                    return False
                self._resend_from(f["id"])
                break
        return True

    # ----------------------------------------------------------------------------------------- what it knows
    def cameras(self) -> list[dict]:
        """Every physical input with its name and whether it is on program or preview anywhere."""
        out = []
        for iid in sorted(self.state.inputs):
            if not 1 <= iid <= p.MAX_CAMERA_INPUT:
                continue
            program, preview = self.state.tally.get(iid, (False, False))
            inp = self.state.inputs[iid]
            out.append({"n": iid, "name": inp["long"] or inp["short"], "program": program, "preview": preview})
        return out


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, client: AtemClient) -> None:
        self.client = client

    def datagram_received(self, data: bytes, addr) -> None:
        self.client.datagram(data)

    def error_received(self, exc: Exception) -> None:
        self.client.error = "Could not reach the switcher"
