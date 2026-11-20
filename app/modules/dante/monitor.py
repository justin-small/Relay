"""Dante monitoring (#258): what is wrong on the Dante network, what changed, and the history that survives a restart.

`Monitor.observe(devices)` takes what a scan read (each device record carries `reachable`, `clock`, `subscriptions`)
and works out, with no network of its own:

* the problems now (a device gone, a subscription that does not resolve, a clock that is unlocked or counting errors),
  which become status cards and, through the stage alert named "Dante" when the operator has made one, a flash;
* the changes since last time (device appeared or vanished, subscription changed, clock leader changed), which go to
  the history in the module's own tables.

A problem raises once when it starts and clears when it ends. Two scans in a row must miss a device before it counts as
gone, so one dropped announcement is not an outage.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

from ... import db, text

log = logging.getLogger("relay.dante")

MISSING_SCANS = 2
KEEP_HISTORY = 1000
STAGE_BUTTON = "Dante"
KINDS = ("appeared", "vanished", "subscription", "clock_leader", "problem", "recovered")


def sub_map(device: dict) -> dict[int, tuple[str, str]]:
    return {s["channel"]: (s["status"], s["source"]) for s in (device.get("subscriptions") or [])}


def leader_of(devices: list[dict]) -> str:
    return next((d["name"] for d in devices if (d.get("clock") or {}).get("leader")), "")


def problems(devices: list[dict], gone: dict[str, str], before: dict[str, dict]) -> dict[str, str]:
    """{problem key: sentence}: one entry per thing that is wrong right now. `gone` is the devices missing long enough
    to count (key -> name); `before` is each device's last clock reading, to see an error count still climbing."""
    out: dict[str, str] = {}
    for key, name in gone.items():
        out["offline:" + key] = f"{name} is not on the network"
    for d in devices:
        key, name = d["key"], d["name"]
        if d.get("reachable") is False:
            out["offline:" + key] = f"{name} does not answer on its control port"
        for s in d.get("subscriptions") or []:
            if s["status"] == "unresolved":
                out[f"unresolved:{key}:{s['channel']}"] = (
                    f"{name} receive channel {s['channel']} cannot find {s['source'] or 'its source'}")
        c = d.get("clock")
        if c:
            was = before.get(key) or {}
            if not c["locked"]:
                out["clock:" + key] = f"{name}'s clock is not locked"
            elif c["follower_errors"] > was.get("follower_errors", c["follower_errors"]):
                out["clock:" + key] = f"{name} is counting clock follower errors"
            elif c["interface_errors"] > was.get("interface_errors", c["interface_errors"]):
                out["clock:" + key] = f"{name} is counting network interface errors"
    return out


class Monitor:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self.active: dict[str, str] = {}            # problem key -> sentence
        self.recent: deque = deque(maxlen=KEEP_HISTORY)   # in memory while the database is down
        self._seen: dict[str, dict] = {}            # device key -> {"name", "subs", "clock", "missing"}
        self._leader = ""
        self._first = True
        self._relay_wrote: set[str] = set()
        self.stage: Callable[[str, bool], None] = lambda why, on: None

    # ---------------------------------------------------------------------------------------------- history
    def event(self, device: str, kind: str, detail: str = "") -> None:
        at = db.now()
        row = {"at": at, "device": device, "kind": kind, "detail": detail}
        with self._lock:
            self.recent.appendleft(row)
        try:
            db.execute("insert into dante_history (at, device, kind, detail) values (?, ?, ?, ?)",
                       (at, device, kind, detail))
            db.execute("delete from dante_history where id <= (select max(id) from dante_history) - ?",
                       (KEEP_HISTORY,))
        except db.Unavailable:
            log.warning("dante: history not saved, the database is down")
        except Exception:  # noqa: BLE001
            log.warning("dante: history not saved", exc_info=True)

    def history(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), KEEP_HISTORY))
        try:
            return db.query("select at, device, kind, detail from dante_history order by id desc limit ?", (limit,))
        except Exception:  # noqa: BLE001
            with self._lock:
                return list(self.recent)[:limit]

    def relay_wrote(self, device_key: str) -> None:
        """Routing (#259) says it is about to change this device's subscriptions, so the change is logged as Relay's."""
        self._relay_wrote.add(device_key)

    # ---------------------------------------------------------------------------------------------- stars
    def starred(self) -> set[tuple[str, str]]:
        try:
            return {(r["device"], r["channel"]) for r in db.query("select device, channel from dante_starred")}
        except Exception:  # noqa: BLE001
            return set()

    def star(self, device: str, channel: str, on: bool) -> None:
        if on:
            db.execute("insert or ignore into dante_starred (device, channel) values (?, ?)", (device, channel))
        else:
            db.execute("delete from dante_starred where device = ? and channel = ?", (device, channel))

    # ---------------------------------------------------------------------------------------------- the diff
    def observe(self, devices: list[dict]) -> None:
        live = {d["key"]: d for d in devices}
        first, self._first = self._first, False
        gone: dict[str, str] = {}
        before: dict[str, dict] = {}
        for key, old in list(self._seen.items()):
            before[key] = old.get("clock") or {}
            if key in live:
                continue
            old["missing"] += 1
            if old["missing"] == MISSING_SCANS:
                self.event(old["name"], "vanished", "stopped announcing on the network")
            if old["missing"] >= MISSING_SCANS:
                gone[key] = old["name"]
        for key, d in live.items():
            old = self._seen.get(key)
            subs = sub_map(d)
            if old is None:
                if not first:
                    self.event(d["name"], "appeared", ", ".join(d["ips"]))
            else:
                if old["missing"] >= MISSING_SCANS:
                    self.event(d["name"], "appeared", "back on the network")
                if d.get("subscriptions") is not None and old["subs"] is not None:
                    self._subscription_changes(d, old["subs"], subs)
            self._seen[key] = {"name": d["name"], "subs": subs if d.get("subscriptions") is not None else
                               (old or {}).get("subs"), "clock": d.get("clock") or (old or {}).get("clock"),
                               "missing": 0}
        leader = leader_of(devices)
        if leader and self._leader and leader != self._leader:
            self.event(leader, "clock_leader", f"clock leader changed from {self._leader}")
        self._leader = leader or self._leader
        self._settle(problems(devices, gone, before))
        for key in [k for k, v in self._seen.items() if v["missing"] > MISSING_SCANS * 20]:
            del self._seen[key]

    def _subscription_changes(self, d: dict, old: dict, new: dict) -> None:
        by = "Relay" if d["key"] in self._relay_wrote else "someone in Dante Controller or another controller"
        self._relay_wrote.discard(d["key"])
        for ch in sorted(set(old) | set(new)):
            if old.get(ch) == new.get(ch):
                continue
            was, now = old.get(ch, ("none", "")), new.get(ch, ("none", ""))
            if was[1] == now[1] and was[0] != now[0]:
                continue        # the same source resolving or not is a problem and a recovery, not a change
            self.event(d["name"], "subscription",
                       f"receive channel {ch}: {now[1] or 'unsubscribed'} (was {was[1] or 'unsubscribed'}), by {by}")

    def _settle(self, now: dict[str, str]) -> None:
        with self._lock:
            started = {k: v for k, v in now.items() if k not in self.active}
            ended = {k: v for k, v in self.active.items() if k not in now}
            was_quiet = not self.active
            self.active = dict(now)
        for why in started.values():
            self.event("", "problem", why)
        for why in ended.values():
            self.event("", "recovered", why)
        try:
            if started and was_quiet:
                self.stage(next(iter(started.values())), True)
            elif ended and not now:
                self.stage("", False)
        except Exception:  # noqa: BLE001
            log.warning("dante: stage alert failed", exc_info=True)

    def current(self) -> list[dict]:
        with self._lock:
            return [{"key": k, "text": v} for k, v in sorted(self.active.items())]


def stage_alert(why: str, on: bool) -> None:
    """Flash or stop the stage alert button named "Dante", if the operator made one; nothing happens without it."""
    from ... import alerts

    if on:
        try:
            alerts.alerts.raise_named(STAGE_BUTTON, "Dante")
        except alerts.Refused:
            log.info("dante: no stage alert button named %s, so only the status card shows this", STAGE_BUTTON)
        return
    button = next((b for b in alerts.alerts.settings()["buttons"] if text.same_name(b["name"], STAGE_BUTTON)), None)
    latest = alerts.alerts.latest.get(button["id"]) if button else None
    if button and latest is not None and latest.state == "on":
        alerts.alerts.cancel(button["id"])


monitor = Monitor()
monitor.stage = stage_alert
