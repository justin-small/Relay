"""Watching the sources the booth relies on (#562): raise an alert when one goes offline, drops frames, goes silent or clips
during a service.

Which sources: the ones with a camera number or in a multiview (`auto`) plus any the operator ticks. Health is only measured
while something receives a source, so while a service is on this holds one low-bandwidth receiver per watched source (the
shared pool's, so a viewer of the same source costs nothing more) and releases it when the service ends. "During a service"
is a named schedule (the Schedules page); none means always.

The decision is Rust's (`relay_ndi.Watcher`): a fault must last `hold` seconds to be raised, and be gone five seconds to be
cleared, so a blip raises nothing and a flap raises once. Raising is the stage alert button named "NDI", if the operator
made one, the same way the Dante module does it; the problems also show on the NDI page and in the module's health.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ... import text

log = logging.getLogger("relay.ndi.watch")

try:
    import relay_ndi as _native
except ImportError:        # pragma: no cover
    _native = None

STAGE_BUTTON = "NDI"
MAX_SOURCES = 32
HOLD_MIN, HOLD_MAX, HOLD_DEFAULT = 3, 300, 10
KINDS = ("offline", "frames", "silence", "clipping")
DEFAULTS = {"on": False, "auto": True, "sources": [], "hold": HOLD_DEFAULT, "schedule": "", "sound": True}


def clean_watch(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    for key in ("on", "auto", "sound"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    names: list[str] = []
    for v in raw.get("sources") if isinstance(raw.get("sources"), list) else []:
        if isinstance(v, str) and 0 < len(v) <= 160 and v not in names:
            names.append(v)
    out["sources"] = names[:MAX_SOURCES]
    hold = raw.get("hold")
    out["hold"] = max(HOLD_MIN, min(HOLD_MAX, hold)) if isinstance(hold, int) and not isinstance(hold, bool) else HOLD_DEFAULT
    sched = raw.get("schedule")
    out["schedule"] = sched.strip()[:80] if isinstance(sched, str) else ""
    return out


def stage_alert(why: str, on: bool) -> None:
    """Flash or stop the stage alert button named "NDI", if the operator made one; nothing happens without it."""
    from ... import alerts

    if on:
        try:
            alerts.alerts.raise_named(STAGE_BUTTON, "NDI")
        except alerts.Refused:
            log.info("ndi: no stage alert button named %s, so the problem shows on the NDI page only", STAGE_BUTTON)
        return
    button = next((b for b in alerts.alerts.settings()["buttons"] if text.same_name(b["name"], STAGE_BUTTON)), None)
    latest = alerts.alerts.latest.get(button["id"]) if button else None
    if button and latest is not None and latest.state == "on":
        alerts.alerts.cancel(button["id"])


class Watch:
    """`settings()` is the saved watch settings; `auto()` the names of sources with a camera number or in a multiview;
    `problems(source, sound)` what is wrong with one now as [(kind, sentence)]; `acquire(source)` a lease on its receiver;
    `in_service(schedule)` whether the named schedule is open; `stage(why, on)` raises or stops the alert."""

    def __init__(self, settings: Callable[[], dict], auto: Callable[[], list[str]],
                 problems: Callable[[str, bool], list[tuple[str, str]]], acquire: Callable[[str], Any],
                 in_service: Callable[[str], bool], stage: Callable[[str, bool], None] = stage_alert,
                 changed: Callable[[], None] = lambda: None) -> None:
        self._settings, self._auto, self._problems, self._acquire = settings, auto, problems, acquire
        self._in_service, self.stage, self._changed = in_service, stage, changed
        self._w = _native.Watcher(HOLD_DEFAULT) if _native is not None else None
        self._leases: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.on_service = False

    def relied(self, cfg: dict | None = None) -> list[str]:
        cfg = cfg or self._settings()
        names = list(cfg["sources"])
        if cfg["auto"]:
            names += [n for n in self._auto() if n not in names]
        return names

    def tick(self, now: float) -> None:
        """Look at every watched source (about every two seconds, on a worker thread)."""
        if self._w is None:
            return
        cfg = self._settings()
        live = cfg["on"]
        self.on_service = bool(live and (not cfg["schedule"] or self._in_service(cfg["schedule"])))
        wanted = self.relied(cfg) if self.on_service else []
        events: list[tuple] = []
        with self._lock:
            self._w.set_hold(float(cfg["hold"]))
            for gone in [s for s in self._leases if s not in wanted]:
                self._leases.pop(gone).release()
                events += self._w.forget(gone)
            for name in wanted:
                if name not in self._leases:
                    lease = self._acquire(name)
                    if lease is not None:
                        self._leases[name] = lease
                events += self._w.feed(now, name, self._problems(name, cfg["sound"]))
            for stale in {s for s, _, _ in self._w.active()} - set(wanted):
                events += self._w.forget(stale)
        if events:
            self._settle(events)

    def _settle(self, events: list[tuple]) -> None:
        raised = [e for e in events if e[0] == "raised"]
        cleared = [e for e in events if e[0] == "cleared"]
        for e in raised:
            log.warning("NDI source %s: %s", e[1], e[3])
        try:
            if raised and len(self.active()) == len(raised):
                self.stage(raised[0][3], True)          # the first problem in a quiet moment flashes the button
            elif cleared and not self.active():
                self.stage("", False)
        except Exception:    # noqa: BLE001
            log.warning("ndi: the stage alert failed", exc_info=True)
        self._changed()

    def active(self) -> list[dict]:
        if self._w is None:
            return []
        return [{"source": s, "kind": k, "text": why} for s, k, why in self._w.active()]

    def stop(self) -> None:
        """Release every receiver and clear what was raised (NDI is stopping, or the service is over)."""
        if self._w is None:
            return
        with self._lock:
            leases, self._leases = list(self._leases.values()), {}
            events = [e for s in {s for s, _, _ in self._w.active()} for e in self._w.forget(s)]
        for lease in leases:
            lease.release()
        if events:
            self._settle(events)
        self.on_service = False

    def snapshot(self) -> dict:
        cfg = self._settings()
        return {**cfg, "relied": self.relied(cfg), "active": self.active(), "in_service": self.on_service}
