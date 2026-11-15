"""The audio side of the bridge (#277): LiveKit's rtc SDK on the local side, aiortc on Cloudflare's.

Listening: for each channel a remote phone hears, a bridge participant joins the channel's LiveKit room (subscribe only,
under an identity the talk arbiter ignores), mixes every audio track in it into one 48 kHz mono stream and pushes that
to Cloudflare as the track `ch<id>`; phones pull it. Talking: a remote phone's microphone track is pulled from the
phone's Cloudflare session and published into the room under that phone's own identity (`u<user>-d<device>`), so the
arbiter (#274) arms, unmutes and mutes it exactly as for a local phone.

Both libraries are imported only when used: a missing one is reported by `unavailable()` and shows as the bridge's
"missing" state. Everything here is outbound; closing it never touches a local phone's connection.

Known limits: one mix per channel means a remote talker also hears the room's mix including themselves (a little
echo), and the pure audio helpers (`Mixer`, `to_mono`) are tested while the SDK calls need real sessions.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import time
from collections import deque
from fractions import Fraction
from typing import Any

import numpy as np

log = logging.getLogger("relay.intercom_remote")

RATE = 48_000
FRAME = RATE // 50                      # 20 ms, what Opus and aiortc work in
MAX_BUFFER = RATE // 2                  # a source that gets 500 ms behind drops its oldest audio
PUBLISH_TRIES = 15                      # the arbiter arms a new participant when LiveKit's webhook arrives
PUBLISH_WAIT_S = 0.2
CONNECT_TRIES = 50                      # 10 s for the bridge's own connection to Cloudflare
CONNECT_WAIT_S = 0.2
TOKEN_TTL_S = 3600
LIBS = ("livekit", "aiortc", "av")


def to_mono(data: Any, channels: int, planar: bool = False) -> np.ndarray:
    """Interleaved (or planar) int16 samples of any channel count as one mono int16 array."""
    a = np.asarray(data, dtype=np.int16)
    if channels <= 1:
        return a.reshape(-1)
    a = a.reshape(channels, -1) if planar else a.reshape(-1, channels).T
    return a.astype(np.int32).mean(axis=0).astype(np.int16)


class Mixer:
    """Sums any number of mono int16 sources into 20 ms frames. A source with nothing queued adds silence."""

    def __init__(self) -> None:
        self.q: dict[Any, deque[np.ndarray]] = {}
        self.n: dict[Any, int] = {}

    def add(self, key: Any, samples: np.ndarray) -> None:
        q = self.q.setdefault(key, deque())
        q.append(samples)
        self.n[key] = self.n.get(key, 0) + len(samples)
        while self.n[key] > MAX_BUFFER and len(q) > 1:
            self.n[key] -= len(q.popleft())

    def drop(self, key: Any) -> None:
        self.q.pop(key, None)
        self.n.pop(key, None)

    def _take(self, key: Any) -> np.ndarray:
        q, need, parts = self.q[key], FRAME, []
        while need and q:
            head = q.popleft()
            if len(head) > need:
                q.appendleft(head[need:])
                head = head[:need]
            parts.append(head)
            need -= len(head)
        got = sum(len(p) for p in parts)
        self.n[key] -= got
        out = np.concatenate(parts) if parts else np.zeros(0, np.int16)
        return np.pad(out, (0, FRAME - len(out))) if len(out) < FRAME else out

    def pull(self) -> np.ndarray:
        total = np.zeros(FRAME, np.int32)
        for key in list(self.q):
            total += self._take(key)
        return np.clip(total, -32768, 32767).astype(np.int16)


def _mix_track(mixer: Mixer):
    """An aiortc audio track that plays a Mixer in real time. Built on demand so aiortc stays an optional import."""
    import av
    from aiortc import MediaStreamTrack

    class MixTrack(MediaStreamTrack):
        kind = "audio"

        def __init__(self) -> None:
            super().__init__()
            self.start: float | None = None
            self.ts = 0

        async def recv(self):
            if self.readyState != "live":
                raise ConnectionError("track ended")
            if self.start is None:
                self.start = time.monotonic()
            else:
                wait = self.start + self.ts / RATE - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
            frame = av.AudioFrame.from_ndarray(mixer.pull().reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate, frame.pts, frame.time_base = RATE, self.ts, Fraction(1, RATE)
            self.ts += FRAME
            return frame

    return MixTrack()


def _frame_to_mono(frame) -> np.ndarray:
    planar = frame.format.is_planar
    return to_mono(frame.to_ndarray(), len(frame.layout.channels), planar)


class _Listen:
    """One channel's mix: a room, a mixer and the track pushed to Cloudflare."""

    def __init__(self, room, mixer: Mixer, track) -> None:
        self.room, self.mixer, self.track = room, mixer, track
        self.mid = ""
        self.readers: dict[str, asyncio.Task] = {}


class _Mic:
    """One remote phone's microphone, pulled from Cloudflare and fanned out to every room it may talk in."""

    def __init__(self, mid: str, track) -> None:
        self.mid, self.track = mid, track
        self.sources: dict[int, Any] = {}      # channel -> livekit AudioSource
        self.rooms: dict[int, Any] = {}
        self.task: asyncio.Task | None = None


class LiveMedia:
    def __init__(self) -> None:
        self.pc = None
        self.listens: dict[int, _Listen] = {}
        self.mics: dict[int, _Mic] = {}
        self._neg = asyncio.Lock()             # one offer/answer on the Cloudflare connection at a time
        self._closed = False

    # ------------------------------------------------------------------ life
    def unavailable(self) -> str:
        missing = [n for n in LIBS if importlib.util.find_spec(n) is None]
        if missing:
            return "The audio bridge needs these Python packages: " + ", ".join(missing) + "."
        return ""

    async def open(self) -> None:
        from aiortc import RTCPeerConnection

        self._closed = False
        self.pc = RTCPeerConnection()

    async def close(self) -> None:
        self._closed = True
        for channel_id in list(self.listens):
            await self._stop_listen(channel_id)
        for device_id in list(self.mics):
            await self._stop_mic(device_id)
        pc, self.pc = self.pc, None
        if pc is not None:
            with contextlib.suppress(Exception):
                await pc.close()

    def alive(self) -> bool:
        if self._closed or self.pc is None or self.pc.connectionState in ("failed", "closed"):
            return False
        return all(r.room.isconnected() for r in self.listens.values()) and not any(
            m.task is not None and m.task.done() for m in self.mics.values())

    # ------------------------------------------------------------------ the LiveKit side
    @staticmethod
    def _bridge_token(channel_id: int) -> str:
        from ..intercom import livekit as lk, rooms

        key, secret = lk.keys()
        now = int(time.time())
        return rooms.sign({
            "iss": key, "sub": f"bridge-{int(channel_id)}", "name": "Remote bridge", "nbf": now - 5,
            "exp": now + TOKEN_TTL_S,
            "video": {"roomJoin": True, "room": rooms.room_name(channel_id), "canSubscribe": True,
                      "canPublish": False, "canPublishData": False, "hidden": True}}, secret)

    # ------------------------------------------------------------------ the Cloudflare side
    async def _push(self, track, name: str) -> str:
        """Offer a local track on the bridge's Cloudflare session. Returns its mid."""
        from aiortc import RTCSessionDescription

        from . import cloud
        from .bridge import bridge

        async with self._neg:
            t = self.pc.addTransceiver(track, direction="sendonly")
            await self.pc.setLocalDescription(await self.pc.createOffer())
            offer = self.pc.localDescription
            out = await cloud.run(cloud.tracks_new, bridge.session_id, {
                "sessionDescription": {"type": "offer", "sdp": offer.sdp},
                "tracks": [{"location": "local", "trackName": name, "mid": t.mid}]})
            answer = out.get("sessionDescription") or {}
            await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type="answer"))
            return t.mid

    async def _connected(self) -> None:
        for _ in range(CONNECT_TRIES):
            if self.pc is not None and self.pc.connectionState == "connected":
                return
            if self.pc is None or self.pc.connectionState in ("failed", "closed"):
                break
            await asyncio.sleep(CONNECT_WAIT_S)
        raise ConnectionError("The audio bridge could not connect to Cloudflare.")

    async def _pull(self, cf_session_id: str, name: str):
        """Pull another session's track into the bridge's session. Returns (mid, aiortc track)."""
        from aiortc import RTCSessionDescription

        from . import cloud
        from .bridge import bridge

        async with self._neg:
            out = await cloud.run(cloud.tracks_new, bridge.session_id, {"tracks": [
                {"location": "remote", "trackName": name, "sessionId": cf_session_id}]})
            got = [t for t in out.get("tracks", []) if t.get("trackName") == name and t.get("mid")]
            if not got:
                log.warning("intercom_remote: Cloudflare did not give a track for %s: %r", name, out.get("tracks"))
                raise cloud.CloudError("unexpected")
            mid = got[0]["mid"]
            offer = out.get("sessionDescription")
            if offer:
                await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer["sdp"], type="offer"))
                await self.pc.setLocalDescription(await self.pc.createAnswer())
                await cloud.run(cloud.renegotiate, bridge.session_id, {
                    "sessionDescription": {"type": "answer", "sdp": self.pc.localDescription.sdp}})
            for t in self.pc.getTransceivers():
                if t.mid == mid and t.receiver.track is not None:
                    return mid, t.receiver.track
            raise cloud.CloudError("unexpected")

    async def _close_cf_track(self, mid: str) -> None:
        from . import cloud
        from .bridge import bridge

        if mid and bridge.session_id:
            with contextlib.suppress(Exception):
                await cloud.run(cloud.tracks_close, bridge.session_id, {"tracks": [{"mid": mid}], "force": True})

    # ------------------------------------------------------------------ listening
    async def listen(self, channel_id: int, cf_session_id: str, name: str) -> None:
        from livekit import rtc

        if channel_id in self.listens:
            return
        mixer = Mixer()
        room = rtc.Room()
        entry = _Listen(room, mixer, _mix_track(mixer))
        self.listens[channel_id] = entry

        def on_subscribed(track, _pub, participant):
            if track.kind != rtc.TrackKind.KIND_AUDIO:
                return
            key = f"{participant.identity}/{track.sid}"
            entry.readers[key] = asyncio.create_task(self._read(entry, key, track))

        def on_unsubscribed(track, _pub, participant):
            key = f"{participant.identity}/{track.sid}"
            task = entry.readers.pop(key, None)
            if task is not None:
                task.cancel()
            mixer.drop(key)

        room.on("track_subscribed", on_subscribed)
        room.on("track_unsubscribed", on_unsubscribed)
        try:
            from ..intercom import livekit as lk, rooms

            await rooms.ensure_room(channel_id)          # rooms are not made on demand
            await room.connect(f"ws://127.0.0.1:{lk.SIGNAL_PORT}", self._bridge_token(channel_id))
            entry.mid = await self._push(entry.track, name)
            await self._connected()       # a phone can pull the mix only once Cloudflare has media flowing
        except BaseException:
            await self._stop_listen(channel_id)
            raise

    async def _read(self, entry: _Listen, key: str, track) -> None:
        from livekit import rtc

        stream = rtc.AudioStream(track, sample_rate=RATE, num_channels=1)
        try:
            async for ev in stream:
                entry.mixer.add(key, np.frombuffer(ev.frame.data, dtype=np.int16).copy())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("intercom_remote: reading a room's audio failed")
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()

    async def unlisten(self, channel_id: int) -> None:
        await self._stop_listen(channel_id)

    async def _stop_listen(self, channel_id: int) -> None:
        entry = self.listens.pop(channel_id, None)
        if entry is None:
            return
        for task in entry.readers.values():
            task.cancel()
        with contextlib.suppress(Exception):
            entry.track.stop()
        with contextlib.suppress(Exception):
            await entry.room.disconnect()
        await self._close_cf_track(entry.mid)

    # ------------------------------------------------------------------ talking
    async def talk(self, channel_id: int, user_id: int, device_id: int, cf_session_id: str, name: str) -> None:
        from livekit import rtc

        from ..intercom import livekit as lk, rooms

        mic = self.mics.get(device_id)
        if mic is None:
            mid, track = await self._pull(cf_session_id, name)
            mic = self.mics[device_id] = _Mic(mid, track)
        if channel_id in mic.sources:
            return
        key, secret = lk.keys()
        token = rooms.mint_token(key, secret, channel_id=channel_id, user_id=user_id, device_id=device_id,
                                 name="Remote", can_subscribe=False)
        room = rtc.Room()
        await rooms.ensure_room(channel_id)
        await room.connect(f"ws://127.0.0.1:{lk.SIGNAL_PORT}", token)
        source = rtc.AudioSource(RATE, 1)
        local = rtc.LocalAudioTrack.create_audio_track(name, source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        for attempt in range(PUBLISH_TRIES):              # refused until the arbiter has armed this participant
            try:
                await room.local_participant.publish_track(local, options)
                break
            except Exception:
                if attempt == PUBLISH_TRIES - 1:
                    await room.disconnect()
                    raise
                await asyncio.sleep(PUBLISH_WAIT_S)
        mic.sources[channel_id], mic.rooms[channel_id] = source, room
        if mic.task is None:
            mic.task = asyncio.create_task(self._pump(mic), name=f"intercom-remote-mic-{device_id}")

    async def _pump(self, mic: _Mic) -> None:
        """Move one phone's microphone from Cloudflare into every room it is talking in."""
        from livekit import rtc

        while True:
            frame = await mic.track.recv()
            pcm = _frame_to_mono(frame)
            if frame.sample_rate != RATE:
                raise RuntimeError("unexpected sample rate")
            for source in list(mic.sources.values()):
                await source.capture_frame(rtc.AudioFrame(pcm.tobytes(), RATE, 1, len(pcm)))

    async def untalk(self, channel_id: int, device_id: int) -> None:
        mic = self.mics.get(device_id)
        if mic is None:
            return
        mic.sources.pop(channel_id, None)
        room = mic.rooms.pop(channel_id, None)
        if room is not None:
            with contextlib.suppress(Exception):
                await room.disconnect()
        if not mic.sources:
            await self._stop_mic(device_id)

    async def _stop_mic(self, device_id: int) -> None:
        mic = self.mics.pop(device_id, None)
        if mic is None:
            return
        if mic.task is not None:
            mic.task.cancel()
        for room in list(mic.rooms.values()):
            with contextlib.suppress(Exception):
                await room.disconnect()
        mic.sources.clear(), mic.rooms.clear()
        await self._close_cf_track(mic.mid)


class NoMedia:
    """Stands in when the audio libraries are not installed: reports why, moves nothing."""

    def __init__(self, why: str) -> None:
        self.why = why

    def unavailable(self) -> str:
        return self.why

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def alive(self) -> bool:
        return False

    async def listen(self, channel_id, cf_session_id, name) -> None: ...
    async def unlisten(self, channel_id) -> None: ...
    async def talk(self, channel_id, user_id, device_id, cf_session_id, name) -> None: ...
    async def untalk(self, channel_id, device_id) -> None: ...


def load():
    live = LiveMedia()
    why = live.unavailable()
    return NoMedia(why) if why else live
