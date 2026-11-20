"""The Cloudflare bridge's card on the intercom's Health page (#276), and its `bridge_state` log rows.

The intercom's watchdog asks every 5 s; Relay's cloud is asked whether it answers at most every 30 s, so the check
stays well inside relay-cloud's limit of about 30 calls a minute per install. Nothing here touches local audio.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..intercom import watchdog as wd
from . import cloud
from .bridge import bridge

REACH_EVERY_S = 30.0


class BridgeCheck:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.down_since: float | None = None
        self.reachable: bool | None = None
        self._asked = -REACH_EVERY_S
        self._tasks: set[asyncio.Task] = set()
        self.probe: Callable[[], bool] = cloud.reachable

    def state_changed(self, state: str) -> None:
        """Bridge._set's hook: note when the link went down, and log the change."""
        if state == "connected" or state == "off":
            self.down_since = None
        elif self.down_since is None:
            self.down_since = self.clock()
        try:
            task = asyncio.get_running_loop().create_task(wd.record("bridge_state", detail={"state": state}))
        except RuntimeError:
            return              # no loop (a test calling it bare): nothing to log to
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def __call__(self, t: dict) -> wd.Card:
        now = self.clock()
        if bridge.state != "off" and now - self._asked >= REACH_EVERY_S:
            self._asked = now
            try:
                self.reachable = bool(await cloud.run(self.probe))
            except Exception:
                self.reachable = False
        if bridge.state not in ("connected", "off") and self.down_since is None:
            self.down_since = now
        down = 0.0 if self.down_since is None else now - self.down_since
        return wd.eval_bridge(t, bridge.state, bridge.detail, down, self.reachable)


check = BridgeCheck()


def register() -> None:
    bridge.on_state = check.state_changed
    wd.sources["bridge"] = check


def unregister() -> None:
    if bridge.on_state == check.state_changed:
        bridge.on_state = None
    wd.sources.pop("bridge", None)
    check.down_since = check.reachable = None
