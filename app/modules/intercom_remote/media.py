"""The audio side of the bridge (#277): LiveKit's rtc SDK on the local side, aiortc on Cloudflare's.

NOT WRITTEN YET. This is the one part of the module that has to move audio frames between two WebRTC stacks, and it
cannot be built or checked without real LiveKit and Cloudflare Realtime sessions and the two libraries installed;
nothing here pretends otherwise. Until it exists the module reports "missing" and no remote phone can connect.
`bridge.Media` is exactly what it must provide.
"""
from __future__ import annotations


class NoMedia:
    def unavailable(self) -> str:
        return "The audio bridge is not installed in this version of Relay."

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    def alive(self) -> bool:
        return False

    async def listen(self, channel_id, cf_session_id, name) -> None: ...
    async def unlisten(self, channel_id) -> None: ...
    async def talk(self, channel_id, user_id, device_id, cf_session_id, name) -> None: ...
    async def untalk(self, channel_id, device_id) -> None: ...


def load() -> NoMedia:
    return NoMedia()
