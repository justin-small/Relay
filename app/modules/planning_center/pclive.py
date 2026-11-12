"""Follows today's plan for each chosen Planning Center service type and keeps
its LIVE state current.

A background task (like scheduler.py) does two things per followed service type:

- Finds today's plan. Planning Center has no "today" filter, so Relay lists the
  next plans with their times and picks the one with a `service` time today in
  the organisation's time zone (planningcenter.pick_plan). Re-checked every few
  minutes, when the local date turns over, and when the operator picks a
  different plan for today in admin. A service still running at midnight keeps
  its plan until it ends.
- Polls the plan's LIVE state. Fast (about every 2 s) only from an hour before
  a service time until shortly after it, or while someone is running LIVE;
  every few minutes otherwise. The fast rate is stretched when several service
  types are hot at once so that all of them together stay under half of the
  rate limit Planning Center last reported.

Planning Center has no confirmed push for LIVE, so this polls. Every call goes
through planningcenter.Client on a worker thread; nothing is sent until a token
is saved and a service type is followed.

The state is read by the admin page and by the panels' Service feed
(servicefeed.py), which registers as a listener.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from ... import config, schedules
from . import planningcenter as pc

log = logging.getLogger("relay.pclive")

TICK_S = 0.5
FAST_S = 2.0            # LIVE poll while a service is near or running
SLOW_S = 180.0          # LIVE poll the rest of the time
PLAN_CHECK_S = 300.0    # look for today's plan (and re-read its items) this often
PLAN_CHECK_FAST_S = 30.0  # ...and this often near or during a service, so plan edits reach panels soon
ERROR_RETRY_S = 20.0    # after a call fails
AUTH_RETRY_S = 300.0    # after Planning Center refuses the token: no point hammering
ZONE_RETRY_S = 300.0
RATE_SHARE = 0.5        # fast polling may use at most this much of the rate limit
LOOKBACK = timedelta(hours=12)   # list plans from this long before local midnight


def fast_interval(hot: int, rate: dict) -> float:
    """Seconds between LIVE polls of one service type while `hot` of them are
    polling fast, keeping their total under RATE_SHARE of the reported limit."""
    limit, period = rate.get("limit") or 100, rate.get("period") or 20
    return max(FAST_S, max(1, hot) * period / (limit * RATE_SHARE))


class Track:
    """One followed service type."""

    def __init__(self, st: str):
        self.st = st
        self.plans: list[dict] = []        # the last listing, for the admin picker
        self.plan: dict | None = None
        self.override: str | None = None   # plan id the operator picked for today
        self.items: list[dict] = []
        self.live: dict | None = None
        self.day: date | None = None
        self.plan_due = 0.0
        self.live_due = 0.0
        self.fast = False
        self.error: str | None = None
        self.reachable = True
        self.updated_at: float | None = None       # last good LIVE read
        self.service_start: dict[str, float] = {}  # earliest item start seen, per plan time
        self._asked: tuple | None = None            # the one-off refresh already made for this item


class Follower:
    def __init__(self):
        self.tracks: dict[str, Track] = {}
        self.rev = 0
        self.zone: ZoneInfo | None = None
        self.zone_name = ""
        self.names: dict[str, str] = {}
        self.listeners: list[Callable[[], None]] = []
        self.make_client: Callable[[dict], Any] = pc.client_from   # a test swaps this
        self._client: Any = None
        self._token: tuple | None = None
        self._zone_due = 0.0
        # The token owner's Planning Center person id: LIVE's controller is compared with it, so a panel can tell
        # whether Relay itself has control (Take control or Release). Read once per token.
        self.me_id: str | None = None
        self._me_due = 0.0
        self._names_for: tuple | None = None
        self._sig = ""
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Planning Center follow tick failed")
            await asyncio.sleep(TICK_S)

    async def poke(self) -> None:
        """Re-evaluate straight away, after the token, the followed service
        types or the operator's plan pick changes."""
        for tr in self.tracks.values():
            tr.plan_due = 0.0
        try:
            await self.tick()
        except Exception:
            log.exception("Planning Center follow tick failed")

    @staticmethod
    async def _call(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    def fallback_zone(self) -> ZoneInfo:
        return ZoneInfo(schedules.default_timezone(os.environ.get("TZ")))

    def next_due(self) -> float:
        """Epoch of the next call this follower wants to make (for tests and pacing)."""
        return min((min(t.plan_due, t.live_due) if t.plan else t.plan_due
                    for t in self.tracks.values()), default=float("inf"))

    # -- the loop body -------------------------------------------------
    async def tick(self, now: datetime | None = None) -> None:
        async with self._lock:
            await self._tick(now or datetime.now(timezone.utc))

    async def _tick(self, now: datetime) -> None:
        stored = config.get()["planning_center"]
        ids = list(stored["service_types"])
        token = (stored["app_id"], stored["secret"])
        if not (token[0] and token[1] and ids):
            self.tracks.clear()
            if not (token[0] and token[1]):
                self._client = self._token = self.zone = self._names_for = self.me_id = None
                self.zone_name, self.names, self._zone_due, self._me_due = "", {}, 0.0, 0.0
            self._note_change()
            return
        if token != self._token:
            self._client, self._token = self.make_client(config.get()), token
            self.tracks.clear()
            self.zone, self.zone_name, self._zone_due, self._names_for = None, "", 0.0, None
            self.me_id, self._me_due = None, 0.0
        client, ts = self._client, now.timestamp()

        for st in [s for s in self.tracks if s not in ids]:
            del self.tracks[st]
        for st in ids:
            self.tracks.setdefault(st, Track(st))

        if self.zone is None and ts >= self._zone_due:
            await self._read_zone(client, ts)
        if self.me_id is None and ts >= self._me_due:
            try:
                self.me_id = await self._call(client.me) or None
            except pc.PlanningCenterError:
                self._me_due = ts + ERROR_RETRY_S
        if self._names_for != tuple(ids):
            await self._read_names(client, ids)
        zone = self.zone or self.fallback_zone()
        day = pc.local_day(now, zone)

        for st, tr in self.tracks.items():
            ov = stored["plan_overrides"].get(st)
            override = ov["plan"] if ov and ov["day"] == day.isoformat() else None
            if ts >= tr.plan_due or tr.day != day or tr.override != override:
                await self._guard(tr, ts, "plan_due", self._check_plan, client, tr, now, zone, day, override)
            if tr.plan is not None and ts >= tr.live_due:
                await self._guard(tr, ts, "live_due", self._poll_live, client, tr, ts)
        self._note_change()

    async def _guard(self, tr: Track, ts: float, due: str, step, *args) -> None:
        try:
            await step(*args)
            tr.error, tr.reachable = None, True
        except pc.PlanningCenterError as exc:
            wait = exc.retry_after or (AUTH_RETRY_S if exc.kind == "auth" else ERROR_RETRY_S)
            if str(exc) != tr.error:
                log.warning("Planning Center, service type %s: %s", tr.st, exc)
            tr.error, tr.reachable = str(exc), exc.kind != "network"
            setattr(tr, due, ts + wait)

    async def _read_zone(self, client, ts: float) -> None:
        try:
            name = await self._call(client.time_zone)
        except pc.PlanningCenterError as exc:
            log.warning("Planning Center time zone not read (%s); using %s for now",
                        exc.kind, self.fallback_zone().key)
            self._zone_due = ts + ERROR_RETRY_S
            return
        self.zone_name, self.zone = name, pc.zone_for(name)
        if self.zone is None:
            # Asked and answered, but not a zone Relay can place: say so once
            # and carry on in Relay's own zone rather than guess silently.
            log.warning("Planning Center time zone %r is not one Relay knows; using %s",
                        name, self.fallback_zone().key)
            self._zone_due = ts + ZONE_RETRY_S

    async def _read_names(self, client, ids: list[str]) -> None:
        try:
            self.names = {t["id"]: t["name"] for t in await self._call(client.service_types)}
        except pc.PlanningCenterError:
            pass  # names are a nicety; the ids still work
        self._names_for = tuple(ids)

    async def _check_plan(self, client, tr: Track, now: datetime, zone: ZoneInfo,
                          day: date, override: str | None) -> None:
        ts = now.timestamp()
        # Set first: a failure must not retry every tick.
        tr.plan_due = ts + (PLAN_CHECK_FAST_S if tr.fast else PLAN_CHECK_S)
        midnight = datetime.combine(day, dtime.min, tzinfo=zone)
        plans = await self._call(client.plans_from, tr.st, midnight - LOOKBACK)
        tr.plans, tr.day, tr.override = plans, day, override
        by_id = {p["id"]: p for p in plans}
        if override:
            chosen = by_id.get(override) or await self._call(client.plan, tr.st, override)
        else:
            chosen = pc.pick_plan(plans, now, zone)
            if tr.plan is not None and pc.running(tr.live, ts) and (
                    chosen is None or chosen["id"] != tr.plan["id"]):
                # Still being run (past midnight, or over its planned end):
                # do not pull the plan out from under the service.
                chosen = by_id.get(tr.plan["id"]) or tr.plan
        if (chosen or {}).get("id") != (tr.plan or {}).get("id"):
            log.info("Planning Center, service type %s: following %s", tr.st,
                     f"plan {chosen['id']} ({chosen['dates'] or 'no date'})" if chosen else "no plan today")
            tr.items, tr.live, tr.live_due, tr.service_start, tr._asked = [], None, 0.0, {}, None
            tr.fast = False
        tr.plan = chosen
        if chosen is not None:
            tr.items = await self._call(client.items, tr.st, chosen["id"])

    async def _poll_live(self, client, tr: Track, ts: float) -> None:
        plan = tr.plan
        tr.live_due = ts + (FAST_S if tr.fast else SLOW_S)  # set first, as above
        live = await self._call(client.live, tr.st, plan["id"])
        tr.live, tr.updated_at = live, ts
        cur = live["current"]
        if cur and cur["live_start_at"] and cur["plan_time"]:
            seen = tr.service_start.get(cur["plan_time"])
            tr.service_start[cur["plan_time"]] = min(seen or cur["live_start_at"], cur["live_start_at"])
        if cur and tr._asked != (cur["id"], cur["item"]):
            # A new current item: once, fill in what the last listing lacks --
            # the service's live_starts_at, or an item added since.
            tr._asked = (cur["id"], cur["item"])
            pt = next((t for t in plan["times"] if t["id"] == cur["plan_time"]), None)
            if pt is None or not pt["live_starts_at"]:
                tr.plan = plan = await self._call(client.plan, tr.st, plan["id"])
            if not any(i["id"] == cur["item"] for i in tr.items):
                tr.items = await self._call(client.items, tr.st, plan["id"])
        tr.fast = pc.near_service(plan, ts) or pc.running(live, ts)
        if tr.fast:
            tr.plan_due = min(tr.plan_due, ts + PLAN_CHECK_FAST_S)
        hot = sum(1 for t in self.tracks.values() if t.fast)
        tr.live_due = ts + (fast_interval(hot, client.rate) if tr.fast else SLOW_S)

    # -- LIVE buttons (pccontrol.py) -------------------------------------
    def client(self):
        """The client for the saved token, shared with the poll so both count
        against the same rate-limit reading. None until the first tick."""
        return self._client

    def apply_live(self, st: str, live: dict, ts: float | None = None) -> None:
        """A LIVE state Planning Center just answered an action with: shown to
        every panel at once, and the next poll comes soon to fill in the rest."""
        tr = self.tracks.get(st)
        if tr is None:
            return
        ts = datetime.now(timezone.utc).timestamp() if ts is None else ts
        tr.live, tr.updated_at, tr.error, tr.reachable = live, ts, None, True
        cur = live.get("current")
        if cur and cur["live_start_at"] and cur["plan_time"]:
            seen = tr.service_start.get(cur["plan_time"])
            tr.service_start[cur["plan_time"]] = min(seen or cur["live_start_at"], cur["live_start_at"])
        tr.fast = True
        tr.live_due = min(tr.live_due, ts + FAST_S)
        self._note_change()

    # -- state ----------------------------------------------------------
    def _note_change(self) -> None:
        sig = json.dumps([self.me_id] + [[t.st, t.plan, t.items, t.live, t.error, t.override,
                           [p["id"] for p in t.plans]] for t in self.tracks.values()], sort_keys=True)
        if sig != self._sig:
            self._sig = sig
            self.rev += 1
            for fn in list(self.listeners):
                try:
                    fn()
                except Exception:
                    log.exception("Planning Center listener failed")

    def state(self, st: str, now: float | None = None) -> dict | None:
        """Everything known about one followed service type, timers worked out for `now`."""
        tr = self.tracks.get(st)
        if tr is None:
            return None
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        live = tr.live or {}
        cur = live.get("current")
        start = tr.service_start.get(cur["plan_time"]) if cur and cur["plan_time"] else None
        return {
            "service_type": st,
            "name": self.names.get(st) or f"Service type {st}",
            "day": tr.day.isoformat() if tr.day else None,
            "plan": tr.plan,
            "override": tr.override is not None,
            "items": tr.items,
            # Nobody controlling is "not live", not an error.
            "live": bool(live.get("controller")),
            "live_id": live.get("id"),
            "controller": live.get("controller"),
            "timers": pc.timers(tr.plan, tr.items, tr.live, now, start),
            "polling": "fast" if tr.fast else "slow",
            "reachable": tr.reachable,
            "error": tr.error,
            "updated_at": tr.updated_at,
        }

    def snapshot(self, now: float | None = None) -> dict:
        """For the admin page: every followed service type, plus the plans the
        operator can pick from for today. Item notes are left out."""
        types = []
        for st, tr in self.tracks.items():
            s = self.state(st, now)
            s["items"] = [{k: v for k, v in i.items() if k != "notes"} for i in s["items"]]
            s["plans"] = [{"id": p["id"], "title": p["title"], "dates": p["dates"],
                           "sort_date": p["sort_date"]} for p in tr.plans]
            types.append(s)
        return {
            "rev": self.rev,
            "time_zone": (self.zone or self.fallback_zone()).key,
            # True while the day boundary is Relay's zone, not Planning Center's.
            "time_zone_guessed": self.zone is None,
            "rate": dict(getattr(self._client, "rate", None) or {}),
            "service_types": types,
        }


follower = Follower()
