"""The bridge between the local LiveKit rooms and Cloudflare Realtime (#277).

For each channel with a remote member online, the bridge pushes a mixed track of the room to Cloudflare, and pulls
each remote talker's track into the room under that phone's own identity, so the talk arbiter (#274) treats a remote
phone exactly like a local one. The audio itself is the `Media` backend's job (media.py: livekit rtc on one side,
aiortc on the other); this file is the part that decides: whether the link is up, when to try again, which tracks a
phone may pull, and what happens to remote phones when the internet goes.

Only outbound connections. Losing the internet, or switching the module off, never touches local audio: the bridge
holds no local state the phones depend on.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable, Protocol

from . import cloud

log = logging.getLogger("relay.intercom_remote")

BACKOFF_S = (1, 2, 4, 8, 16, 30)       # then every 30 s
WATCH_EVERY_S = 10
LOST_AFTER_FAILS = 2                   # consecutive failed checks before the link counts as lost
STATES = ("off", "connecting", "connected", "reconnecting", "missing")


def backoff(attempt: int) -> int:
    """Seconds to wait after the `attempt`th failure in a row (0 is the first)."""
    return BACKOFF_S[min(max(attempt, 0), len(BACKOFF_S) - 1)]


def track_name(channel_id: int) -> str:
    """The Cloudflare track a channel's mix is pushed under. A phone pulls only these, named by Relay, never by itself."""
    return f"ch{channel_id}"


def mic_name(device_id: int) -> str:
    return f"mic{device_id}"


class Media(Protocol):
    """What the bridge needs from the audio side. media.py implements it; the tests use a fake."""

    def unavailable(self) -> str:
        """A fixed sentence when the libraries it needs are not installed, else ''."""

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def alive(self) -> bool: ...
    async def listen(self, channel_id: int, cf_session_id: str, name: str) -> None: ...
    async def unlisten(self, channel_id: int) -> None: ...
    async def talk(self, channel_id: int, user_id: int, device_id: int, cf_session_id: str, name: str) -> None: ...
    async def untalk(self, channel_id: int, device_id: int) -> None: ...


class Bridge:
    def __init__(self) -> None:
        self.state = "off"
        self.detail = ""
        self.attempt = 0
        self.session_id = ""                 # the bridge's own Cloudflare session: the channel mixes live in it
        self.media: Media | None = None
        self.sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self.on_lost: Callable[[], Awaitable[None]] | None = None      # drop every remote phone
        self.on_state: Callable[[str], None] | None = None
        self.listening: dict[int, int] = {}  # channel -> remote phones listening to it
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.restarts = 0                    # times the link came back, for the live page

    # ------------------------------------------------------------------ state
    def _set(self, state: str, detail: str = "") -> None:
        changed = state != self.state
        self.state, self.detail = state, detail
        if changed and self.on_state is not None:
            try:
                self.on_state(state)
            except Exception:
                log.exception("intercom_remote: state hook failed")

    @property
    def connected(self) -> bool:
        return self.state == "connected"

    # ------------------------------------------------------------------ life
    def begin(self, media: Media) -> None:
        self.media = media
        self._stop = asyncio.Event()
        why = media.unavailable()
        if why:
            self._set("missing", why)
            return
        self._set("connecting", "Connecting to Cloudflare")
        self._task = asyncio.create_task(self._run(), name="intercom-remote-bridge")

    async def end(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self._teardown()
        self.listening.clear()
        self._set("off", "")

    async def _teardown(self) -> None:
        sid, self.session_id = self.session_id, ""
        if self.media is not None:
            with contextlib.suppress(Exception):
                await self.media.close()
        if sid:
            with contextlib.suppress(Exception):
                await cloud.run(cloud.close_session, sid)

    async def _connect(self) -> None:
        assert self.media is not None
        self.session_id = await cloud.run(cloud.open_session)
        await self.media.open()
        for channel_id in list(self.listening):          # phones that were listening before the link dropped
            await self.media.listen(channel_id, self.session_id, track_name(channel_id))

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._connect()
            except asyncio.CancelledError:
                raise
            except cloud.CloudError as exc:
                self._set("reconnecting", exc.message)
            except Exception:
                log.exception("intercom_remote: could not connect the bridge")
                self._set("reconnecting", "Could not connect the bridge to Cloudflare.")
            else:
                if self.attempt:
                    self.restarts += 1
                self.attempt = 0
                self._set("connected", "Connected to Cloudflare")
                await self._watch()
                self._set("reconnecting", "Lost the link to Cloudflare. Trying again.")
                await self._lost()
            await self._teardown()
            wait = backoff(self.attempt)
            self.attempt += 1
            await self.sleep(wait)
            self._set("connecting", "Connecting to Cloudflare")

    async def _watch(self) -> None:
        """Return when the link is lost: Cloudflare unreachable, or the audio side died."""
        fails = 0
        while True:
            await self.sleep(WATCH_EVERY_S)
            ok = await cloud.run(cloud.reachable) and self.media is not None and self.media.alive()
            fails = 0 if ok else fails + 1
            if fails >= LOST_AFTER_FAILS:
                return

    async def _lost(self) -> None:
        if self.on_lost is not None:
            try:
                await self.on_lost()
            except Exception:
                log.exception("intercom_remote: could not drop remote phones")

    # ------------------------------------------------------------------ what remote phones may pull
    async def pull_tracks(self, channels: list[int]) -> list[dict]:
        """The Cloudflare tracks a phone listening to `channels` is told to pull. Starts each channel's mix."""
        if not self.connected or self.media is None:
            raise cloud.CloudError("unexpected")
        out = []
        for channel_id in channels:
            if self.listening.get(channel_id, 0) == 0:
                await self.media.listen(channel_id, self.session_id, track_name(channel_id))
            self.listening[channel_id] = self.listening.get(channel_id, 0) + 1
            out.append({"location": "remote", "trackName": track_name(channel_id),
                        "sessionId": getattr(self.media, "session", "") or self.session_id})
        return out

    async def release(self, channels: list[int]) -> None:
        """A phone stopped listening to `channels`; a mix nobody hears any more stops."""
        for channel_id in channels:
            n = self.listening.get(channel_id, 0) - 1
            if n > 0:
                self.listening[channel_id] = n
                continue
            self.listening.pop(channel_id, None)
            if self.media is not None:
                with contextlib.suppress(Exception):
                    await self.media.unlisten(channel_id)


bridge = Bridge()
