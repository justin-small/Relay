"""Slide-note tags that drive signage screens (#208). The ProPresenter module names `signage` in its `uses`.

Which action does what (a value is `Name:Thing` for the `-screen` and `-group` forms, which split at the colon that
follows a screen or group name Relay knows; the plain forms act on every screen):

    signage-screen=Lobby TV:Welcome        a screen plays the signage playlist "Welcome"
    signage-group=Lobby:Welcome            every screen in a group plays it
    signage-all=Welcome                    every screen plays it
    signage-push[-screen|-group]=...       send one picture from the media library (or one announcement slide) to play
                                           once, then the screen goes back to its playlist
    signage-captions[-screen|-group]       show Relay's live captions until a resume tag
    signage-service[-screen|-group]        show the Planning Center service rundown until a resume tag
    signage-resume[-screen|-group]         end what tags put on the screens: playlists, pushes and live views

A playlist put on by a tag is the "tag" layer of signage's schedule: above a playlist chosen by hand and the schedule,
below a takeover. It stays until another tag, a resume tag or a restart of Relay. These actions do nothing while the
signage module is off (the tag's result says so). Names are not case-sensitive.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ...text import fold

log = logging.getLogger("relay.signagetags")

TARGETS = (("screen", "-screen"), ("group", "-group"), ("all", ""))

# action -> takes a value. Added to slidetags.ACTIONS so the tags are read whether or not signage is on.
NAMES: dict[str, bool] = {"signage-screen": True, "signage-group": True, "signage-all": True}
for _target, _suffix in TARGETS:
    for _kind in ("push", "captions", "service", "resume"):
        NAMES[f"signage-{_kind}{_suffix}"] = _suffix != "" or _kind == "push"

OFF = "Signage is switched off, so nothing was changed."
LIVE_VIEWS = {"captions": "captions", "service": "service"}

# (target, id) -> playlist id: what tags have put on screens. Nothing else is kept; a restart forgets it.
_picks: dict[tuple[str, int], int] = {}


def _signage_on() -> bool:
    from ... import modules

    return modules.using("signage.content") is not None


def _content():
    """What signage provides ("signage.content": store, media, live, takeover, schedule, sources); an action stops
    with a sentence while signage is off."""
    from ... import modules

    got = modules.using("signage.content")
    if got is None:
        raise RuntimeError(OFF)
    return got


def layer(screen: dict, now_utc: datetime) -> dict | None:
    """Signage's "tag" layer: the playlist a tag put on this screen, its group or every screen."""
    for key in (("screen", screen["id"]), ("group", screen.get("group_id") or 0), ("all", 0)):
        pid = _picks.get(key)
        if pid:
            return {"playlist_id": pid, "why": "Put on by a ProPresenter slide-note tag"}
    return None


def state() -> dict[str, Any]:
    """What tags have put on screens now, for the tag panel: [{target, name, playlist}]."""
    if not _picks or not _signage_on():
        return {"on": _signage_on(), "held": []}
    store = _content().store

    try:
        screens = {s["id"]: s["name"] for s in store.screens()}
        groups = {g["id"]: g["name"] for g in store.groups()}
        lists = {p["id"]: p["name"] for p in store.playlists()}
    except Exception:
        return {"on": True, "held": []}
    held = []
    for (target, tid), pid in sorted(_picks.items()):
        name = {"screen": screens.get(tid), "group": groups.get(tid), "all": "every screen"}[target]
        if name and pid in lists:
            held.append({"target": target, "name": name, "playlist": lists[pid]})
    return {"on": True, "held": held}


def forget() -> None:
    _picks.clear()


# ------------------------------------------------------------------ finding things by name
def _named(rows: list[dict], name: str) -> dict | None:
    want = fold(name)
    hits = [r for r in rows if fold(r["name"]) == want]
    return hits[0] if len(hits) == 1 else None


def _split(value: str, rows: list[dict]) -> tuple[dict | None, str]:
    """`Name:rest` -> (the row named `Name`, rest). The name may hold colons: every split is tried."""
    value = " ".join(value.split())
    for i, ch in enumerate(value):
        if ch == ":":
            row = _named(rows, value[:i])
            if row is not None:
                return row, value[i + 1:].strip()
    return None, ""


def _target(kind: str, value: str | None, rows_of: dict[str, list[dict]], takes_rest: bool) -> tuple[str, int, str, str]:
    """(target, id, rest, what to call it) for an action's value. Raises RuntimeError with a sentence."""
    if kind == "all":
        return "all", 0, (value or "").strip(), "every screen"
    rows = rows_of[kind]
    if takes_rest:
        row, rest = _split(value or "", rows)
        if row is None or not rest:
            raise RuntimeError(f"Write it as {kind} name:name. Relay has no {kind} called that.")
    else:
        row, rest = _named(rows, value or ""), ""
        if row is None:
            raise RuntimeError(f"Relay has no {kind} called \"{' '.join((value or '').split())[:40]}\".")
    return kind, int(row["id"]), rest, row["name"]


def _playlist(name: str) -> dict:
    store = _content().store

    row = _named(store.playlists(), name)
    if row is None:
        raise RuntimeError(f"Signage has no playlist called \"{' '.join(name.split())[:40]}\".")
    return row


def _media(name: str) -> dict:
    store = _content().store

    rows = [m for m in store.media_list() if m["kind"] == "image"]
    row = _named(rows, name)
    if row is None:
        # An announcement slide is named "ProPresenter: <presentation> <n>"; allow the name without the prefix.
        from .signagesync import PREFIX

        row = _named(rows, PREFIX + name)
    if row is None:
        raise RuntimeError(f"The media library has no picture called \"{' '.join(name.split())[:40]}\".")
    return row


# ------------------------------------------------------------------ the actions (sync; run off the event loop)
def _tables() -> dict[str, list[dict]]:
    store = _content().store

    return {"screen": store.screens(), "group": store.groups()}


def _do(action: str, value: str | None) -> str:
    content = _content()
    live, store, takeover = content.live, content.store, content.takeover

    if not _signage_on():
        raise RuntimeError(OFF)
    kind, _, suffix = action.removeprefix("signage-").partition("-")
    # signage-screen / -group / -all are the playlist actions; the others are kind[-screen|-group].
    if action in ("signage-screen", "signage-group", "signage-all"):
        target = action.removeprefix("signage-")
        what_kind = "playlist"
    else:
        what_kind, target = kind, suffix or "all"
    tables = _tables()
    takes_rest = what_kind in ("playlist", "push") and target != "all"
    tgt, tid, rest, label = _target(target, value, tables, takes_rest)
    if what_kind == "playlist":
        pl = _playlist(rest if target != "all" else (value or ""))
        _picks[(tgt, tid)] = int(pl["id"])
        live.bump()
        return f"{label} now plays {pl['name']}."
    if what_kind == "push":
        m = _media(rest if target != "all" else (value or ""))
        takeover.push_set(tgt, tid, m["id"], "once")
        return f"Sent {m['name']} to {label}."
    if what_kind in LIVE_VIEWS:
        takeover.live_view_set(tgt, tid, LIVE_VIEWS[what_kind])
        return f"{label} shows the live {what_kind} until a signage-resume tag."
    # resume
    if tgt == "all":
        _picks.clear()
        n = takeover.clear_all()
        live.bump()
        return f"Every screen is back on its playlist ({n} takeover{'s' if n != 1 else ''} ended)."
    _picks.pop((tgt, tid), None)
    try:
        takeover.clear(tgt, tid)
    except store.Invalid:
        pass
    live.bump()
    return f"{label} is back on its playlist."


def _action(name: str):
    async def run(value: str | None) -> str:
        store = _content().store

        try:
            return await asyncio.to_thread(_do, name, value)
        except store.Invalid as exc:
            raise RuntimeError(exc.message) from None

    return run


ACTIONS = {name: _action(name) for name in NAMES}
