"""Next / Previous / Take control / Release from a caption panel (#80).

    POST /api/panels/{id}/service/{next|previous|take_control|release}
    body: {"key": "<the panel's control key>", "type": "<service type id>"}

On the viewer port, like everything a panel sends: no login and plain HTTP.
What bounds a stranger on the LAN:

* a panel may press only while the operator has set its service mode to
  "control" or "both" in admin. Every panel starts "off";
* it must send the random key Relay gave it when control was turned on;
* a quarter second between presses from one panel (the panel itself sends
  one at a time, so this only stops a flood);
* the press goes to a service type Relay follows, and to today's plan for it.

The panel never holds the Planning Center token. Relay presses the button as
the token's owner, and the answer (the new LIVE state) goes straight to every
panel on the Service feed.

What Planning Center does, checked against a real account:
* the action paths are .../plans/{plan}/live/{action}, with no LIVE id;
* Next and Previous are refused (403) unless the token's owner has control;
* toggle_control takes control, and releases it when the owner already has
  it. So Take control does nothing when Relay's person already has control,
  and Release does nothing when they don't. Whether it takes control from
  someone else is not checked yet (it needs a second person).
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

from . import planningcenter as pc

log = logging.getLogger("relay.pccontrol")

ACTIONS = ("next", "previous", "take_control", "release")
MIN_GAP_S = 0.25   # was 1 s: a quick second Next was refused as "too fast"
LOG_MAX = 100
BODY_MAX_BYTES = 512

# What a panel is told when a press does not go through: a short code it
# words itself, and an HTTP status. The reason stays in Relay's log.
REFUSALS = {
    "not_allowed": 403,       # this panel may not control
    "bad_key": 403,
    "too_fast": 429,
    "not_followed": 409,      # no service type, or one Relay does not follow
    "no_plan": 409,           # nothing planned today
    "not_controlling": 409,   # Next / Previous before Take control
    "not_connected": 503,     # no Planning Center token
    "unreachable": 502,       # Planning Center did not answer
    "refused": 502,           # Planning Center said no for another reason
}


class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
        self.status = REFUSALS[code]


class Control:
    def __init__(self, follower, registry, feed):
        self.follower = follower
        self.registry = registry
        self.feed = feed
        self.actions: deque[dict[str, Any]] = deque(maxlen=LOG_MAX)
        self._last: dict[str, float] = {}
        self._me: tuple[Any, str] | None = None   # (client, person id)
        self._lock = asyncio.Lock()
        self.clock = time.monotonic   # a test swaps this
        # Called after each press, for the admin status stream.
        self.on_action = None

    # -- checks -----------------------------------------------------------
    def _authorize(self, pid: str, body: dict) -> None:
        """Refused unless this panel may control and sent its key. Not logged:
        anyone on the LAN can get this far, and must not be able to flood the
        operator's list."""
        want = self.registry.control_key(pid)
        if want is None:
            raise Refused("not_allowed")
        key = body.get("key")
        if not isinstance(key, str) or not hmac.compare_digest(key.encode(), want.encode()):
            raise Refused("bad_key")

    def _target(self, pid: str, body: dict) -> str:
        """The service type to act on, or Refused."""
        now = self.clock()
        if now - self._last.get(pid, -1e9) < MIN_GAP_S:
            raise Refused("too_fast")
        self._last[pid] = now
        offered = [o["service_type"] for o in self.feed.offered()]
        if not offered and not self.follower.client():
            raise Refused("not_connected")
        st = self.registry.service(pid).get("service_type") or str(body.get("type") or "")
        if not st and len(offered) == 1:
            st = offered[0]
        if st not in offered:
            raise Refused("not_followed")
        tr = self.follower.tracks.get(st)
        if tr is None or tr.plan is None:
            raise Refused("no_plan")
        return st

    async def _call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def _my_id(self, client) -> str:
        if getattr(self.follower, "me_id", None):
            return self.follower.me_id
        if self._me is None or self._me[0] is not client:
            self._me = (client, await self._call(client.me))
        return self._me[1]

    # -- the press -----------------------------------------------------------
    async def press(self, pid: str, action: str, body: dict) -> dict:
        """Carry out one press. Returns the panel's Service feed message with
        the new state; raises Refused otherwise. Every attempt that gets past
        the key is logged."""
        if action not in ACTIONS:
            raise ValueError(action)
        self._authorize(pid, body)
        st = str(body.get("type") or "")[:20]
        try:
            st = self._target(pid, body)
            async with self._lock:   # one press at a time across panels: no interleaved toggles
                did = await self._press(st, action)
        except Refused as exc:
            self._record(pid, st, action, exc.code)
            raise
        self._record(pid, st, action, "ok" if did else "unchanged")
        return self.feed.payload(st)

    async def _press(self, st: str, action: str) -> bool:
        client = self.follower.client()
        if client is None:
            raise Refused("not_connected")
        plan = self.follower.tracks[st].plan["id"]
        try:
            if action in ("take_control", "release"):
                live = await self._call(client.live, st, plan)
                me = await self._my_id(client)
                mine = bool(live["controller"] and live["controller"]["id"] == me)
                if mine == (action == "take_control"):
                    self.follower.apply_live(st, live)
                    return False   # already as asked: toggling would undo it
                live = await self._call(client.live_action, st, plan, "toggle_control")
            else:
                name = "go_to_next_item" if action == "next" else "go_to_previous_item"
                live = await self._call(client.live_action, st, plan, name)
        except pc.PlanningCenterError as exc:
            if exc.kind == "network":
                raise Refused("unreachable") from None
            if exc.status == 403 and action in ("next", "previous"):
                raise Refused("not_controlling") from None
            log.warning("Planning Center refused %s for service type %s: %s", action, st, exc)
            raise Refused("refused") from None
        self.follower.apply_live(st, live)
        return True

    def _record(self, pid: str, st: str, action: str, result: str) -> None:
        name = self.registry.display_name(pid)
        entry = {"at": datetime.now(timezone.utc).timestamp(), "panel": pid, "panel_name": name,
                 "service_type": st, "action": action, "result": result}
        self.actions.appendleft(entry)
        log.info("Panel %s (%s): %s on service type %s -> %s", name, pid[:8], action, st, result)
        if self.on_action:
            try:
                self.on_action()
            except Exception:
                log.exception("control listener failed")

    def recent(self) -> list[dict[str, Any]]:
        return list(self.actions)
