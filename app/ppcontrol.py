"""ProPresenter control (#138, stage 2): next/previous, the slide grid and its thumbnails, the clear dock,
playlists, macros, looks, timers, messages and the media transport.

Everything goes through `propresenter.Client` (reached as `follower.client`), so view-only mode refuses every
write there before a socket is opened, and the bulk timer calls are never sent (Client.action refuses them).
This module only decides WHICH calls to make and shapes what comes back; it has no HTTP of its own.

Methods are blocking-free coroutines: each runs its client call off the event loop with `follower.call`.
Callers (the admin routes in main.py) catch `ProError` and answer through `_pp_error`; a bad argument raises
`ValueError` with a fixed message that is safe to show.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.parse
from collections import OrderedDict
from typing import Any

from . import propresenter as pp

LAYERS = ("slide", "media", "props", "messages", "audio", "video_input", "announcements")
TIMER_OPS = ("start", "stop", "reset")
THUMB_QUALITY = 320
THUMB_MAX_ENTRIES = 600
THUMB_MAX_BYTES = 48 * 1024 * 1024
THUMB_PARALLEL = 3            # ProPresenter can stall: a page of thumbnails must not tie up every worker
MAX_INDEX = 5000
TOKEN_TEXT_MAX = 500


def _seg(value: Any) -> str:
    """One path segment: a UUID, a name or an index. Refuses anything that could change the path."""
    text = str(value if value is not None else "").strip()
    if not pp.ID_RE.match(text):
        raise ValueError("That is not a ProPresenter ID.")
    return urllib.parse.quote(text, safe="")


def _index(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError("The slide number must be a whole number.")
    if not 0 <= n <= MAX_INDEX or isinstance(value, bool):
        raise ValueError("The slide number must be a whole number.")
    return n


def _hex(color: Any) -> str:
    if not isinstance(color, dict):
        return ""
    try:
        r, g, b = (max(0, min(255, round(float(color.get(k, 0)) * 255))) for k in ("red", "green", "blue"))
    except (TypeError, ValueError):
        return ""
    return f"#{r:02x}{g:02x}{b:02x}"


def _id(obj: Any) -> dict:
    i = obj.get("id") if isinstance(obj, dict) and isinstance(obj.get("id"), dict) else obj
    i = i if isinstance(i, dict) else {}
    return {"uuid": str(i.get("uuid") or ""), "name": str(i.get("name") or "").strip(), "index": i.get("index")}


class Control:
    def __init__(self, follower: pp.Follower) -> None:
        self.f = follower
        self.thumbs: OrderedDict[tuple, tuple[str, bytes]] = OrderedDict()
        self.thumb_bytes = 0
        self.thumb_hits = 0
        self.thumb_fetches = 0
        self._sem: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None

    # -- plumbing ----------------------------------------------------------------------------------------
    def _client(self) -> pp.Client:
        return self.f._need_client()

    async def _get(self, path: str) -> Any:
        return await self.f.call(self._client().read, path)

    async def _act(self, path: str) -> None:
        await self.f.call(self._client().action, path)

    # -- slides ------------------------------------------------------------------------------------------
    async def slides(self, uuid: str | None = None) -> dict:
        """The cues of a presentation (the active one by default): group name and colour, text, and a `rev`
        that changes when the slides do, which keys the thumbnail cache."""
        if not uuid:
            pres = self.f.presentation
            if not pres:
                return {"uuid": "", "name": "", "rev": "", "cues": []}
            uuid = pres["uuid"]
        seg = _seg(uuid)
        body = await self._get(f"/v1/presentation/{seg}")
        detail = body.get("presentation") if isinstance(body, dict) and "presentation" in body else body
        detail = detail if isinstance(detail, dict) else {}
        cues = []
        for g in pp.cue_groups(detail):
            for s in g.get("slides") or []:
                if not isinstance(s, dict):
                    continue
                cues.append({"index": len(cues), "group": str(g.get("name") or "").strip(),
                             "color": _hex(g.get("color")) or _hex(s.get("color")),
                             "text": str(s.get("text") or ""), "label": str(s.get("label") or ""),
                             "enabled": s.get("enabled") is not False})
        rev = hashlib.sha1(json.dumps(cues, sort_keys=True).encode()).hexdigest()[:10]
        return {"uuid": str(uuid), "name": _id(detail)["name"], "rev": rev, "cues": cues}

    async def thumbnail(self, uuid: str, index: Any, rev: str = "") -> tuple[str, bytes]:
        """A slide's thumbnail, from the cache when it has been fetched for this presentation, index and change
        marker. Browsers never see ProPresenter: they ask Relay, and Relay asks once."""
        key = (str(uuid), _index(index), str(rev)[:32])
        hit = self.thumbs.get(key)
        if hit:
            self.thumbs.move_to_end(key)
            self.thumb_hits += 1
            return hit
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem[0] is not loop:
            self._sem = (loop, asyncio.Semaphore(THUMB_PARALLEL))
        async with self._sem[1]:
            hit = self.thumbs.get(key)       # another request may have fetched it while this one waited
            if hit:
                self.thumb_hits += 1
                return hit
            path = f"/v1/presentation/{_seg(uuid)}/thumbnail/{key[1]}?quality={THUMB_QUALITY}&thumbnail_type=jpeg"
            ctype, data = await self.f.call(self._client().read_image, path)
            self.thumb_fetches += 1
        if not ctype.startswith("image/"):
            raise pp.ProError("bad", "ProPresenter sent something that is not an image.")
        self.thumbs[key] = (ctype, data)
        self.thumb_bytes += len(data)
        while self.thumbs and (len(self.thumbs) > THUMB_MAX_ENTRIES or self.thumb_bytes > THUMB_MAX_BYTES):
            _, (_, old) = self.thumbs.popitem(last=False)
            self.thumb_bytes -= len(old)
        return ctype, data

    async def step(self, direction: str) -> None:
        if direction not in ("next", "previous"):
            raise ValueError("Direction is next or previous.")
        await self._act(f"/v1/trigger/{direction}")

    async def next_presentation(self) -> None:
        await self._act("/v1/presentation/active/next/trigger")

    async def trigger_slide(self, uuid: str, index: Any) -> None:
        await self._act(f"/v1/presentation/{_seg(uuid)}/{_index(index)}/trigger")

    async def clear(self, layer: str) -> None:
        if layer not in LAYERS:
            raise ValueError("That is not a layer ProPresenter can clear.")
        await self._act(f"/v1/clear/layer/{layer}")

    # -- playlists ---------------------------------------------------------------------------------------
    async def playlists(self) -> dict:
        body = await self._get("/v1/playlists")

        def tree(items: Any) -> list[dict]:
            out = []
            for o in items if isinstance(items, list) else []:
                if not isinstance(o, dict):
                    continue
                kids = o.get("children") if o.get("children") is not None else o.get("playlists")
                kind = o.get("field_type") or o.get("type") or "playlist"
                out.append({**_id(o), "type": "group" if kind == "group" else "playlist", "children": tree(kids)})
            return out

        return {"playlists": tree(body), "active": self.f.data.get("playlist/active")}

    async def playlist_items(self, playlist: str) -> dict:
        body = await self._get(f"/v1/playlist/{_seg(playlist)}")
        body = body if isinstance(body, dict) else {}
        items = []
        for n, o in enumerate(body.get("items") or []):
            if not isinstance(o, dict):
                continue
            info = o.get("presentation_info") if isinstance(o.get("presentation_info"), dict) else {}
            ident = _id(o)
            items.append({"index": n if ident["index"] is None else ident["index"], "uuid": ident["uuid"],
                          "name": ident["name"], "type": str(o.get("type") or ""),
                          "hidden": bool(o.get("is_hidden")), "color": _hex(o.get("header_color")),
                          "duration": o.get("duration"),
                          "presentation": str(info.get("presentation_uuid") or o.get("target_uuid") or "")
                          if o.get("type") == "presentation" else ""})
        return {"playlist": _id(body), "items": items}

    async def trigger_playlist_item(self, playlist: str, index: Any) -> None:
        await self._act(f"/v1/playlist/{_seg(playlist)}/{_index(index)}/trigger")

    # -- macros and looks --------------------------------------------------------------------------------
    def macro_allowed(self, uuid: str) -> bool:
        s = pp.settings()
        return s.get("macros_mode") == "all" or uuid in (s.get("macros_allowed") or [])

    async def macros(self, everything: bool = False) -> dict:
        """The macros an operator may use. `everything` (the admin picker) lists them all, marking which are
        in the operator-safe selection."""
        body = await self._get("/v1/macros")
        s = pp.settings()
        allowed = set(s.get("macros_allowed") or [])
        out = []
        for o in body if isinstance(body, list) else []:
            ident = _id(o)
            if not ident["uuid"]:
                continue
            shown = s.get("macros_mode") == "all" or ident["uuid"] in allowed
            if shown or everything:
                out.append({**ident, "color": _hex(o.get("color")), "selected": ident["uuid"] in allowed,
                            "shown": shown})
        return {"macros": out, "mode": s.get("macros_mode"), "allowed": sorted(allowed)}

    async def trigger_macro(self, uuid: str) -> None:
        seg = _seg(uuid)
        if not self.macro_allowed(str(uuid)):
            raise PermissionError("That macro is not in the operator-safe selection.")
        await self._act(f"/v1/macro/{seg}/trigger")

    async def looks(self) -> dict:
        body = await self._get("/v1/looks")
        return {"looks": [_id(o) for o in body if isinstance(o, dict)] if isinstance(body, list) else []}

    async def trigger_look(self, look: str) -> None:
        await self._act(f"/v1/look/{_seg(look)}/trigger")

    # -- timers ------------------------------------------------------------------------------------------
    async def timers(self) -> dict:
        body = await self._get("/v1/timers")
        out = []
        for o in body if isinstance(body, list) else []:
            if not isinstance(o, dict):
                continue
            kind = "countdown" if "countdown" in o else "to_time" if "count_down_to_time" in o \
                else "elapsed" if "count_up" in o or "elapsed" in o else "timer"
            out.append({**_id(o), "kind": kind})
        return {"timers": out}

    async def timer(self, timer: str, op: str) -> None:
        """One timer. The bulk `timers/start` starts every timer the church has; Client.action refuses it."""
        if op not in TIMER_OPS:
            raise ValueError("A timer can be started, stopped or reset.")
        await self._act(f"/v1/timer/{_seg(timer)}/{op}")

    # -- messages ----------------------------------------------------------------------------------------
    async def messages(self) -> dict:
        body = await self._get("/v1/messages")
        out = []
        for o in body if isinstance(body, list) else []:
            if not isinstance(o, dict):
                continue
            toks = []
            for t in o.get("tokens") or []:
                if isinstance(t, dict) and t.get("name"):
                    kind = "text" if "text" in t else "timer" if "timer" in t else "clock" if "clock" in t else "other"
                    val = (t.get("text") or {}).get("text", "") if kind == "text" else ""
                    toks.append({"name": str(t["name"])[:64], "kind": kind, "text": str(val)})
            out.append({**_id(o), "message": str(o.get("message") or ""), "tokens": toks,
                        "active": bool(o.get("is_active"))})
        return {"messages": out}

    async def trigger_message(self, message: str, tokens: Any) -> None:
        """Show a prepared message, filling its text tokens: [{"name": "text", "text": "Sound desk"}]."""
        body = []
        for t in tokens if isinstance(tokens, list) else []:
            if not isinstance(t, dict) or not str(t.get("name") or "").strip():
                raise ValueError("Each token needs a name.")
            text = " ".join(str(t.get("text") or "").split())[:TOKEN_TEXT_MAX]
            body.append({"name": str(t["name"]).strip()[:64], "text": {"text": text}})
        await self.f.call(self._client().post, f"/v1/message/{_seg(message)}/trigger", body)

    async def clear_message(self, message: str) -> None:
        await self._act(f"/v1/message/{_seg(message)}/clear")

    # -- transport ---------------------------------------------------------------------------------------
    async def go_to_time(self, seconds: Any) -> None:
        """Move the presentation's media to a time. The reference says `PUT /v1/transport/{layer}/time` with a
        bare number, not the `go_to_time` name the issue used."""
        try:
            t = float(seconds)
        except (TypeError, ValueError):
            raise ValueError("The time is a number of seconds.")
        if isinstance(seconds, bool) or not 0 <= t <= 86400 or t != t:
            raise ValueError("The time is a number of seconds.")
        await self.f.call(self._client().put, "/v1/transport/presentation/time", t)


control = Control(pp.follower)
