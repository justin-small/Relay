"""Announcement slides as signage items (#208): the slides of one ProPresenter playlist, kept as pictures in the
signage media library and as one signage playlist, "ProPresenter announcements".

The operator picks the playlist (ProPresenter's API cannot say which one holds announcements). Relay reads each
presentation's slides in the playlist's own arrangement, fetches a large thumbnail of each from ProPresenter, stores
it in the media library under a name that starts with `PREFIX`, and replaces that signage playlist's items with them.
A slide whose picture has not changed keeps its library row, and an item keeps its duration, dates and transition when
the same picture is still in the playlist. A picture no item names any more is deleted, but only a library row whose
name starts with `PREFIX`: a file an operator uploaded, or renamed, is never touched.

It looks again every `CHECK_S` seconds and when the operator presses Sync now. Nothing is fetched while ProPresenter
is down or signage is off; the pictures already stored keep playing. Slides are licensed content, so this is off until
the operator switches it on.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

from . import propresenter as pp
from .ppcontrol import Control, control as pp_control

log = logging.getLogger("relay.signagesync")

PREFIX = "ProPresenter: "
PLAYLIST_NAME = "ProPresenter announcements"
CHECK_S = 120.0
SLIDE_QUALITY = 1280        # longest side in pixels: the signage player shows it full screen
SLIDES_MAX = 150
DURATION_DEFAULT_S = 10
MESSAGES = {
    "no_playlist": "Pick the announcement playlist first.",
    "not_connected": "ProPresenter is not connected.",
    "off": "Signage is switched off.",
    "empty": "That playlist has no presentations Relay can read slides from.",
    "too_many": f"That playlist has more than {SLIDES_MAX} slides, more than Relay will copy.",
    "failed": "ProPresenter did not give Relay the slides; it will try again.",
    "store": "Relay could not save the slides into signage.",
}


def _content():
    from ... import modules

    got = modules.using("signage.content")
    if got is None:
        raise SyncError("off")
    return got


class SyncError(Exception):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class Sync:
    def __init__(self, follower: pp.Follower | None = None, control: Control | None = None) -> None:
        self.f = follower or pp.follower
        self.ctl = control or pp_control
        self.rev = ""                    # what the last good sync was made from
        self.at = 0.0                    # wall-clock time of the last good sync
        self.slides = 0
        self.error = ""                  # a MESSAGES key, "" when the last try was fine
        self.checked = 0.0
        self.syncing = False
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Announcement sync failed")
            await asyncio.sleep(CHECK_S)

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    @staticmethod
    def settings() -> dict[str, Any]:
        return pp.settings().get("signage") or pp.default_signage()

    @staticmethod
    def _signage_on() -> bool:
        from ... import modules

        return modules.using("signage.content") is not None

    # -- one pass ----------------------------------------------------------------------------------------
    async def sync(self, force: bool = False) -> dict:
        """Look at the playlist and, when it changed (or `force`), copy its slides into signage. Returns the state.
        Quiet when switched off. A failure is kept as a message key and tried again on the next pass."""
        s = self.settings()
        if not s["enabled"]:
            self.error = ""
            return self.state()
        async with self._guard():
            self.checked = time.time()
            try:
                await self._pass(s["playlist"], force)
                self.error = ""
            except SyncError as exc:
                self.error = exc.key
            except pp.ProError:
                self.error = "failed"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Announcement sync failed")
                self.error = "failed"
        return self.state()

    async def _pass(self, playlist: str, force: bool) -> None:
        if not playlist:
            raise SyncError("no_playlist")
        if not self._signage_on():
            raise SyncError("off")
        if not (self.f.up and self.f.client):
            raise SyncError("not_connected")
        body = await self.ctl.playlist_items(playlist)
        items = [i for i in body["items"] if i["type"] == "presentation" and not i["hidden"] and i["presentation"]
                 and isinstance(i["index"], int)]
        if not items:
            raise SyncError("empty")
        plan = []                        # (item, cues response)
        total = 0
        for it in items:
            got = await self.ctl.playlist_item_cues(playlist, it["index"])
            cues = [c for c in got["cues"] if c["enabled"]]
            total += len(cues)
            if total > SLIDES_MAX:
                raise SyncError("too_many")
            plan.append((it, got, cues))
        rev = hashlib.sha1(json.dumps([playlist, [(it["index"], it["presentation"], got["rev"],
                                                   [c["index"] for c in cues]) for it, got, cues in plan]]
                                      ).encode(), usedforsecurity=False).hexdigest()[:16]
        if rev == self.rev and not force:
            return
        slides: list[tuple[str, bytes, bytes]] = []
        for it, got, cues in plan:
            for c in cues:
                _, big = await self.ctl.playlist_thumbnail(playlist, it["index"], c["index"], got["rev"],
                                                           SLIDE_QUALITY)
                _, small = await self.ctl.playlist_thumbnail(playlist, it["index"], c["index"], got["rev"])
                slides.append((f"{PREFIX}{it['name']} {c['index'] + 1}", big, small))
        try:
            await self._store(slides)
        except SyncError:
            raise
        except Exception:
            log.exception("Announcement sync could not store the slides")
            raise SyncError("store") from None
        self.rev, self.at, self.slides = rev, time.time(), len(slides)
        log.info("Announcement sync: %d slide(s) from %d presentation(s)", len(slides), len(plan))

    async def _store(self, slides: list[tuple[str, bytes, bytes]]) -> None:
        content = _content()
        media, store = content.media, content.store

        async def chunks(data: bytes):
            yield data

        refs: list[str] = []
        by_sha = {m["sha256"]: m for m in await asyncio.to_thread(store.media_list) if m["name"].startswith(PREFIX)}
        for name, big, small in slides:
            sha, ext, size = await media.receive(chunks(big))
            row = by_sha.get(sha)
            if row is None:
                row = await asyncio.to_thread(store.media_add, name, sha, ext, size)
                by_sha[sha] = row
                try:
                    await asyncio.to_thread(store.media_thumb, row["id"], small)
                except media.Refused:
                    pass
            refs.append(row["id"])
        await asyncio.to_thread(self._set_playlist, refs)

    @staticmethod
    def _set_playlist(refs: list[str]) -> None:
        store = _content().store

        lists = [p for p in store.playlists() if p["name"] == PLAYLIST_NAME]
        pl = lists[0] if lists else store.playlist_create(PLAYLIST_NAME)
        keep = {}
        for it in (lists[0]["items"] if lists else []):
            if it["source"] == "media":
                keep.setdefault(it["ref"], it)
        items = []
        for ref in refs:
            old = keep.get(ref)
            items.append({"source": "media", "ref": ref,
                          "duration_s": old["duration_s"] if old else DURATION_DEFAULT_S,
                          "start_date": old["start_date"] if old else "", "end_date": old["end_date"] if old else "",
                          "transition": old["transition"] if old else "fade", "fit": old["fit"] if old else "fit"})
        store.playlist_set_items(pl["id"], items)
        # Pictures nothing names any more. media_delete refuses one a playlist still uses, which is fine.
        used = {it["ref"] for p in store.playlists() for it in p["items"] if it["source"] == "media"}
        for m in store.media_list():
            if m["name"].startswith(PREFIX) and m["id"] not in used:
                try:
                    store.media_delete(m["id"])
                except store.Invalid:
                    pass

    # -- state -------------------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        s = self.settings()
        return {"enabled": s["enabled"], "playlist": s["playlist"], "playlist_name": PLAYLIST_NAME,
                "slides": self.slides, "synced_at": self.at, "checked_at": self.checked,
                "error": MESSAGES.get(self.error, "") if self.error else "", "signage_on": self._signage_on()}


sync = Sync()
