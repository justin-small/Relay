"""Tally Arbiter listener (#244): follows a Tally Arbiter server and publishes its tally into core's shared state.

Tally Arbiter (MIT, https://github.com/josephdadams/TallyArbiter) reads tally from many switchers and serves it over
Socket.IO. Relay joins as a listener client, the way a tally light does, and needs no login. Socket.IO's wire format
is small, so it is spoken here over the `websockets` package Relay already has:

    server  0{"sid":...,"pingInterval":25000,...}      Engine.IO open
    client  40                                         connect to the default namespace
    server  2   ->  client 3                           ping / pong
    both    42["event", arg, ...]                      an event

Each Tally Arbiter device is a camera: number 1 is the first device, in the order Tally Arbiter lists them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Callable

import websockets

from ... import netsock, tally

log = logging.getLogger("relay.tally_arbiter")

SOURCE = "tally_arbiter"
DEFAULT_PORT = 4455
MAX_DEVICES = 64
REFRESH_S = 20.0
QUIET_S = 60.0                 # more than pingInterval + pingTimeout, with room
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_FRAME = 1_000_000
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")


def clean_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    host = raw.get("host").strip() if isinstance(raw.get("host"), str) else ""
    if not _HOST_RE.fullmatch(host):
        host = ""
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = DEFAULT_PORT
    return {"host": host, "port": port}


def encode_event(name: str, *args) -> str:
    return "42" + json.dumps([name, *args], separators=(",", ":"))


def decode(frame: str) -> tuple[str, object]:
    """One Engine.IO text frame as (kind, value): kind is open, ping, pong, connect, event, close or other."""
    if not frame:
        return "other", None
    head = frame[0]
    if head == "0":
        return "open", _json(frame[1:])
    if head == "2":
        return "ping", None
    if head == "3":
        return "pong", None
    if head == "1":
        return "close", None
    if frame.startswith("40"):
        return "connect", None
    if frame.startswith("41"):
        return "close", None
    if frame.startswith("42"):
        body = frame[2:]
        if body[:1] != "[":  # a namespace prefix such as 42/admin,[...]
            body = body[body.find(",") + 1:] if "," in body else ""
        data = _json(body)
        if isinstance(data, list) and data and isinstance(data[0], str):
            return "event", (data[0], data[1:])
    return "other", None


def _json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None


def cameras_from(devices: list, buses: list, states: dict[str, set[str]]) -> list[dict]:
    """`states` maps a device id to the ids of the busses that device is currently on."""
    program = {b.get("id") for b in buses if isinstance(b, dict) and b.get("type") == "program"}
    preview = {b.get("id") for b in buses if isinstance(b, dict) and b.get("type") == "preview"}
    out = []
    for i, d in enumerate(devices[:MAX_DEVICES]):
        if not isinstance(d, dict) or not isinstance(d.get("id"), str):
            continue
        on = states.get(d["id"], set())
        out.append({"n": i + 1, "name": d.get("name") if isinstance(d.get("name"), str) else "",
                    "program": bool(on & program), "preview": bool(on & preview)})
    return out


class Listener:
    def __init__(self, host: str, port: int = DEFAULT_PORT, on_change: Callable[[], None] | None = None) -> None:
        self.host, self.port = host, port
        self.on_change = on_change or (lambda: None)
        self.connected = False
        self.error = ""
        self.devices: list = []
        self.buses: list = []
        self.states: dict[str, set[str]] = {}
        self._joined: set[str] = set()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="tally-arbiter")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._lost()

    def cameras(self) -> list[dict]:
        return cameras_from(self.devices, self.buses, self.states)

    def _lost(self) -> None:
        was = self.connected
        self.connected = False
        self.devices, self.buses, self.states = [], [], {}
        self._joined.clear()
        if was:
            self.on_change()

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as exc:
                self.error = netsock.failure("tally_arbiter", "Could not reach Tally Arbiter") \
                    if not isinstance(exc, websockets.ConnectionClosed) else "Tally Arbiter closed the connection"
            except Exception:
                log.exception("Tally Arbiter listener failed")
                self.error = "Lost the connection"
            self._lost()
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def _once(self) -> None:
        url = f"ws://{self.host}:{self.port}/socket.io/?EIO=4&transport=websocket"
        # Through the port the operator chose for Tally Arbiter (#348); the system default when there is none.
        async with netsock.websocket_session("tally_arbiter", url, max_size=MAX_FRAME, open_timeout=5,
                                             ping_interval=None) as ws:
            heard = time.monotonic()
            next_refresh = 0.0
            greeted = False
            while not self._stop.is_set():
                if time.monotonic() - heard > QUIET_S:
                    raise asyncio.TimeoutError()
                if greeted and time.monotonic() >= next_refresh:
                    await ws.send(encode_event("devices"))
                    await ws.send(encode_event("bus_options"))
                    next_refresh = time.monotonic() + REFRESH_S
                try:
                    frame = await asyncio.wait_for(ws.recv(), 1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(frame, bytes):
                    continue
                heard = time.monotonic()
                kind, value = decode(frame)
                if kind == "open":
                    await ws.send("40")
                elif kind == "ping":
                    await ws.send("3")
                elif kind == "connect":
                    greeted = True
                    self.connected, self.error = True, ""
                elif kind == "close":
                    return
                elif kind == "event":
                    await self._event(ws, value[0], value[1])

    async def _event(self, ws, name: str, args: list) -> None:
        if name == "bus_options" and args and isinstance(args[0], list):
            self.buses = args[0][:16]
            self.on_change()
        elif name == "devices" and args and isinstance(args[0], list):
            self.devices = args[0][:MAX_DEVICES]
            for d in self.devices:
                if isinstance(d, dict) and isinstance(d.get("id"), str) and d["id"] not in self._joined:
                    self._joined.add(d["id"])
                    await ws.send(encode_event("listenerclient_connect", {
                        "deviceId": d["id"], "listenerType": "relay", "canBeReassigned": False,
                        "canBeFlashed": False, "supportsChat": False}))
            self.on_change()
        elif name == "device_states" and args and isinstance(args[0], list):
            for st in args[0]:
                if isinstance(st, dict) and isinstance(st.get("deviceId"), str) and isinstance(st.get("busId"), str):
                    on = self.states.setdefault(st["deviceId"], set())
                    (on.add if st.get("sources") else on.discard)(st["busId"])
            self.on_change()


class Service:
    def __init__(self) -> None:
        self.listener: Listener | None = None
        self._running = False

    def publish(self) -> None:
        li = self.listener
        if li is not None and li.connected:
            tally.tally.publish(SOURCE, li.cameras())
        else:
            tally.tally.clear(SOURCE)

    def start(self) -> None:
        self._running = True
        self._open()

    def _open(self) -> None:
        from ... import config

        s = clean_settings(config.get().get("tally_arbiter"))
        if self._running and s["host"]:
            self.listener = Listener(s["host"], s["port"], on_change=self.publish)
            self.listener.start()

    async def _close(self) -> None:
        if self.listener is not None:
            li, self.listener = self.listener, None
            await li.close()

    async def stop(self) -> None:
        self._running = False
        await self._close()
        tally.tally.clear(SOURCE)

    async def restart(self) -> None:
        await self._close()
        tally.tally.clear(SOURCE)
        self._open()

    def snapshot(self) -> dict:
        from ... import config

        s = clean_settings(config.get().get("tally_arbiter"))
        li = self.listener
        out = {"host": s["host"], "port": s["port"], "configured": bool(s["host"]), "connected": False,
               "error": li.error if li else "", "cameras": []}
        if li is not None and li.connected:
            out.update(connected=True, cameras=li.cameras())
        return out


service = Service()
