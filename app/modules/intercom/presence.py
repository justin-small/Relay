"""The phones that are connected right now, and the socket they talk to Relay over (#273, #239).

A phone opens `/intercom/ws` and sends its device credential (never a password or a LiveKit key). Relay answers with
what that phone may use, and later with LiveKit tokens for the channels it asks to join. The live set is in memory;
`intercom_sessions` keeps the history. Signing a phone out closes its socket and takes it out of LiveKit at once.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from ... import db, serving
from . import devices, live, rooms, rtcproxy, store
from .arbiter import arbiter

log = logging.getLogger("relay.intercom")

HELLO_WAIT_S = 10
IDLE_S = 90                   # a phone pings every 25 s or so; silence this long is a dead connection
MAX_MESSAGE = 4096
TOKENS_PER_MIN = 30           # per device
BAD_HELLOS_PER_MIN = 10       # per address
CODE_BAD_CREDENTIAL = 4401
CODE_SIGNED_OUT = 4403
CODE_TIMEOUT = 4408
CODE_REPLACED = 4409
CODE_RATE = 4429
CODE_HTTPS = 4426


def _stats(data: dict) -> dict:
    """A phone's receive statistics (packets lost, jitter, round trip), for the watchdog (#276): numbers only."""
    out = {}
    for key in ("packets_lost", "packets_received", "jitter_ms", "rtt_ms", "tracks"):
        v = data.get(key)
        out[key] = v if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e9 else None
    return out


class Remote:
    """How a module that carries phones from outside the building (intercom_remote, #277) joins the same socket.

    `allow(device, user)` says whether this credential may connect here; `handle(conn, data)` answers a message
    of the module's own and returns True when it did; `opened(conn)` and `closed(conn)` run around the connection.
    A remote phone never gets a local LiveKit token: `token` is refused for it."""
    via = "remote"
    max_message = MAX_MESSAGE      # a module whose messages carry more (SDP for remote audio) raises it

    async def allow(self, device: dict, user) -> bool:
        return True

    async def handle(self, conn: "Conn", data: dict) -> bool:
        return False

    async def opened(self, conn: "Conn") -> None:
        pass

    async def closed(self, conn: "Conn") -> None:
        pass


_token_times: dict[int, deque[float]] = {}     # device id -> recent token requests
_token_logged: dict[int, float] = {}


class Conn:
    def __init__(self, ws: WebSocket, device: dict, user, session_id: int, remote: Remote | None = None):
        self.remote = remote
        self.stats: dict = {}
        self.ws, self.device_id, self.user_id, self.name = ws, device["id"], user.id, user.name
        self.session_id = session_id

    def allow_token(self) -> bool:
        """30 token requests a minute per device (#278), counted across reconnects, so a phone cannot reset its
        count by opening a new socket. A refusal is logged once a minute, not per request."""
        now = time.monotonic()
        q = _token_times.setdefault(self.device_id, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= TOKENS_PER_MIN:
            if now - _token_logged.get(self.device_id, -60.0) >= 60:
                _token_logged[self.device_id] = now
                log.warning("intercom: phone %s asked for more than %d tokens a minute; refused",
                            self.device_id, TOKENS_PER_MIN)
            return False
        q.append(now)
        if len(_token_times) > 2000:
            for k in [k for k, v in _token_times.items() if not v or now - v[-1] > 60]:
                _token_times.pop(k, None), _token_logged.pop(k, None)
        return True


class Presence:
    def __init__(self) -> None:
        self.conns: dict[int, Conn] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._bad: dict[str, deque[float]] = {}

    # -------------------------------------------------------------- from other threads
    def run(self, coro) -> None:
        """Run `coro` on the server's loop from any thread (the account hooks are plain functions)."""
        loop = self.loop
        if loop is None or loop.is_closed():
            coro.close()
            return
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        fut.add_done_callback(self._failed)

    @staticmethod
    def _failed(fut) -> None:
        if not fut.cancelled() and fut.exception() is not None:
            log.error("intercom: a background change failed", exc_info=fut.exception())

    # -------------------------------------------------------------- state
    def online(self, device_id: int) -> bool:
        return device_id in self.conns

    def count(self) -> int:
        return len(self.conns)

    async def drop(self, device_id: int, code: int = CODE_SIGNED_OUT, reason: str = "signed_out") -> None:
        conn = self.conns.get(device_id)
        if conn is not None:
            await self._close(conn, code, reason)

    async def drop_user(self, user_id: int) -> None:
        for conn in [c for c in self.conns.values() if c.user_id == user_id]:
            await rtcproxy.drop_device(conn.device_id)
            await self._close(conn, CODE_SIGNED_OUT, "signed_out")

    async def _close(self, conn: Conn, code: int, reason: str) -> None:
        try:
            await conn.ws.close(code=code, reason=reason)
        except Exception:      # already gone
            pass

    # -------------------------------------------------------------- the account and phone hooks
    def user_signed_out(self, user_id: int) -> None:
        """users.signed_out_hooks: a password change or reset, a turned-off or deleted account, sign out everywhere."""
        try:
            devices.revoke_user(user_id, reason="account")
        except db.Unavailable:
            pass
        self.run(self._evict_user(user_id))

    async def _evict_user(self, user_id: int) -> None:
        await self.drop_user(user_id)
        await rooms.remove_user(user_id)

    async def revoked(self, row: dict) -> None:
        """A phone was signed out: close it and take it out of LiveKit, both at once."""
        await asyncio.gather(self.drop(row["id"]), rtcproxy.drop_device(row["id"]), rooms.remove_device(row["id"], row["user_id"]),
                             return_exceptions=True)

    def member_changed(self, user_id: int | None, channel_id: int) -> None:
        """store.member_hooks."""
        self.run(rooms.member_changed(user_id, channel_id))
        self.run(arbiter.member_changed(user_id, channel_id))

    def config_changed(self, users: "set[int] | None") -> None:
        """store.config_hooks: send phones what they may use, within a moment of a change."""
        self.run(self._push_config(users))

    @staticmethod
    def _changed() -> None:
        """Who is connected or what the label says changed: the live page and the top bar pill follow."""
        from .. import modules as registry

        live.notify()
        registry.on_change()

    async def _push_config(self, users: "set[int] | None") -> None:
        label = await store.run(store.production)
        live.label = label
        self._changed()
        cache: dict[int, list] = {}
        for conn in list(self.conns.values()):
            if users is not None and conn.user_id not in users:
                continue
            if conn.user_id not in cache:
                cache[conn.user_id] = await store.run(devices.phone_config, conn.user_id)
            await self._send_to(conn.device_id, {"type": "config", "production": label,
                                                 "channels": cache[conn.user_id]})

    # -------------------------------------------------------------- talk leases (#274)
    def bind(self) -> None:
        """Connect the talk arbiter to the database, the phones' sockets and LiveKit."""
        arbiter.lookup, arbiter.send, arbiter.apply = self._lookup, self._send_to, self._unmute
        arbiter.record, arbiter.changed = self._record, self._activity

    @staticmethod
    async def _lookup(user_id: int, channel_id: int):
        return await store.run(lambda: (devices.rights(user_id, channel_id), store.channel(channel_id)))

    async def _send_to(self, device_id: int, msg: dict) -> None:
        conn = self.conns.get(device_id)
        if conn is not None:
            try:
                await self._send(conn.ws, msg)
            except Exception:      # the phone is going away; its disconnect releases what it held
                pass

    @staticmethod
    async def _unmute(channel_id: int, user_id: int, device_id: int, on: bool) -> None:
        try:
            await rooms.set_talking(channel_id, user_id, device_id, on)
        except rooms.RoomError:
            log.warning("intercom: could not switch a talker in LiveKit")

    @staticmethod
    async def _record(kind: str, user_id: int, device_id: int, channel_id: int | None, detail: dict) -> None:
        def write() -> None:
            with db.transaction() as tx:
                store.log_event(tx, kind, user_id=user_id, device_id=device_id, channel_id=channel_id, detail=detail)
        try:
            await store.run(write)
        except db.Unavailable:
            pass

    def activity(self, channel_id: int) -> dict:
        return {"type": "activity", "channel": channel_id,
                "talkers": [{"user_id": l.user_id, "name": l.name} for l in arbiter.talkers(channel_id)],
                "waiting": len(arbiter.queues.get(channel_id, []))}

    async def _activity(self, channel_id: int) -> None:
        """Tell everyone who listens on the channel who is talking now."""
        live.notify()
        msg = self.activity(channel_id)
        rows = await store.run(store.members, channel_id)
        listeners = {r["user_id"] for r in rows if r["can_listen"] or r["can_talk"]}
        hearing = {r["user_id"] for r in rows if r["can_listen"]}
        msg["listening"] = len({c.user_id for c in self.conns.values() if c.user_id in hearing})
        await asyncio.gather(*(self._send_to(c.device_id, msg) for c in list(self.conns.values())
                               if c.user_id in listeners), return_exceptions=True)

    # -------------------------------------------------------------- the socket
    def _too_many_bad(self, ip: str, add: bool) -> bool:
        now = time.monotonic()
        q = self._bad.setdefault(ip, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if add:
            q.append(now)
        if not q:
            self._bad.pop(ip, None)
        return len(q) >= BAD_HELLOS_PER_MIN

    async def serve(self, ws: WebSocket, remote: Remote | None = None) -> None:
        ip = ws.client.host if ws.client else ""
        await ws.accept()
        if remote is None and not on_operator_port(ws):
            await ws.close(code=CODE_HTTPS, reason="https_only")
            return
        if self._too_many_bad(ip, False):
            await ws.close(code=CODE_RATE, reason="slow_down")
            return
        auth = await self._hello(ws, ip, remote)
        if auth is None:
            return
        device, user, fresh = auth
        old = self.conns.get(device["id"])
        if old is not None:
            await self._close(old, CODE_REPLACED, "replaced")
        try:
            sid = await store.run(self._open, device, user, remote.via if remote else "local")
        except db.Unavailable:
            await ws.close(code=1013, reason="try_again")
            return
        conn = Conn(ws, device, user, sid, remote)
        self.conns[device["id"]] = conn
        self._changed()
        if remote is not None:
            await remote.opened(conn)
        reason = "closed"
        try:
            await self._send(ws, await self._welcome(device, user, fresh))
            reason = await self._loop(conn)
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("intercom: socket failed")
            reason = "error"
        finally:
            if self.conns.get(device["id"]) is conn:
                del self.conns[device["id"]]
                self._changed()
                if remote is not None:       # first: the arbiter may wait on LiveKit, and Cloudflare must not
                    try:
                        await remote.closed(conn)
                    except Exception:
                        log.exception("intercom: remote cleanup failed")
                await arbiter.disconnected(device["id"])
            elif remote is not None:
                try:
                    await remote.closed(conn)
                except Exception:
                    log.exception("intercom: remote cleanup failed")
            try:
                await store.run(self._shut, conn, reason)
            except Exception:
                pass

    async def _hello(self, ws: WebSocket, ip: str, remote: Remote | None = None):
        try:
            msg = await asyncio.wait_for(ws.receive_text(), HELLO_WAIT_S)
            data = json.loads(msg) if len(msg) <= MAX_MESSAGE else None
        except asyncio.TimeoutError:
            await ws.close(code=CODE_TIMEOUT, reason="timeout")
            return None
        except (WebSocketDisconnect, ValueError):
            return None
        got = None
        if isinstance(data, dict) and data.get("type") == "hello":
            try:
                got = await store.run(devices.authenticate, data.get("credential"))
            except db.Unavailable:
                await ws.close(code=1013, reason="try_again")
                return None
        if got is not None and remote is not None and not await remote.allow(got[0], got[1]):
            got = None
        if got is None:
            self._too_many_bad(ip, True)
            await ws.close(code=CODE_BAD_CREDENTIAL, reason="sign_in_again")
            return None
        return got

    async def _welcome(self, device: dict, user, fresh: str | None) -> dict:
        channels = await store.run(devices.phone_config, user.id)
        label = await store.run(lambda: (db.one("select label from intercom_production where org_id = 1") or
                                         {}).get("label", ""))
        out: dict[str, Any] = {
            "type": "welcome", "device": {"id": device["id"], "name": device["name"]}, "org_id": 1,
            "production": label, "user": {"id": user.id, "name": user.name}, "channels": channels,
            "rtc_path": "/intercom/rtc",
            "activity": {c["id"]: self.activity(c["id"]) for c in channels}}
        if fresh:
            out["credential"] = fresh        # the phone replaces what it holds
        return out

    async def _loop(self, conn: Conn) -> str:
        ws = conn.ws
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), IDLE_S)
            except asyncio.TimeoutError:
                await self._close(conn, CODE_TIMEOUT, "timeout")
                return "timeout"
            except (WebSocketDisconnect, RuntimeError):      # RuntimeError: closed from our side meanwhile
                return "closed"
            if len(text) > (MAX_MESSAGE if conn.remote is None else max(MAX_MESSAGE, conn.remote.max_message)):
                await self._send(ws, {"type": "error", "text": "Message too large."})
                continue
            try:
                data = json.loads(text)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                continue
            if await self._handle(conn, data):
                return "slow_down"

    async def _handle(self, conn: Conn, data: dict) -> bool:
        """Answer one message. True when the socket was closed because of it."""
        kind = data.get("type")
        if conn.remote is not None:
            if kind == "token":      # a remote phone reaches LiveKit only through the bridge
                await self._send(conn.ws, {"type": "error", "for": "token", "text": "Not available remotely."})
                return False
            if await conn.remote.handle(conn, data):
                return False
        if kind == "ping":
            await self._send(conn.ws, {"type": "pong"})
        elif kind == "token":
            if not conn.allow_token():
                await self._send(conn.ws, {"type": "error", "text": "Too many requests. Wait a moment."})
                await self._close(conn, CODE_RATE, "slow_down")
                return True
            try:
                got = await rooms.issue(conn.user_id, conn.device_id, conn.name, data.get("channel"))
                await self._send(conn.ws, {"type": "token", **got})
            except rooms.Refusal as exc:
                await self._send(conn.ws, {"type": "error", "for": "token", "text": exc.message})
        elif kind == "choice":
            try:
                await store.run(lambda: devices.set_choice(
                    conn.user_id, data.get("channel"), talk_selected=data.get("talk_selected"),
                    listen_selected=data.get("listen_selected"), volume=data.get("volume", 100)))
                await rooms.apply(conn.user_id, data["channel"])      # Talk selected or cleared: arm or disarm
            except devices.Refused as exc:
                await self._send(conn.ws, {"type": "error", "for": "choice", "text": exc.message})
            except rooms.RoomError:
                pass           # LiveKit is not up; the watch loop settles it
        elif kind == "talk.request":
            await arbiter.request(conn.user_id, conn.device_id, conn.name, data.get("channels"), data.get("seq"),
                                  data.get("latched") is True)
        elif kind == "talk.release":
            await arbiter.release(conn.user_id, conn.device_id, data.get("channels"))
        elif kind == "talk.beat":
            arbiter.beat(conn.device_id)
        elif kind == "stats":
            conn.stats = _stats(data)      # the live page picks it up on its next refresh
        return False

    async def _send(self, ws: WebSocket, msg: dict) -> None:
        await ws.send_text(json.dumps(msg, separators=(",", ":")))

    # -------------------------------------------------------------- history (sync, in a worker thread)
    @staticmethod
    def _open(device: dict, user, via: str = "local") -> int:
        with db.transaction() as tx:
            row = tx.one("insert into intercom_sessions (device_id, user_id, via, started_at) values (?, ?, ?, ?)"
                         " returning id", (device["id"], user.id, via, db.now()))
            store.log_event(tx, "device_connected", user_id=user.id, device_id=device["id"])
            return row["id"]

    @staticmethod
    def _shut(conn: Conn, reason: str) -> None:
        with db.transaction() as tx:
            tx.execute("update intercom_sessions set ended_at = ?, end_reason = ? where id = ?",
                       (db.now(), reason, conn.session_id))
            if tx.one("select 1 from intercom_devices where id = ?", (conn.device_id,)):
                store.log_event(tx, "device_disconnected", user_id=conn.user_id, device_id=conn.device_id,
                                detail={"reason": reason})


def on_operator_port(ws: WebSocket) -> bool:
    """Phones use Relay's HTTPS port. Until ports are published (tests, scripts) nothing is gated by port."""
    if not serving.published():
        return True
    from ... import config

    server = ws.scope.get("server")
    return bool(server) and server[1] == serving.admin_port(config.get()["admin_port"])


presence = Presence()
