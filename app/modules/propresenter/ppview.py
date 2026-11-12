"""The slide view (#138, stage 7): the live slide and the next slide as images (or as text when no image is
available), with a countdown overlay for the timer the operator chose and the progress of the video playing on
the slide.

Everything is read from the follower, which already holds the slide, the timers and the presentation layer's media
position (`transport/presentation/time` and `.../current`, read with the slide every second because the stream
misses changes). Images are ProPresenter's own thumbnails fetched by Relay, at a larger size than the slide grid's,
through the same cache. Browsers never reach ProPresenter, and a viewer can only ask for "the slide that is up now"
and "the next one", never for an arbitrary slide of a presentation.

Slide images are licensed content like lyrics, so the page is behind the admin login unless the operator turns
`view.public` on (off by default), which opens the same page without a login on the viewer port at /stage.
"""
from __future__ import annotations

import time
from typing import Any

from . import propresenter as pp
from .ppcontrol import LIVE_QUALITY, Control, _rev, control as pp_control

CARRY_MAX_S = 5.0      # a reading older than this is not carried forward: Relay would be guessing
WHICH = ("current", "next")


def _round(v: float) -> float:
    return round(v, 1)


class View:
    def __init__(self, follower: pp.Follower | None = None, control: Control | None = None) -> None:
        self.f = follower or pp.follower
        self.ctl = control or pp_control

    # -- what is up ----------------------------------------------------------------------------------------
    def target(self, which: str) -> dict | None:
        """The slide `which` ("current" or "next") names: {uuid, index, v}, or None when there is no such slide or
        no way to draw it. `v` changes with the slide, so a changed slide is a new image URL."""
        f = self.f
        pres, idx = f.presentation, f.slide_index
        if which not in WHICH or not (f.up and pres and idx is not None):
            return None
        n = idx + (1 if which == "next" else 0)
        cues = pres.get("cues") or []
        if not 0 <= n < len(cues):
            return None
        return {"uuid": pres["uuid"], "index": n,
                "v": _rev(pres["uuid"], n, [c.get("text", "") for c in cues])}

    def _timer(self, now: float) -> dict | None:
        f = self.f
        want = pp.settings()["view"]["timer"]
        if not (want and f.up):
            return None
        cfg = pp.settings()["timers"]
        for t in f.timers():
            if t["id"] != want:
                continue
            value, state = t["value"], t["state"]
            if state in pp.RUNNING_STATES and f.timers_read_at:
                dt = min(max(now - f.timers_read_at, 0.0), CARRY_MAX_S)
                value += -dt if t["dir"] == "down" else dt
            value = _round(value)
            return {"id": t["id"], "name": t["name"], "state": state, "dir": t["dir"], "value": value,
                    "level": pp.timer_level(value, state, t["dir"], cfg["amber_s"], cfg["red_s"])}
        return None

    def _media(self, now: float) -> dict | None:
        m = self.f.media
        if not (self.f.up and m and m["duration"] > 0):
            return None            # no media on the slide, or one with no length (a still): no progress to show
        t = m["time"]
        if m["playing"]:
            t += min(max(now - m["read_at"], 0.0), CARRY_MAX_S)
        t = min(max(t, 0.0), m["duration"])
        return {"name": m["name"], "playing": m["playing"], "time": _round(t), "duration": _round(m["duration"]),
                "remaining": _round(m["duration"] - t), "percent": round(100.0 * t / m["duration"], 1)}

    def snapshot(self, base: str) -> dict:
        """What the page draws. `base` is the URL prefix of the image route (admin or public)."""
        f, now = self.f, time.time()
        out: dict[str, Any] = {"configured": pp.configured(), "connected": bool(f.up), "live": False,
                               "view_only": bool(pp.settings()["view_only"]), "server_time": now,
                               "title": "", "part": "", "part_color": "", "slide_number": None, "slide_count": 0,
                               "current": {"text": "", "image": ""}, "next": {"text": "", "image": ""},
                               "timer": None, "media": None}
        if not (f.up and f.presentation):
            return out
        item = f.item() or {}
        out.update(live=True, title=item.get("title") or f.presentation["name"], part=item.get("part", ""),
                   part_color=item.get("part_color", ""), slide_number=item.get("slide_number"),
                   slide_count=item.get("slide_count", 0),
                   timer=self._timer(now), media=self._media(now))
        for which, side in (("current", f.current), ("next", f.next)):
            tgt = self.target(which)
            out[which] = {"text": side["text"], "image": f"{base}/image/{which}?v={tgt['v']}" if tgt else ""}
        return out

    # -- the images ----------------------------------------------------------------------------------------
    async def image(self, which: str, v: str) -> tuple[str, bytes]:
        """The picture of the slide that is `which` right now, if `v` is still its marker. LookupError when the
        slide has moved on (the page asks again with the new marker) or there is nothing to draw."""
        tgt = self.target(which)
        if tgt is None or tgt["v"] != str(v):
            raise LookupError(which)
        return await self.ctl.thumbnail(tgt["uuid"], tgt["index"], tgt["v"], LIVE_QUALITY)


view = View()
