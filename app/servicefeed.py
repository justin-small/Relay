"""The Service feed: today's plan and its LIVE state, for caption panels.

    GET /stream?stream=service&type=<service_type_id>

On the viewer port with no login, like the caption feeds. Each `service`
message carries the whole current state, so a panel that reconnects needs
nothing else. A new one is sent only when something a panel shows changes;
between them a small `service_tick` every heartbeat carries Relay's clock and
when Planning Center last answered, so the panel can count timers down on its
own and say how fresh they are.

Timers are sent as they stood at `server_time`. The panel works them out
for later times itself:
    item_left_s(t)    = item_left_s - (t - server_time)
    service_over_s(t) = service_over_s + max(0, (t - server_time) - max(0, item_left_s))
(the service only falls further behind once the current item has run over).

Item notes can hold private things (names, pastoral notes), so they are sent
only while the operator has "Show item notes on panels" on. The Planning
Center token never reaches this feed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from . import config, planningcenter as pc
from .hub import sse_frame
from .pclive import follower

log = logging.getLogger("relay.servicefeed")

QUEUE_MAX = 32
ITEM_TYPES = ("song", "header", "media", "item")
# Fields that move with the clock alone: left out of the "has it changed" test.
_CLOCK_FIELDS = ("server_time", "updated_at", "item_left_s", "service_over_s")


def show_notes() -> bool:
    return bool(config.get()["planning_center"].get("show_notes"))


class ServiceFeed:
    def __init__(self, follower):
        self.follower = follower
        self.subs: dict[str, set[asyncio.Queue]] = {}
        self._sig: dict[str, str] = {}
        self._offered: list[dict] = []
        # Called when the list of service feeds a panel can pick changes
        # (main.py re-sends the `targets` message).
        self.on_offered_change = None
        follower.listeners.append(self.refresh)

    # -- what the panel menu offers -------------------------------------
    def offered(self) -> list[dict]:
        """One entry per followed service type, for /api/targets and `targets`."""
        if not pc.connected(config.get()):
            return []
        return [{"service_type": st, "label": self.follower.names.get(st) or f"Service type {st}"}
                for st in self.follower.tracks]

    # -- the message ----------------------------------------------------
    def payload(self, st: str, now: float | None = None) -> dict:
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        s = self.follower.state(st, now) if pc.connected(config.get()) else None
        if s is None:
            # Not (or no longer) followed: the panel says so and keeps listening.
            return {"type": "service", "service_type": st, "followed": False, "server_time": now}
        notes = show_notes()
        plan = s["plan"]
        items = []
        for i in s["items"]:
            out = {"id": i["id"], "title": i["title"],
                   "type": i["type"] if i["type"] in ITEM_TYPES else "item",
                   "length": i["length"], "position": i["position"]}
            if i["type"] == "song":
                out["key"] = i["key"]
                out["arrangement"] = i["arrangement"]
                out["sequence"] = i["arrangement_sequence"]
            if notes and i["notes"]:
                out["notes"] = [{"category": n["category"], "content": n["content"]} for n in i["notes"]]
            items.append(out)
        t = s["timers"]
        return {
            "type": "service",
            "service_type": st,
            "followed": True,
            "name": s["name"],
            "plan": {"id": plan["id"], "title": plan["title"], "series_title": plan["series_title"],
                     "dates": plan["dates"]} if plan else None,
            "items": items,
            "notes": notes,
            "current_item": t["current_item"],
            "next_item": t["next_item"],
            "item_started_at": t["item_started_at"],
            "item_length_s": t["item_length_s"],
            "item_left_s": t["item_left_s"],
            "service_started_at": t["service_started_at"],
            "service_over_s": t["service_over_s"],
            "live": s["live"],
            "controller": (s["controller"] or {}).get("name"),
            # The reason stays in admin; a panel only says Planning Center is unreachable.
            "reachable": bool(s["reachable"] and not s["error"]),
            "updated_at": s["updated_at"],
            "server_time": now,
        }

    def tick_payload(self, st: str, now: float | None = None) -> dict:
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        tr = self.follower.tracks.get(st)
        return {"type": "service_tick", "service_type": st, "server_time": now,
                "updated_at": tr.updated_at if tr else None}

    @staticmethod
    def _signature(msg: dict) -> str:
        return json.dumps({k: v for k, v in msg.items() if k not in _CLOCK_FIELDS}, sort_keys=True)

    # -- subscribers ----------------------------------------------------
    def subscribe(self, st: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        msg = self.payload(st)
        self._sig.setdefault(st, self._signature(msg))
        q.put_nowait(sse_frame(msg))
        self.subs.setdefault(st, set()).add(q)
        return q

    def unsubscribe(self, st: str, q: asyncio.Queue) -> None:
        subs = self.subs.get(st)
        if subs is not None:
            subs.discard(q)
            if not subs:
                del self.subs[st]
                self._sig.pop(st, None)

    def refresh(self) -> None:
        """Re-send each watched service type whose state changed. Called by the
        follower when it notices a change, and when the notes switch moves."""
        offered = self.offered()
        if offered != self._offered:
            self._offered = offered
            if self.on_offered_change:
                try:
                    self.on_offered_change()
                except Exception:
                    log.exception("targets push failed")
        for st, subs in list(self.subs.items()):
            msg = self.payload(st)
            sig = self._signature(msg)
            if sig == self._sig.get(st):
                continue
            self._sig[st] = sig
            frame = sse_frame(msg)
            for q in list(subs):
                try:
                    q.put_nowait(frame)
                except asyncio.QueueFull:
                    # A panel that stopped reading: drop it, it reconnects for a fresh state.
                    subs.discard(q)

    def viewer_count(self) -> int:
        return sum(len(s) for s in self.subs.values())


feed = ServiceFeed(follower)
