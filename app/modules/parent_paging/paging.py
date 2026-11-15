"""Parent paging (#220): the saved setup, the pages that are up, and what ProPresenter is told.

A kids worker opens their room's link, taps a child, and Relay pages that child's Check-Ins security code. How it
reaches the audience screen is the operator's choice (`mode`):

  queue  it waits on Relay's Parent paging page until the ProPresenter operator presses Show, then Relay triggers
         the ProPresenter message with the code in its token;
  fill   Relay only writes the code into the message's token; the operator shows the message from ProPresenter;
  auto   Relay triggers the message at once.

Every code that is up shares the one message: its token holds them all, oldest first. A page ends when the operator
clears it, the kids worker taps "Parent arrived", or it has been up `timeout_min` minutes. Pages live in memory: a
restart of Relay ends them all (and leaves whatever ProPresenter shows as it was).

The screen only ever gets the code, never a name: the name stays on the room's own page.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from ... import audit, config

log = logging.getLogger("relay.parent_paging")

KEY = "parent_paging"
MODES = ("queue", "fill", "auto")
MAX_ROOMS = 40
MAX_PAGES = 20               # pages up at once, across every room
TIMEOUT_MAX_MIN = 120
CODE_GAP = "   "             # between codes in the token
_ID = re.compile(r"^[0-9a-f]{8}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_LOCATION = re.compile(r"^[0-9]{1,20}$")


def default_settings() -> dict[str, Any]:
    return {"mode": "queue", "message": "Parent Page", "token": "code", "timeout_min": 10, "rooms": []}


def _text(raw: object, n: int) -> str:
    return " ".join(str(raw or "").split())[:n]


def clean_room(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    rid, loc, sec = str(raw.get("id") or ""), str(raw.get("location_id") or ""), str(raw.get("secret") or "")
    if not _ID.match(rid) or not _LOCATION.match(loc) or not _SECRET.match(sec):
        return None
    return {"id": rid, "name": _text(raw.get("name"), 80) or "Room", "location_id": loc,
            "event": _text(raw.get("event"), 80), "secret": sec}


def clean_stored(raw: Any) -> dict:
    s = default_settings()
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("mode") in MODES:
        s["mode"] = raw["mode"]
    s["message"] = _text(raw.get("message"), 100) or s["message"]
    s["token"] = _text(raw.get("token"), 60) or s["token"]
    try:
        s["timeout_min"] = max(0, min(TIMEOUT_MAX_MIN, int(raw.get("timeout_min", s["timeout_min"]))))
    except (TypeError, ValueError):
        pass
    rooms, seen = [], set()
    for r in raw.get("rooms") or []:
        r = clean_room(r)
        if r and r["id"] not in seen and len(rooms) < MAX_ROOMS:
            seen.add(r["id"])
            rooms.append(r)
    s["rooms"] = rooms
    return s


def settings() -> dict[str, Any]:
    return config.get().get(KEY) or default_settings()


def public_room(room: dict) -> dict:
    return {k: v for k, v in room.items() if k != "secret"}


def public(s: dict) -> dict:
    """What the admin page may see: everything but the rooms' link secrets (read one with its own route)."""
    return {**s, "rooms": [public_room(r) for r in s["rooms"]]}


def new_room(name: str, location_id: str, event: str = "") -> dict:
    taken = {r["id"] for r in settings()["rooms"]}
    rid = secrets.token_hex(4)
    while rid in taken:
        rid = secrets.token_hex(4)
    return {"id": rid, "name": _text(name, 80) or "Room", "location_id": location_id, "event": _text(event, 80),
            "secret": secrets.token_urlsafe(18)}


def room_by_secret(secret: str) -> dict | None:
    """The room whose link holds `secret`. Compared in constant time; a malformed secret matches nothing."""
    if not _SECRET.match(secret or ""):
        return None
    for r in settings()["rooms"]:
        if hmac.compare_digest(r["secret"], secret):
            return r
    return None


def room_by_id(rid: str) -> dict | None:
    return next((r for r in settings()["rooms"] if r["id"] == rid), None)


class Refused(Exception):
    """A request that cannot be done. `.message` is a fixed sentence, `.status` the HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------------------------- the pages
@dataclass
class Page:
    id: str
    room_id: str
    room: str
    code: str
    at: float
    shown: bool = False           # queue mode: the operator pressed Show
    kid: str = field(default="", repr=False)   # the Check-Ins id, so the room's list can mark the child; never sent on


class Pager:
    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.sent: str | None = None      # the codes ProPresenter was last told, None before the first write
        self.triggered = False            # the message was last shown by Relay (so ending it should hide it)
        self.error = ""                   # a fixed sentence: the last ProPresenter write that failed
        self.force = False                # write again even if the codes did not change (the setup changed)
        self.rev = 0
        self._lock: asyncio.Lock | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self.client_for = None            # () -> ProPresenter client (set by the module; replaced in tests)
        self.call = None                  # async (fn, *args) -> result, runs a blocking call off the loop

    # -- state ------------------------------------------------------------------------------------------
    def reset(self) -> None:
        self.pages.clear()
        self.sent, self.triggered, self.error, self.force = None, False, "", False
        self.rev += 1

    def poke(self) -> None:
        """Let the worker tell ProPresenter now rather than on its next round."""
        if self._wake is not None:
            self._wake.set()

    def _changed(self) -> None:
        self.rev += 1
        self.poke()

    def on_screen(self, mode: str | None = None) -> list[Page]:
        """The pages whose code goes into the ProPresenter message's token."""
        mode = mode or settings()["mode"]
        return [p for p in self.pages if mode != "queue" or p.shown]

    def status_of(self, page: Page, mode: str | None = None) -> str:
        mode = mode or settings()["mode"]
        if mode == "fill":
            return "sent"
        if mode == "auto" or page.shown:
            return "shown"
        return "waiting"

    def view(self, page: Page, now: float | None = None, mode: str | None = None) -> dict:
        now = time.time() if now is None else now
        return {"id": page.id, "room_id": page.room_id, "room": page.room, "code": page.code,
                "age_s": max(0, int(now - page.at)), "status": self.status_of(page, mode)}

    def for_room(self, room_id: str) -> list[Page]:
        return [p for p in self.pages if p.room_id == room_id]

    # -- what people do ---------------------------------------------------------------------------------
    def page(self, room: dict, kid: dict) -> Page:
        """A kids worker paged `kid` ({"id", "code"}) from `room`. The same code already up is that page."""
        for p in self.pages:
            if p.code == kid["code"]:
                return p
        if len(self.pages) >= MAX_PAGES:
            raise Refused("Too many parents are being paged right now. Try again when one has arrived.", 429)
        p = Page(secrets.token_hex(4), room["id"], room["name"], kid["code"], time.time(), kid=str(kid.get("id", "")))
        self.pages.append(p)
        audit.record("parent_paging.page", f"Paged a parent from {room['name']}", target=p.code,
                     detail={"room": room["name"]})
        self._changed()
        return p

    def _find(self, pid: str) -> Page:
        p = next((p for p in self.pages if p.id == pid), None)
        if p is None:
            raise Refused("That page has already ended.", 404)
        return p

    def show(self, pid: str) -> Page:
        p = self._find(pid)
        p.shown = True
        self._changed()
        return p

    def end(self, pid: str, room_id: str | None = None, why: str = "cleared") -> None:
        p = self._find(pid)
        if room_id is not None and p.room_id != room_id:
            raise Refused("That page has already ended.", 404)
        self.pages.remove(p)
        log.info("Parent page %s ended (%s)", p.code, why)
        self._changed()

    def end_all(self) -> int:
        n = len(self.pages)
        self.pages.clear()
        self._changed()
        return n

    def end_room(self, room_id: str) -> None:
        before = len(self.pages)
        self.pages = [p for p in self.pages if p.room_id != room_id]
        if len(self.pages) != before:
            self._changed()

    def expire(self, now: float | None = None) -> int:
        minutes = settings()["timeout_min"]
        if not minutes:
            return 0
        now = time.time() if now is None else now
        old = [p for p in self.pages if now - p.at >= minutes * 60]
        for p in old:
            self.pages.remove(p)
            log.info("Parent page %s timed out", p.code)
        if old:
            self._changed()
        return len(old)

    # -- ProPresenter -----------------------------------------------------------------------------------
    def codes(self, mode: str | None = None) -> str:
        return CODE_GAP.join(p.code for p in self.on_screen(mode))

    async def sync(self) -> None:
        """Tell ProPresenter what should be up, if that changed since it was last told."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            s = settings()
            codes = self.codes(s["mode"])
            if self.sent is None and not codes:
                return   # nothing was ever sent and nothing is up: leave ProPresenter alone
            if codes == self.sent and not self.error and not self.force:
                return
            try:
                await self._write(s, codes)
            except Exception as exc:  # noqa: BLE001 - the follower's errors, view-only mode, a refused write
                self.error = _pp_refusal(exc)
                log.warning("Parent paging: ProPresenter was not updated: %s", exc)
                return
            self.sent, self.error, self.force = codes, "", False
            self.rev += 1

    async def _write(self, s: dict, codes: str) -> None:
        client = self.client_for() if self.client_for else None
        if client is None:
            raise Refused("ProPresenter is not set up.")
        mid = "/v1/message/" + urllib.parse.quote(s["message"], safe="")
        if s["mode"] == "fill":
            msg = await self.call(client.read, mid)
            body = with_token(msg, s["token"], codes)
            await self.call(client.put, mid, body)
            if not codes and self.triggered:
                await self.call(client.action, mid + "/clear")
                self.triggered = False
            return
        if codes:
            await self.call(client.post, mid + "/trigger", [{"name": s["token"], "text": {"text": codes}}])
            self.triggered = True
        else:
            await self.call(client.action, mid + "/clear")
            self.triggered = False

    # -- the worker -------------------------------------------------------------------------------------
    async def start(self) -> None:
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._wake = None

    async def _run(self) -> None:
        while True:
            try:
                self.expire()
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("parent paging worker failed")
            wake = self._wake
            if wake is None:
                return
            try:
                await asyncio.wait_for(wake.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            wake.clear()


def with_token(msg: Any, token: str, text: str) -> dict:
    """ProPresenter's message `msg` with its text token `token` set to `text`; refused when it has no such token."""
    if not isinstance(msg, dict) or not isinstance(msg.get("tokens"), list):
        raise Refused("ProPresenter did not send that message.")
    tokens, found = [], False
    for t in msg["tokens"]:
        if isinstance(t, dict) and t.get("name") == token and "text" in t:
            t = {**t, "text": {"text": text}}
            found = True
        tokens.append(t)
    if not found:
        raise Refused("The ProPresenter message has no text token with that name.")
    return {**msg, "tokens": tokens}


def _pp_refusal(exc: Exception) -> str:
    """A fixed sentence for the admin page, by kind: never the exception's own text."""
    if isinstance(exc, Refused):
        return exc.message
    kind = getattr(exc, "kind", "")
    if kind == "view_only":
        return "ProPresenter is in view-only mode in Relay, so the page was not sent."
    if kind == "http":
        return "ProPresenter refused it. Check that the message name is right and the message has the token."
    if kind in ("network", "timeout"):
        return "Could not reach ProPresenter."
    return "ProPresenter could not be updated."


pager = Pager()
