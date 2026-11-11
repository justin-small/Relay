"""Alerts (#121): a musician taps a button and the screens behind it flash red and white until someone there
taps one of them.

The pieces:

* **Buttons**, set by the operator in admin and kept in config.json under "alerts": a name ("FOH booth") and
  what it flashes -- panels by id, and browser screens (`/screen?alert=<name>`) by the name they were opened
  with. Also the auto-stop: how long an alert flashes when nobody taps.
* **Senders**: the `/alert` page on a phone, or a panel whose feed is Alert. Both read `/stream?stream=alert`,
  which carries the whole state of every button in each message, and press with `POST /api/alerts/<button>/send`
  (again to cancel). A phone says who it is ("Drums"); a panel is named by its id, and Relay uses its name.
* **Targets**: a panel is told on its event stream (`alert`, then `alert_end`), a browser screen on
  `GET /api/alerts/screen?name=`. The first tap on any of them acknowledges for all (`POST /api/alerts/ack`), and
  the sender sees where it was seen.

Like the panels' routes, all of this is on the viewer port with no login: anyone who has the `/alert` link can
send, by the operator's choice. What a stray client can do is bounded -- a few short strings, a fixed number of
buttons and streams, and an alert is never stored.

The flash is once a second, fixed: well under the 3 per second where flashing becomes a seizure risk.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import config

MAX_BUTTONS = 16
MAX_TARGETS = 16            # panels plus screens, per button
NAME_MAX = 32
AUTO_STOP_DEFAULT_S = 120
AUTO_STOP_MIN_S = 30
AUTO_STOP_MAX_S = 600
# How long a button keeps saying how its last alert ended (seen, not seen, cancelled) before it is idle again.
OUTCOME_KEEP_S = 600
# A second press of the same button inside this long is taken as the same press (a double tap), not a cancel.
PRESS_GAP_S = 0.8
BODY_MAX_BYTES = 512
MAX_SENDER_STREAMS = 64
MAX_SCREEN_STREAMS = 32
QUEUE_MAX = 16
FLASH_HZ = 1

_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_ALERT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_PANEL_RE = re.compile(r"^[0-9a-f]{32}$")

STATES = ("idle", "on", "seen", "missed", "cancelled")


def _now() -> float:
    return time.monotonic()


def clean_name(value: object) -> str:
    """A button, screen or sender name: printable, one line, at most NAME_MAX characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:NAME_MAX]


def clean_stored(raw: object) -> dict[str, Any]:
    """The operator's alert settings, with anything malformed dropped rather than refused, so a hand edit cannot
    stop Relay starting."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        auto = int(raw.get("auto_stop_s") or AUTO_STOP_DEFAULT_S)
    except (TypeError, ValueError):
        auto = AUTO_STOP_DEFAULT_S
    buttons: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for item in raw.get("buttons") if isinstance(raw.get("buttons"), list) else []:
        if not isinstance(item, dict) or len(buttons) >= MAX_BUTTONS:
            continue
        bid = item.get("id")
        name = clean_name(item.get("name"))
        if not isinstance(bid, str) or not _ID_RE.fullmatch(bid) or bid in seen_ids or not name \
                or name.lower() in seen_names:
            continue
        panels = _unique([p for p in (item.get("panels") if isinstance(item.get("panels"), list) else [])
                          if isinstance(p, str) and _PANEL_RE.fullmatch(p)])
        screens = _unique([clean_name(s) for s in (item.get("screens") if isinstance(item.get("screens"), list)
                                                   else [])])
        screens = [s for s in screens if s]
        if len(panels) + len(screens) > MAX_TARGETS:
            continue
        seen_ids.add(bid)
        seen_names.add(name.lower())
        buttons.append({"id": bid, "name": name, "panels": panels, "screens": screens})
    return {"auto_stop_s": max(AUTO_STOP_MIN_S, min(AUTO_STOP_MAX_S, auto)), "buttons": buttons}


def _unique(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item.lower() not in (o.lower() for o in out):
            out.append(item)
    return out


def validate(raw: object) -> dict[str, Any]:
    """The operator's settings from admin, refused with a sentence when something is wrong. A button without an
    id gets one."""
    if not isinstance(raw, dict):
        raise ValueError("alert settings must be an object")
    auto = raw.get("auto_stop_s", AUTO_STOP_DEFAULT_S)
    if isinstance(auto, bool) or not isinstance(auto, int) or not AUTO_STOP_MIN_S <= auto <= AUTO_STOP_MAX_S:
        raise ValueError(f"auto-stop must be {AUTO_STOP_MIN_S} to {AUTO_STOP_MAX_S} seconds")
    items = raw.get("buttons")
    if not isinstance(items, list):
        raise ValueError("buttons must be a list")
    if len(items) > MAX_BUTTONS:
        raise ValueError(f"at most {MAX_BUTTONS} alert buttons")
    buttons = []
    names: set[str] = set()
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each button must be an object")
        name = clean_name(item.get("name"))
        if not name:
            raise ValueError("every button needs a name")
        if name.lower() in names:
            raise ValueError(f"two buttons are called {name}")
        names.add(name.lower())
        bid = item.get("id") or secrets.token_hex(4)
        if not isinstance(bid, str) or not _ID_RE.fullmatch(bid) or bid in ids:
            bid = secrets.token_hex(4)
        ids.add(bid)
        panels = item.get("panels") or []
        screens = item.get("screens") or []
        if not isinstance(panels, list) or not all(isinstance(p, str) and _PANEL_RE.fullmatch(p) for p in panels):
            raise ValueError(f"{name}: panels must be panel ids")
        if not isinstance(screens, list) or not all(isinstance(s, str) for s in screens):
            raise ValueError(f"{name}: screens must be names")
        screens = [s for s in (clean_name(s) for s in screens) if s]
        panels, screens = _unique(panels), _unique(screens)
        if not panels and not screens:
            raise ValueError(f"{name} flashes nothing; pick a panel or a screen")
        if len(panels) + len(screens) > MAX_TARGETS:
            raise ValueError(f"{name}: at most {MAX_TARGETS} panels and screens")
        buttons.append({"id": bid, "name": name, "panels": panels, "screens": screens})
    return {"auto_stop_s": auto, "buttons": buttons}


def sse_frame(message: dict) -> bytes:
    return f"data: {json.dumps(message, ensure_ascii=False)}\n\n".encode("utf-8")


@dataclass
class Alert:
    id: str
    button: str
    sender: str           # who pressed, as shown on the target ("Drums", a panel's name, or "")
    started: float
    state: str = "on"     # on, seen, missed, cancelled
    seen_on: str = ""     # where it was acknowledged
    ended: float = 0.0


class Refused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class Alerts:
    def __init__(self) -> None:
        # The newest alert per button id, live or ended. Memory only.
        self.latest: dict[str, Alert] = {}
        self._senders: list[asyncio.Queue] = []
        self._screens: dict[str, list[asyncio.Queue]] = {}   # by lower-cased name
        self._screen_names: dict[str, str] = {}              # lower-cased -> as written
        # Wired by main: push a message onto one panel's event stream (returns how many streams took it), and a
        # panel's display name.
        self.push_panel: Callable[[str, dict], int] = lambda pid, msg: 0
        self.panel_name: Callable[[str], str] = lambda pid: ""
        # Called when anything the admin page shows has changed.
        self.on_change: Callable[[], None] | None = None

    # ------------------------------------------------------------ settings
    @staticmethod
    def settings() -> dict[str, Any]:
        return config.get().get("alerts") or clean_stored({})

    def button(self, bid: str) -> dict[str, Any] | None:
        return next((b for b in self.settings()["buttons"] if b["id"] == bid), None)

    def save(self, raw: object) -> dict[str, Any]:
        clean = validate(raw)
        # An alert on a button that is gone ends now, while its targets are still known.
        kept = {b["id"] for b in clean["buttons"]}
        for bid, alert in list(self.latest.items()):
            if bid not in kept:
                if alert.state == "on":
                    self._end(alert, "cancelled")
                self.latest.pop(bid, None)
        config.save({"alerts": clean}, replace=("alerts",))
        self._broadcast_state()
        return self.admin_view()

    # --------------------------------------------------------------- state
    def state(self) -> dict[str, Any]:
        """Every button and how its newest alert stands: what `/stream?stream=alert` carries, whole, each time."""
        self.expire()
        now = _now()
        rows = []
        for b in self.settings()["buttons"]:
            alert = self.latest.get(b["id"])
            row: dict[str, Any] = {"id": b["id"], "name": b["name"], "state": "idle"}
            if alert is not None:
                row["state"] = alert.state
                row["from"] = alert.sender
                row["seen_on"] = alert.seen_on
                row["age_s"] = round(now - (alert.ended or alert.started))
            rows.append(row)
        return {"type": "alerts", "buttons": rows}

    def admin_view(self) -> dict[str, Any]:
        cfg = self.settings()
        return {"alerts": cfg, "screens": self.screen_names(), "state": self.state()["buttons"],
                "limits": {"buttons": MAX_BUTTONS, "targets": MAX_TARGETS, "name": NAME_MAX,
                           "auto_stop_min_s": AUTO_STOP_MIN_S, "auto_stop_max_s": AUTO_STOP_MAX_S}}

    def screen_names(self) -> list[str]:
        """Browser screens open right now that can be flashed, as they were named."""
        return sorted((self._screen_names[k] for k in self._screens), key=str.lower)

    def active_for_panel(self, pid: str) -> list[dict[str, Any]]:
        """The `alert` messages a panel's event stream opens with: alerts flashing on it now."""
        self.expire()
        out = []
        for b in self.settings()["buttons"]:
            alert = self.latest.get(b["id"])
            if alert is not None and alert.state == "on" and pid in b["panels"]:
                out.append(self._alert_message(alert, b))
        return out

    def _alert_message(self, alert: Alert, button: dict[str, Any]) -> dict[str, Any]:
        remaining = self.settings()["auto_stop_s"] - (_now() - alert.started)
        return {"type": "alert", "id": alert.id, "from": alert.sender, "to": button["name"],
                "ends_in_s": max(1, round(remaining)), "hz": FLASH_HZ}

    # ------------------------------------------------------------- presses
    def press(self, bid: str, sender: str) -> dict[str, Any]:
        """Start an alert on a button, or cancel the one flashing. Returns the new state."""
        button = self.button(bid) if isinstance(bid, str) else None
        if button is None:
            raise Refused(404, "no such alert button")
        self.expire()
        alert = self.latest.get(bid)
        now = _now()
        if alert is not None and alert.state == "on":
            if now - alert.started < PRESS_GAP_S:
                return self.state()   # a double tap is one press
            self._end(alert, "cancelled")
        else:
            alert = Alert(secrets.token_hex(8), bid, clean_name(sender), now)
            self.latest[bid] = alert
            message = self._alert_message(alert, button)
            for pid in button["panels"]:
                self.push_panel(pid, message)
            for name in button["screens"]:
                self._push_screen(name, message)
        self._broadcast_state()
        return self.state()

    def cancel(self, bid: str) -> dict[str, Any]:
        button = self.button(bid) if isinstance(bid, str) else None
        if button is None:
            raise Refused(404, "no such alert button")
        alert = self.latest.get(bid)
        if alert is not None and alert.state == "on":
            self._end(alert, "cancelled")
            self._broadcast_state()
        return self.state()

    def ack(self, alert_id: str, *, panel: str | None = None, screen: str | None = None) -> dict[str, Any]:
        """Someone tapped a flashing screen: the alert ends everywhere, and the sender is told where."""
        if not isinstance(alert_id, str) or not _ALERT_ID_RE.fullmatch(alert_id):
            raise Refused(400, "id must be an alert id")
        self.expire()
        alert = next((a for a in self.latest.values() if a.id == alert_id), None)
        if alert is None:
            raise Refused(404, "no such alert")
        button = self.button(alert.button)
        if button is None:
            raise Refused(404, "no such alert")
        if panel is not None:
            if panel not in button["panels"]:
                raise Refused(403, "this panel is not flashed by that alert")
            where = self.panel_name(panel) or "a panel"
        elif screen is not None:
            screen = clean_name(screen).lower()
            where = next((s for s in button["screens"] if s.lower() == screen), "")
            if not where:
                raise Refused(403, "this screen is not flashed by that alert")
        else:
            raise Refused(400, "say which panel or screen saw it")
        if alert.state == "on":
            alert.seen_on = where
            self._end(alert, "seen")
            self._broadcast_state()
        return {"ok": True, "state": alert.state, "seen_on": alert.seen_on}

    def expire(self) -> bool:
        """End alerts nobody tapped in time, and forget old outcomes. True when something changed. Called on every
        read and once a second by main, so a sender sees "Not seen" when it happens."""
        now = _now()
        auto = self.settings()["auto_stop_s"]
        changed = False
        for bid, alert in list(self.latest.items()):
            if alert.state == "on" and now - alert.started >= auto:
                self._end(alert, "missed")
                changed = True
            elif alert.state != "on" and now - alert.ended >= OUTCOME_KEEP_S:
                del self.latest[bid]
                changed = True
        return changed

    def tick(self) -> None:
        if self.expire():
            self._broadcast_state()

    def _end(self, alert: Alert, state: str) -> None:
        alert.state = state
        alert.ended = _now()
        button = self.button(alert.button)
        message = {"type": "alert_end", "id": alert.id, "why": state}
        if button is not None:
            for pid in button["panels"]:
                self.push_panel(pid, message)
            for name in button["screens"]:
                self._push_screen(name, message)

    # ------------------------------------------------------------- streams
    def subscribe_sender(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        while len(self._senders) >= MAX_SENDER_STREAMS:
            self._close(self._senders.pop(0))
        self._senders.append(q)
        q.put_nowait(sse_frame(self.state()))
        return q

    def unsubscribe_sender(self, q: asyncio.Queue) -> None:
        if q in self._senders:
            self._senders.remove(q)

    def subscribe_screen(self, name: str) -> asyncio.Queue:
        key = name.lower()
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        if key not in self._screens and len(self._screens) >= MAX_SCREEN_STREAMS:
            raise Refused(429, "too many alert screens are open")
        subs = self._screens.setdefault(key, [])
        while len(subs) >= 4:
            self._close(subs.pop(0))
        subs.append(q)
        self._screen_names[key] = name
        self.expire()
        for b in self.settings()["buttons"]:
            alert = self.latest.get(b["id"])
            if alert is not None and alert.state == "on" and key in (s.lower() for s in b["screens"]):
                q.put_nowait(sse_frame(self._alert_message(alert, b)))
        self._admin_changed()
        return q

    def unsubscribe_screen(self, name: str, q: asyncio.Queue) -> None:
        key = name.lower()
        subs = self._screens.get(key)
        if subs and q in subs:
            subs.remove(q)
        if not subs:
            self._screens.pop(key, None)
            self._screen_names.pop(key, None)
            self._admin_changed()

    def _push_screen(self, name: str, message: dict) -> None:
        frame = sse_frame(message)
        for q in list(self._screens.get(name.lower(), [])):
            self._put(q, frame)

    def _broadcast_state(self) -> None:
        frame = sse_frame(self.state())
        for q in list(self._senders):
            self._put(q, frame)
        self._admin_changed()

    def _put(self, q: asyncio.Queue, frame: bytes) -> None:
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            self._close(q)   # a reader that stopped reading reconnects and is told the state

    @staticmethod
    def _close(q: asyncio.Queue) -> None:
        while True:
            try:
                q.put_nowait(None)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    return

    def _admin_changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass


alerts = Alerts()
