"""obs-websocket 5 over Relay's routed WebSocket helper (#250): the handshake, requests and events.

The protocol is JSON with an `op` code. OBS sends Hello (0), with an `authentication` challenge when a password is
set; Relay answers Identify (1) and waits for Identified (2). After that a Request (6) carries `requestType`,
`requestId` and `requestData`, its RequestResponse (7) carries the same id, a `requestStatus` and `responseData`, and
OBS pushes Events (5) for the subscriptions named in Identify.

The password never leaves this computer: Identify carries
base64(sha256(base64(sha256(password + salt)) + challenge)).
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import itertools
import json
from typing import Any, Awaitable, Callable

from ... import netsock

TRAFFIC = "obs"
DEFAULT_PORT = 4455
TIMEOUT = 5.0
RPC_VERSION = 1
# Event subscriptions: General (1), Scenes (4), Inputs (8), Outputs (64) and Ui (1024, studio mode). The 20-a-second
# audio meters (InputVolumeMeters, 1 << 16) are added only when a computer has audio inputs to watch.
EVENTS = 1 | 4 | 8 | 64 | 1024
METERS = 1 << 16
MAX_MESSAGE = 4 * 1024 * 1024        # a screenshot is the largest answer


class ObsError(Exception):
    """A failure safe to show on the admin page: never built from the exception or OBS's own text."""


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def url_for(host: str, port: int) -> str:
    if not host:
        raise ObsError("Enter the OBS computer's address first.")
    if ":" in host:                      # an IPv6 literal
        host = f"[{host}]"
    return f"ws://{host}:{port or DEFAULT_PORT}"


class Connection:
    """One identified connection. `request()` may be called from any task; one reader task routes answers to their
    callers and events to `on_event`."""

    def __init__(self, ws, on_event: Callable[[str, dict], Awaitable[None] | None] | None = None) -> None:
        self.ws = ws
        self.on_event = on_event
        self._ids = itertools.count(1)
        self._waiting: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self.closed = asyncio.Event()

    @classmethod
    @contextlib.asynccontextmanager
    async def open(cls, host: str, port: int, password: str, on_event=None, events: int = EVENTS):
        url = url_for(host, port)
        try:
            ctx = netsock.websocket_session(TRAFFIC, url, open_timeout=TIMEOUT, max_size=MAX_MESSAGE)
            ws = await ctx.__aenter__()
        except ObsError:
            raise
        except Exception:
            raise ObsError(netsock.failure(
                TRAFFIC, "Could not reach OBS. Check that OBS is open, the WebSocket server is on "
                         "(Tools > WebSocket Server Settings), and the address and port.")) from None
        conn = cls(ws, on_event)
        try:
            await conn._identify(password, events)
            conn._reader = asyncio.get_running_loop().create_task(conn._read())
            yield conn
        finally:
            await conn._stop()
            with contextlib.suppress(Exception):
                await ctx.__aexit__(None, None, None)

    async def _recv_json(self) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), TIMEOUT)
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            raise ObsError("OBS sent something that is not obs-websocket 5.") from None
        if not isinstance(msg, dict):
            raise ObsError("OBS sent something that is not obs-websocket 5.")
        return msg

    async def _identify(self, password: str, events: int = EVENTS) -> None:
        try:
            hello = await self._recv_json()
        except ObsError:
            raise
        except Exception:
            raise ObsError("OBS did not say hello. Is this the obs-websocket port (4455 by default)?") from None
        if hello.get("op") != 0:
            raise ObsError("OBS did not say hello. Is this the obs-websocket port (4455 by default)?")
        d = hello.get("d") or {}
        ident: dict[str, Any] = {"rpcVersion": RPC_VERSION, "eventSubscriptions": events}
        auth = d.get("authentication")
        if isinstance(auth, dict):
            if not password:
                raise ObsError("OBS asks for a password. Enter the one in Tools > WebSocket Server Settings.")
            ident["authentication"] = auth_string(password, str(auth.get("salt", "")), str(auth.get("challenge", "")))
        await self.ws.send(json.dumps({"op": 1, "d": ident}))
        try:
            msg = await self._recv_json()
        except ObsError:
            raise
        except Exception:
            # OBS closes the connection (code 4009) on a wrong password.
            if auth:
                raise ObsError("OBS refused the password.") from None
            raise ObsError("OBS closed the connection while connecting.") from None
        if msg.get("op") != 2:
            raise ObsError("OBS did not accept Relay's connection.")

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    msg = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(msg, dict):
                    continue
                d = msg.get("d") or {}
                if msg.get("op") == 7:
                    fut = self._waiting.pop(str(d.get("requestId")), None)
                    if fut is not None and not fut.done():
                        fut.set_result(d)
                elif msg.get("op") == 5 and self.on_event is not None:
                    try:
                        r = self.on_event(str(d.get("eventType", "")), d.get("eventData") or {})
                        if asyncio.iscoroutine(r):
                            await r
                    except Exception:
                        pass
        except Exception:
            pass
        finally:
            self.closed.set()
            for fut in self._waiting.values():
                if not fut.done():
                    fut.set_exception(ObsError("The connection to OBS closed."))
            self._waiting.clear()

    async def request(self, kind: str, data: dict | None = None, timeout: float = TIMEOUT) -> dict:
        """Send one request and return its responseData. Raises ObsError with a short, page-safe reason."""
        if self.closed.is_set():
            raise ObsError("The connection to OBS closed.")
        rid = str(next(self._ids))
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = fut
        body: dict[str, Any] = {"requestType": kind, "requestId": rid}
        if data:
            body["requestData"] = data
        try:
            await self.ws.send(json.dumps({"op": 6, "d": body}))
            d = await asyncio.wait_for(fut, timeout)
        except ObsError:
            raise
        except asyncio.TimeoutError:
            raise ObsError("OBS did not answer in time.") from None
        except Exception:
            raise ObsError("The connection to OBS closed.") from None
        finally:
            self._waiting.pop(rid, None)
        status = d.get("requestStatus") or {}
        if not status.get("result"):
            raise ObsError(f"OBS refused {kind} (code {int(status.get('code') or 0)}).")
        out = d.get("responseData")
        return out if isinstance(out, dict) else {}

    async def _stop(self) -> None:
        task, self._reader = self._reader, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
