"""Lobby announcements (#138, stage 7): Relay steps a ProPresenter announcement playlist along by itself, one slide
every `interval_s` seconds, for lobby screens.

The API says nothing about which playlists are announcement playlists (a playlist has no destination field), so the
operator picks one. Each step is one of two calls, both through `propresenter.Client` so view-only mode refuses them
before a socket is opened:

  * the next cue of the announcement that is up:   GET /v1/announcement/active/{n}/trigger
  * the next item of the playlist:                 GET /v1/playlist/{playlist}/{index}/trigger

Rotation never starts by itself: the operator presses Start (a restart of Relay does not resume it). While it runs,
each tick compares what is on the announcements layer with what Relay last put there:

  * the same: carry on, and step when `interval_s` has passed;
  * a slide of one of the playlist's own presentations, but not the one Relay put up (the operator nudged it):
    Relay carries on from where the operator left it;
  * something else (a presentation or media that is not in the playlist): the rotation PAUSES and writes nothing.
    It resumes by itself once the operator's content is gone (the layer empty for RESUME_S), or when the operator
    puts one of the playlist's own slides up again;
  * the layer empty although Relay had something up (the operator pressed Clear): the rotation pauses and stays
    paused until Resume or Stop, because a deliberate clear should not be undone.

A ProPresenter that drops off and comes back does not end the rotation: nothing is written while it is away, and on
return Relay starts the playlist over if the layer came back empty.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from . import propresenter as pp
from .ppcontrol import Control, control as pp_control

log = logging.getLogger("relay.lobby")

TICK_S = 1.0
GRACE_S = 2.5          # the follower reads the announcement once a second: a step is not judged before it has seen it
RESUME_S = 5.0         # how long the layer must stay empty before a rotation paused by the operator carries on
ROTATE_TYPES = ("presentation", "media")   # headers, placeholders, audio and live video have nothing to show

MESSAGES = {
    "no_playlist": "Pick the announcement playlist first.",
    "not_connected": "ProPresenter is not connected.",
    "empty": "That playlist has nothing Relay can show (presentations and media only).",
    "view_only": "View-only mode is on, so Relay did not change anything in ProPresenter.",
    "failed": "ProPresenter did not take the last step; Relay will try again.",
}


class LobbyError(Exception):
    """A refusal with a fixed sentence that is safe to show."""

    def __init__(self, key: str):
        super().__init__(MESSAGES[key])
        self.key = key


class Lobby:
    def __init__(self, follower: pp.Follower | None = None, control: Control | None = None) -> None:
        self.f = follower or pp.follower
        self.ctl = control or pp_control
        self.clock: Callable[[], float] = time.monotonic     # a test swaps this
        self.running = False
        self.paused = ""                 # "", "operator" (something else is up) or "cleared"
        self.finished = False            # a rotation without loop reached the end
        self.items: list[dict] = []      # the playlist's items Relay shows: {index, name, type, presentation}
        self.playlist = ""
        self.pos = 0                     # position in `items` of what Relay last put up
        self.cue = 0
        self.fired_at = 0.0
        self.empty_since: float | None = None
        self.steps = 0
        self.error = ""
        self._failures = 0
        self._lost = False               # ProPresenter went away while running
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        """Relay is shutting down: stop ticking. Nothing is written to ProPresenter, and nothing is cleared."""
        self.running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Lobby rotation tick failed")
            await asyncio.sleep(TICK_S)

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    # -- settings ----------------------------------------------------------------------------------------
    @staticmethod
    def settings() -> dict[str, Any]:
        return pp.settings().get("lobby") or pp.default_lobby()

    # -- reading what is up ------------------------------------------------------------------------------
    def observed(self) -> dict | None:
        """The announcements layer as the follower last read it, or None before it has read the layers."""
        layers = self.f.data.get("status/layers")
        if not isinstance(layers, dict) or "announcements" not in layers:
            return None
        ann = self.f.announcement
        return {"on": bool(layers.get("announcements")), "uuid": ann["uuid"] if ann else "",
                "cue": self.f.announcement_index, "cues": len(ann.get("cues") or []) if ann else 0}

    def _here(self) -> dict | None:
        return self.items[self.pos] if 0 <= self.pos < len(self.items) else None

    def _matches(self, seen: dict) -> bool:
        item = self._here()
        if item is None or not seen["on"]:
            return False
        if item["type"] == "presentation":
            return seen["uuid"] == item["presentation"] and seen["cue"] == self.cue
        return not seen["uuid"]          # media: the layer is on and no presentation is up on it

    def _find(self, seen: dict) -> int | None:
        """Where in the playlist the slide that is up belongs, if it is one of the playlist's presentations: the
        first match at or after the current position, else the first match."""
        if not seen["uuid"]:
            return None
        hits = [n for n, i in enumerate(self.items) if i["type"] == "presentation" and i["presentation"] == seen["uuid"]]
        after = [n for n in hits if n >= self.pos]
        return (after or hits or [None])[0]

    # -- the operator's buttons --------------------------------------------------------------------------
    async def _load(self, playlist: str) -> None:
        body = await self.ctl.playlist_items(playlist)
        self.items = [{"index": o["index"], "name": o["name"], "type": o["type"], "presentation": o["presentation"]}
                      for o in body["items"] if o["type"] in ROTATE_TYPES and not o["hidden"]
                      and isinstance(o["index"], int)]
        if not self.items:
            raise LobbyError("empty")

    async def start_rotation(self) -> dict:
        """Start (or restart) from the first item. Refused unless ProPresenter is up and the playlist is picked;
        a view-only ProPresenter refuses the first trigger, so nothing is started."""
        async with self._guard():
            playlist = self.settings()["playlist"]
            if not playlist:
                raise LobbyError("no_playlist")
            if not (self.f.up and self.f.client):
                raise LobbyError("not_connected")
            self.running = False
            await self._load(playlist)
            self.playlist = playlist
            await self._put_up(0, self.clock())
            self.running, self.paused, self.finished, self.error = True, "", False, ""
            self.empty_since, self._failures, self._lost, self.steps = None, 0, False, 0
            log.info("Lobby rotation started (%d item(s), %.1f s a slide)", len(self.items), self.settings()["interval_s"])
        return self.state()

    async def stop_rotation(self, clear: bool = False) -> dict:
        """Stop stepping. With `clear`, also clear the announcements layer (a write: view-only refuses it)."""
        async with self._guard():
            was = self.running
            self.running, self.paused, self.finished, self.error = False, "", False, ""
            if clear:
                await self.ctl.clear("announcements")
            if was:
                log.info("Lobby rotation stopped%s", " and announcements cleared" if clear else "")
        return self.state()

    async def resume(self) -> dict:
        """Carry on from the top after a pause, on the operator's say-so."""
        if not self.running:
            return await self.start_rotation()
        async with self._guard():
            if not (self.f.up and self.f.client):
                raise LobbyError("not_connected")
            await self._load(self.playlist)
            await self._put_up(0, self.clock())
            self.paused, self.empty_since, self.error = "", None, ""
        return self.state()

    # -- one step ----------------------------------------------------------------------------------------
    async def _put_up(self, pos: int, now: float) -> None:
        """Trigger the playlist item at `pos` (its first slide) and note it. Raises ProError."""
        item = self.items[pos]
        await self.ctl.trigger_playlist_item(self.playlist, item["index"])
        self.pos, self.cue, self.fired_at = pos, 0, now

    async def _advance(self, now: float, seen: dict) -> None:
        item = self._here()
        if item and item["type"] == "presentation" and self.cue + 1 < seen["cues"]:
            await self.ctl.trigger_announcement_cue(self.cue + 1)
            self.cue, self.fired_at = self.cue + 1, now
            self.steps += 1
            return
        nxt = self.pos + 1
        if nxt >= len(self.items):
            if not self.settings()["loop"]:
                self.running, self.finished = False, True     # the last slide stays up
                log.info("Lobby rotation finished")
                return
            await self._load(self.playlist)                    # the playlist may have been edited since
            nxt = 0
        await self._put_up(nxt, now)
        self.steps += 1

    async def tick(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if not self.running:
            return
        async with self._guard():
            if not self.running:
                return
            s = self.settings()
            if s["playlist"] != self.playlist:     # the operator picked another playlist: that ends this rotation
                self.running = False
                log.info("Lobby rotation stopped: another playlist was picked")
                return
            if not pp.configured():                # ProPresenter was switched off in admin: that ends it
                self.running = False
                log.info("Lobby rotation stopped: ProPresenter was switched off")
                return
            if not (self.f.up and self.f.client):
                self._lost = True                  # write nothing while ProPresenter is away
                return
            seen = self.observed()
            if seen is None:                       # connected, but the layers have not been read yet
                return
            try:
                await self._tick_up(now, seen, s)
            except pp.ViewOnly:
                self.running, self.error = False, MESSAGES["view_only"]
                log.warning("Lobby rotation stopped: view-only mode is on")
            except pp.ProError as exc:
                self._failures += 1
                self.error = MESSAGES["failed"]
                if self._failures in (1, 10):
                    log.warning("Lobby rotation step failed: %s", exc)
            except LobbyError as exc:
                self.running, self.error = False, str(exc)

    async def _tick_up(self, now: float, seen: dict, s: dict) -> None:
        if self._lost:
            # ProPresenter came back. If the layer is empty it was probably restarted: start the playlist over.
            self._lost = False
            if not seen["on"] and not self.paused:
                await self._load(self.playlist)
                await self._put_up(0, now)
                self._failures, self.error = 0, ""
                return
        if self.paused == "cleared":
            return
        if self.paused == "operator":
            if seen["on"] and self._find(seen) is not None:         # the operator is back in the playlist
                self.pos, self.cue, self.fired_at, self.paused, self.empty_since = self._find(seen), seen["cue"] or 0, now, "", None
            elif not seen["on"]:
                self.empty_since = self.empty_since if self.empty_since is not None else now
                if now - self.empty_since >= RESUME_S:              # the operator's content is gone: from the top
                    await self._load(self.playlist)
                    await self._put_up(0, now)
                    self.paused, self.empty_since, self.error = "", None, ""
                return
            else:
                self.empty_since = None
                return
        if now - self.fired_at >= GRACE_S and not self._matches(seen):
            if not seen["on"]:
                self.paused = "cleared"
                log.info("Lobby rotation paused: announcements were cleared")
                return
            at = self._find(seen)
            if at is None:
                self.paused, self.empty_since = "operator", None
                log.info("Lobby rotation paused: something else is on the announcements layer")
                return
            self.pos, self.cue, self.fired_at = at, seen["cue"] or 0, now      # the operator nudged it: carry on from there
        if now - self.fired_at >= float(s["interval_s"]):
            await self._advance(now, seen)
            self._failures, self.error = 0, ""

    # -- what the admin page shows -----------------------------------------------------------------------
    def state(self) -> dict:
        s = self.settings()
        item = self._here() if self.running or self.finished else None
        nxt = None
        if self.running and not self.paused:
            nxt = max(0.0, round(float(s["interval_s"]) - (self.clock() - self.fired_at), 1))
        view_only = bool(pp.settings()["view_only"])
        return {"running": self.running, "paused": self.paused, "finished": self.finished,
                "waiting": bool(self.running and not self.f.up),
                "playlist": s["playlist"], "interval_s": s["interval_s"], "loop": s["loop"],
                "item": item["name"] if item else "", "item_number": self.pos + 1 if item else None,
                "item_count": len(self.items) if item else 0, "cue": self.cue + 1 if item else None,
                "next_in_s": nxt, "steps": self.steps, "error": self.error,
                "view_only": view_only, "connected": bool(self.f.up)}


lobby = Lobby()
