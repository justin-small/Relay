"""A remote phone's connection (#277): the intercom's own socket, plus what only a remote phone needs.

A remote phone signs in with its Relay account through the install's remote address, gets a credential of its own
(never a local phone's), and opens `/intercom/remote/ws`. From then on it is a phone like any other for channels,
choices and talk leases (the same presence and arbiter), with three differences: it must be remote allowed, it never
gets a LiveKit token, and its audio goes to and from Cloudflare Realtime, with Relay telling it which tracks to pull.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import WebSocket

from ..intercom import devices, store as istore
from ..intercom.presence import Conn, Remote, presence
from . import cloud, store
from .bridge import bridge, mic_name

log = logging.getLogger("relay.intercom_remote")

CODE_LINK_LOST = 4503           # the bridge is down: the phone shows "Lost the link to the church" and retries
LINK_LOST_TEXT = "Lost the link to the church"
MAX_SDP = 12_000
TALK_TRIES = 3                  # each try is a call to the cloud, which limits an install to about 30 a minute
TALK_WAIT_S = 2.0


def _sdp(v: Any) -> dict | None:
    if not isinstance(v, dict) or v.get("type") not in ("offer", "answer"):
        return None
    sdp = v.get("sdp")
    return {"type": v["type"], "sdp": sdp} if isinstance(sdp, str) and 0 < len(sdp) <= MAX_SDP else None


class State:
    """What one remote phone holds in Cloudflare."""

    def __init__(self, session_id: int) -> None:
        self.row = session_id
        self.cf = ""                       # the phone's own Cloudflare session
        self.listening: list[int] = []
        self.talking: list[int] = []
        self.pushed = False                # it offered its microphone on its session...
        self.up = False                    # ...and said its connection to Cloudflare is up
        self.lock = asyncio.Lock()
        self.starting: asyncio.Task | None = None    # bringing the microphone into the rooms, once the phone is connected


class RemoteCrew(Remote):
    # Session descriptions ride on this socket; with TURN a phone's answer lists several relay candidates and passes
    # the intercom's usual 4 KB. Each one is still checked against MAX_SDP.
    max_message = MAX_SDP + 1024

    def __init__(self) -> None:
        self.states: dict[int, State] = {}         # device id -> state
        self._tasks: set[asyncio.Task] = set()

    def unavailable(self) -> int | None:
        # The bridge reconnecting is not a bad sign-in: answering 4401 made the phone throw its credential away.
        return None if bridge.connected else CODE_LINK_LOST

    async def allow(self, device: dict, user) -> bool:
        if not bridge.connected:
            return False
        ok = await istore.run(lambda: store.is_remote_device(device["id"]) and store.allowed(user.id))
        return bool(ok and user.can("intercom.use"))

    async def opened(self, conn: Conn) -> None:
        row = await istore.run(store.open_session, conn.device_id, conn.user_id)
        self.states[conn.device_id] = State(row)

    async def closed(self, conn: Conn) -> None:
        state = self.states.pop(conn.device_id, None)
        if state is None:
            return
        # As its own task, started before any await: a connection that was cancelled must still record its end and
        # give its Cloudflare session back.
        task = asyncio.create_task(self._finish(conn, state), name="intercom-remote-release")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish(self, conn: Conn, state: State) -> None:
        reason = getattr(conn, "end_reason", "closed")
        try:        # the record first: it is quick, and the calls to Cloudflare below can take a while
            await istore.run(store.close_session, state.row, conn.device_id, conn.user_id, reason)
        except Exception:
            log.exception("intercom_remote: could not record the end of a session")
        await self._release(conn, state)

    async def _start_talk(self, conn: Conn, state: State) -> None:
        for attempt in range(TALK_TRIES):
            if attempt:
                await asyncio.sleep(TALK_WAIT_S)
            if bridge.media is None or not state.cf:
                return
            try:
                for channel_id in state.talking:
                    await bridge.media.talk(channel_id, conn.user_id, conn.device_id, state.cf, mic_name(conn.device_id))
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("intercom_remote: remote microphone not in the rooms yet (try %d): %r", attempt + 1, exc)
                if attempt == TALK_TRIES - 1:
                    log.exception("intercom_remote: could not bring a remote microphone into the rooms")

    async def _release(self, conn: Conn, state: State) -> None:
        if state.starting is not None:
            state.starting.cancel()
        await bridge.release(state.listening)
        if bridge.media is not None:
            for channel_id in state.talking:
                with contextlib.suppress(Exception):
                    await bridge.media.untalk(channel_id, conn.device_id)
        state.listening, state.talking = [], []
        cf, state.cf = state.cf, ""
        if cf and bridge.connected:
            with contextlib.suppress(Exception):
                await cloud.run(cloud.close_session, cf)

    async def drop_all(self, reason: str = "link_lost") -> None:
        """The internet went: every remote phone is told and disconnected. Local phones are not touched."""
        for device_id in list(self.states):
            conn = presence.conns.get(device_id)
            if conn is None:
                continue
            conn.end_reason = reason
            with contextlib.suppress(Exception):
                await conn.ws.send_text('{"type":"link","state":"lost","text":"%s"}' % LINK_LOST_TEXT)
            await presence.drop(device_id, CODE_LINK_LOST, reason)

    # ------------------------------------------------------------------ messages only a remote phone sends
    async def handle(self, conn: Conn, data: dict) -> bool:
        kind = data.get("type")
        if not isinstance(kind, str) or not kind.startswith("cf."):
            return False
        state = self.states.get(conn.device_id)
        if state is None:
            return True
        async with state.lock:
            try:
                reply = await self._cf(conn, state, kind, data)
            except cloud.CloudError as exc:
                log.warning("intercom_remote: %s refused by the cloud: %s (%s)", kind, exc.kind, exc.status)
                reply = {"type": "error", "for": kind, "text": exc.message}
            except devices.Refused as exc:
                reply = {"type": "error", "for": kind, "text": exc.message}
            except Exception:       # LiveKit down or an audio-side failure: the phone is told, its socket stays up
                log.exception("intercom_remote: %s failed", kind)
                reply = {"type": "error", "for": kind, "text": "The church's intercom could not do that just now."}
        await presence._send(conn.ws, reply)
        return True

    async def _cf(self, conn: Conn, state: State, kind: str, data: dict) -> dict:
        if not bridge.connected:
            raise cloud.CloudError("unexpected")
        if kind == "cf.open":
            if not state.cf:
                state.cf = await cloud.run(cloud.open_session)
            return {"type": "cf.session", "sessionId": state.cf, "iceServers": await cloud.run(cloud.ice_servers)}
        if not state.cf:
            return {"type": "error", "for": kind, "text": "Open a session first."}
        if kind == "cf.pull":
            if state.pushed and not state.up:
                # Cloudflare hangs, then refuses, a pull into a session whose connection is not up yet.
                return {"type": "error", "for": kind, "text": "Audio is still connecting. Try again."}
            # Relay picks the tracks: the phone's own listening channels, nothing it names itself.
            rows = await istore.run(devices.phone_config, conn.user_id)
            want = [r["id"] for r in rows if r["can_listen"] and r["listen_selected"]]
            await bridge.release(state.listening)
            state.listening = []
            if not want:
                return {"type": "cf.pulled", "tracks": [], "sessionDescription": None}
            log.info("intercom_remote: phone %s pulls channels %s", conn.device_id, want)
            tracks = await bridge.pull_tracks(want)
            log.info("intercom_remote: mixes ready for %s, asking Cloudflare", want)
            state.listening = want
            out = await cloud.run(cloud.tracks_new, state.cf, {"tracks": tracks})
            return {"type": "cf.pulled", "channels": want, "sessionDescription": out.get("sessionDescription"),
                    "requiresImmediateRenegotiation": bool(out.get("requiresImmediateRenegotiation"))}
        if kind == "cf.push":
            offer = _sdp(data.get("sessionDescription"))
            mid = data.get("mid")
            if offer is None or not isinstance(mid, str) or not 0 < len(mid) <= 32:
                return {"type": "error", "for": kind, "text": "That offer is not valid."}
            log.info("intercom_remote: phone %s offer: %d bytes, media %s, user agent %s", conn.device_id, len(offer["sdp"]),
                     [l for l in offer["sdp"].splitlines() if l.startswith(("m=", "a=group", "a=sendonly", "a=sendrecv", "a=recvonly"))][:6],
                     getattr(conn.ws, "headers", {}).get("user-agent", "")[:120])
            out = await cloud.run(cloud.tracks_new, state.cf, {
                "sessionDescription": offer,
                "tracks": [{"location": "local", "trackName": mic_name(conn.device_id), "mid": mid}]})
            state.pushed, state.up = True, False
            rows = await istore.run(devices.phone_config, conn.user_id)
            state.talking = [r["id"] for r in rows if r["can_talk"] and r["talk_selected"]]
            return {"type": "cf.pushed", "sessionDescription": out.get("sessionDescription")}
        if kind == "cf.connected":
            # The phone's own connection to Cloudflare is up, so its microphone is a live track the bridge can pull
            # (asked any earlier, Cloudflare waits for it and then refuses). Bring it into the rooms in the background.
            state.up = True
            if state.talking and bridge.media is not None:
                if state.starting is not None:
                    state.starting.cancel()
                state.starting = asyncio.create_task(self._start_talk(conn, state), name="intercom-remote-talk")
            return {"type": "cf.ack"}
        if kind == "cf.diag":
            # Why a phone's audio did not connect, for the log only. Short, plain fields; nothing is acted on.
            def clip(v: Any) -> Any:
                if isinstance(v, list):
                    return [str(x)[:200] for x in v[:20]]
                return str(v)[:40]
            log.warning("intercom_remote: phone %s audio did not connect: %s", conn.device_id, {
                k: clip(data.get(k)) for k in ("connection", "ice", "gathering", "signaling", "servers", "errors", "local", "pairs", "remote")})
            return {"type": "cf.ack"}
        if kind == "cf.renegotiate":
            answer = _sdp(data.get("sessionDescription"))
            if answer is None:
                return {"type": "error", "for": kind, "text": "That answer is not valid."}
            await cloud.run(cloud.renegotiate, state.cf, {"sessionDescription": answer})
            return {"type": "cf.renegotiated"}
        return {"type": "error", "for": kind, "text": "Unknown request."}

    def view(self) -> dict:
        return {"connected": len(self.states)}


crew = RemoteCrew()


async def serve(ws: WebSocket) -> None:
    await presence.serve(ws, crew)


async def drop_user(user_id: int, reason: str = "signed_out") -> None:
    for device_id, conn in list(presence.conns.items()):
        if conn.user_id == user_id and device_id in crew.states:
            conn.end_reason = reason
            await presence.drop(device_id, 4403, reason)
