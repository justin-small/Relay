"""Who is on the network (#356, #358): each poll of the controller's client list is compared with the last, which gives
joins, roams (a client moved to another AP or switch port) and leaves.

A phone drops off Wi-Fi for a while when its screen is off, so a client missing from a poll is kept as online until it
has been gone for `away_s` seconds; only then is it a leave. The first poll after starting fills the list without
making joins, so a restart does not look like everyone arriving at once.

In memory only: the last `KEEP` events, for the admin page. Other modules (the crew page's Wi-Fi check-in) read
`online()` or listen with `listen()`; they name "unifi" in `uses` and never assume it is on.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Callable

log = logging.getLogger("relay.unifi")
KEEP = 300


class Presence:
    def __init__(self) -> None:
        self.clients: dict[str, dict] = {}        # mac → client, with "since", "seen" and "missing" timestamps
        self.events: deque[dict] = deque(maxlen=KEEP)
        self.listeners: list[Callable[[dict], None]] = []
        self.primed = False

    def reset(self) -> None:
        self.clients.clear()
        self.events.clear()
        self.primed = False

    def listen(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """Call `fn(event)` for every join, roam and leave. Returns a function that stops it."""
        self.listeners.append(fn)

        def stop() -> None:
            if fn in self.listeners:
                self.listeners.remove(fn)
        return stop

    def update(self, found: list[dict], now: float, away_s: float) -> list[dict]:
        """Take one poll's client list. Returns the events it made (also kept and sent to listeners)."""
        seen = {c["mac"]: c for c in found if c.get("mac")}
        events: list[dict] = []
        if not self.primed:
            self.clients = {m: {**c, "since": now, "seen": now, "missing": None} for m, c in seen.items()}
            self.primed = True
            return events
        for mac, c in seen.items():
            prev = self.clients.get(mac)
            if prev is None or prev.get("missing") is not None and now - prev["missing"] >= away_s:
                self.clients[mac] = {**c, "since": now, "seen": now, "missing": None}
                events.append(self._event("join", c, now))
                continue
            if (c.get("uplink"), c.get("port")) != (prev.get("uplink"), prev.get("port")) and c.get("uplink"):
                events.append(self._event("roam", c, now, before=prev))
            self.clients[mac] = {**c, "since": prev["since"], "seen": now, "missing": None}
        for mac, prev in list(self.clients.items()):
            if mac in seen:
                continue
            if prev.get("missing") is None:
                prev["missing"] = now
            if now - prev["missing"] >= away_s:
                del self.clients[mac]
                events.append(self._event("leave", prev, now))
        for e in events:
            self.events.appendleft(e)
            for fn in list(self.listeners):
                try:
                    fn(e)
                except Exception:
                    log.warning("unifi: a presence listener failed", exc_info=True)
        return events

    @staticmethod
    def _event(kind: str, c: dict, now: float, before: dict | None = None) -> dict:
        e = {"ts": now, "kind": kind, "mac": c["mac"], "name": c.get("name") or c.get("hostname") or "",
             "hostname": c.get("hostname") or "", "ip": c.get("ip") or "", "wired": bool(c.get("wired")),
             "uplink": c.get("uplink") or "", "port": c.get("port")}
        if before is not None:
            e["from_uplink"], e["from_port"] = before.get("uplink") or "", before.get("port")
        return e

    def online(self) -> list[dict]:
        """Clients on the network now (including ones missing for less than `away_s`)."""
        return list(self.clients.values())

    def by_ip(self, ip: str) -> dict | None:
        """The online client with this address, for "this is my phone". Only a present one, never a missing one."""
        if not ip:
            return None
        for c in self.clients.values():
            if c.get("ip") == ip and c.get("missing") is None:
                return c
        return None
