"""The runner that starts and stops the engine on the configured schedules.

A background task ticks every few seconds and compares "now" with the merged
schedule windows (see schedules.py). The rules, in the order they bite:

- Inside a window and not running: start. A relay restarted mid-window catches
  up the same way; a window missed entirely is not run after the fact.
- Inside a window and already running (a manual start, or an earlier window
  that ran into this one): leave it alone, and stop it at this window's end.
- The operator presses Stop inside a window: that window is held -- nothing
  restarts until the next scheduled start. A manual Start clears the hold.
- A manual Start outside any window is never stopped by the scheduler.
- A schedule edited or deleted while its session is live never cuts the
  session off; it is released to the operator to stop by hand.
- A start that fails (no key, no audio device) is retried on the next tick
  for as long as the window is open. The engine already carries the error.

The hold is in memory only, so a relay restart during a held window starts the
session again -- the catch-up rule wins over a stop nobody can remember.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from . import config, schedules
from .engine import demo_mode
from .stream import stream
from . import modules

log = logging.getLogger("relay.scheduler")

TICK_S = 5.0


def plan_source() -> schedules.PlanSource | None:
    """Today's Planning Center service times, while that module is on (None otherwise)."""
    return modules.using("schedules.plan_times")


def engine_schedules() -> list[dict]:
    """The named schedules the operator picked to start and stop the engine."""
    cfg = config.get()
    return [s for s in (schedules.find(cfg["schedules"], r) for r in cfg["engine_schedules"]) if s]


class Scheduler:
    def __init__(self):
        # End (UTC) of the window whose session this runner is managing, or
        # None when the session, if any, belongs to the operator.
        self.owned_until: datetime | None = None
        self.owned_tz: str = schedules.DEFAULT_TIMEZONE
        # Start (UTC) of the window the operator stopped inside.
        self.held: datetime | None = None
        # Which schedule started the live session, for the panel.
        self.started_by: str | None = None
        self._fail: str | None = None
        self._sig: tuple | None = None
        self._task: asyncio.Task | None = None

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
                log.exception("schedule tick failed")
            await asyncio.sleep(TICK_S)

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    # -- the decision --------------------------------------------------
    async def tick(self, now: datetime | None = None) -> None:
        now = now or self.now()
        scheds = engine_schedules()
        win = schedules.current_window(scheds, now, config.timezone(), plan_source())

        if not stream.running:
            self.started_by = None

        if win is None:
            self.held = None
            if self.owned_until is not None:
                owned, self.owned_until = self.owned_until, None
                if stream.running and now >= owned:
                    log.info("Scheduled window ended: stopping")
                    await stream.stop()
                    self.started_by = None
                elif stream.running:
                    log.info("Schedule changed under a live session: leaving it "
                             "running for the operator to stop")
            self._fail = None
        elif self.held == win.start:
            pass
        elif stream.running:
            self.owned_until, self.owned_tz = win.stop, win.tz
        else:
            await self._start(win)

        self._push_if_changed(now)

    async def _start(self, win: schedules.Window) -> None:
        label = ", ".join(win.names)
        if demo_mode():
            # A rehearsal must never open a billed session, and a schedule
            # coming due mid-rehearsal is the easy way to do it by accident.
            # Skip quietly (logged once per window), rather than retrying the
            # engine's refusal every tick.
            if self._fail != "demo":
                log.info("Scheduled start (%s) skipped: rehearsal mode", label)
                self._fail = "demo"
            return
        try:
            await stream.start()
        except RuntimeError as exc:
            msg = str(exc)
            if msg != self._fail:
                log.warning("Scheduled start (%s) failed, will retry: %s", label, msg)
                self._fail = msg
            return
        self._fail = None
        self.owned_until, self.owned_tz = win.stop, win.tz
        self.started_by = label
        log.info("Scheduled start: %s, until %s", label,
                 schedules.fmt_instant(win.stop, win.tz))

    # -- operator hooks ------------------------------------------------
    def manual_stop(self, now: datetime | None = None) -> None:
        """The operator pressed Stop: hold the current window, if any."""
        now = now or self.now()
        win = schedules.current_window(engine_schedules(), now, config.timezone(), plan_source())
        self.held = win.start if win else None
        self.owned_until = None
        self.started_by = None

    def manual_start(self) -> None:
        """The operator pressed Start: release any hold. Inside a window the
        next tick adopts the session and stops it at the window's end."""
        self.held = None
        self.started_by = None

    async def poke(self) -> None:
        """Re-evaluate straight away, after the schedule list changes."""
        try:
            await self.tick()
        except Exception:
            log.exception("schedule tick failed")

    # -- status --------------------------------------------------------
    def status(self, now: datetime | None = None) -> dict:
        now = now or self.now()
        scheds = engine_schedules()
        tz, plan = config.timezone(), plan_source()
        win = schedules.current_window(scheds, now, tz, plan)
        nxt = schedules.next_window(scheds, now, tz, plan)

        stop = None
        if self.owned_until is not None and stream.running:
            stop = {"at": self.owned_until.timestamp(),
                    "label": schedules.fmt_instant(self.owned_until, self.owned_tz)}
        elif nxt is not None:
            stop = {"at": nxt.stop.timestamp(), "label": schedules.fmt_instant(nxt.stop, nxt.tz)}

        return {
            "count": len(scheds),
            "enabled": sum(1 for s in scheds if s.get("enabled")),
            "active_schedule": ", ".join(win.names) if win else None,
            "started_by_schedule": self.started_by if stream.running else None,
            "held": bool(win and self.held == win.start),
            "next_scheduled_start": {
                "at": nxt.start.timestamp(),
                "label": schedules.fmt_instant(nxt.start, nxt.tz),
                "names": nxt.names,
            } if nxt else None,
            "next_scheduled_stop": stop,
        }

    def _push_if_changed(self, now: datetime) -> None:
        st = self.status(now)
        sig = (st["active_schedule"], st["started_by_schedule"], st["held"],
               (st["next_scheduled_start"] or {}).get("at"),
               (st["next_scheduled_stop"] or {}).get("at"))
        if sig != self._sig:
            self._sig = sig
            stream.push_status()

    # -- the list the panel edits ---------------------------------------
    def listing(self, now: datetime | None = None) -> dict:
        now = now or self.now()
        cfg = config.get()
        tz, plan = cfg["timezone"], plan_source()
        items = []
        for s in cfg["schedules"]:
            nxt = schedules.next_window([dict(s, enabled=True)], now, tz, plan)
            items.append(dict(
                s,
                ref=schedules.ref(s["id"]),
                summary=schedules.summary(s),
                windows=[dict(w, summary=schedules.window_summary(w),
                              start_date_us=schedules.fmt_date(w.get("start_date")),
                              end_date_us=schedules.fmt_date(w.get("end_date"))) for w in s["windows"]],
                expired=schedules.is_expired(s, now, tz),
                live=schedules.is_open(s, now, tz, plan),
                next_start=schedules.fmt_instant(nxt.start, tz) if nxt else None,
                engine=schedules.ref(s["id"]) in cfg["engine_schedules"],
                used_by=used_by(s["id"]),
            ))
        return {
            "schedules": items,
            "timezone": tz,
            "timezones": [{"id": z, "label": l} for z, l in schedules.US_TIMEZONES],
            "plan_times": plan is not None,
            "status": self.status(now),
        }


def used_by(sid: str) -> list[dict]:
    """Every place that picks schedule `sid`: [{"where": "Signage", "what": "Lobby slot"}...]. The engine is core's;
    each module that picks schedules offers what it uses as `schedules.used.<module>` (a function taking the ref)."""
    want = schedules.ref(sid)
    out = [{"where": "Engine", "what": "Starts and stops live captions"}] if want in config.get()["engine_schedules"] else []
    from . import boards

    out += boards.schedule_users(want)
    for _name, fn in modules.using_all("schedules.used.").items():
        out += fn(want)
    return out


scheduler = Scheduler()
