"""Alert events out (#219): tell other products when an alert is sent or acknowledged. A webhook (POST JSON), OSC
over UDP, and a state read for Bitfocus Companion's variables and feedbacks.

A module of its own because each one talks to another product. Core's Alerts only keeps a list of listeners
(`alerts.listeners`); this module adds one in `attach()` and removes it in `detach()`, and its worker sends what
the listener queues.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission

log = logging.getLogger("relay.alert_events")

QUEUE_MAX = 64


class AlertEvents(Module):
    manifest = Manifest(
        id="alert_events",
        name="Alert events out",
        version="1.0.0",
        summary="Sends a webhook or an OSC message when an alert or stage message is sent or an alert is "
                "acknowledged, and shows alert state to Bitfocus Companion.",
        settings_key="alert_events",
        guide="https://github.com/justin-small/relay#alert-events-out",
    )
    defaults = {"webhook": {"enabled": False, "url": ""}, "osc": {"enabled": False, "host": "", "port": 8000}}
    admin_pages = (AdminPage(
        id="alertevents", title="Alert events", template="admin.html", group="Automation",
        icon='<path d="M2 8h3l2-5 2 10 2-5h3"/>'),)
    admin_scripts = ("alertevents.js",)
    route_prefixes = ("/api/admin/alert-events",)
    permissions = (
        Permission("alert_events.view", "See where alert events are sent and the alert state for Companion",
                   "Alert events", "viewer"),
        Permission("alert_events.manage", "Change where alert events are sent, and send a test", "Alert events",
                   "admin"),
    )

    def __init__(self) -> None:
        self._queue: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self.last: dict[str, Any] = {}      # the newest event, for Companion
        self.last_error = ""
        self.sent = 0

    def clean(self, raw: Any) -> dict:
        from .events import clean_stored

        return clean_stored(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    # The listener is a hook into core, so it is set in attach() and removed in detach(): the test suites never
    # run startup. It only queues; the worker started in start() sends.
    def attach(self) -> None:
        from ...alerts import alerts

        if self._listen not in alerts.listeners:
            alerts.listeners.append(self._listen)

    def detach(self) -> None:
        from ...alerts import alerts

        if self._listen in alerts.listeners:
            alerts.listeners.remove(self._listen)

    def _listen(self, event: dict[str, Any]) -> None:
        self.last = event
        q = self._queue
        if q is not None:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self.last_error = "Events are arriving faster than they can be sent; one was dropped."

    async def start(self) -> None:
        self._queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._task = asyncio.create_task(self._work(self._queue))

    async def stop(self) -> None:
        task, self._task, self._queue = self._task, None, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _work(self, queue: asyncio.Queue) -> None:
        from ... import config
        from . import events

        while True:
            event = await queue.get()
            try:
                cfg = config.get()["alert_events"]
                errors = await asyncio.to_thread(events.deliver, cfg, event)
                self.sent += 1
                self.last_error = errors[0] if errors else ""
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("alert event not delivered")
                self.last_error = "An event could not be sent."

    def health(self) -> Health:
        from ... import config

        cfg = config.get()["alert_events"]
        ways = [n for n, k in (("webhook", "webhook"), ("OSC", "osc")) if cfg[k]["enabled"]]
        if self.last_error:
            return Health("warn", self.last_error)
        if not ways:
            return Health("idle", "Nothing is switched on to send to; Companion can still read the state")
        return Health("ok", "Sending to " + " and ".join(ways))

    async def test(self) -> dict:
        from ... import config
        from . import events

        errors = await asyncio.to_thread(events.deliver, config.get()["alert_events"], events.test_event())
        return {"ok": not errors, "detail": errors[0] if errors else "A test event was sent."}


MODULE = AlertEvents()
