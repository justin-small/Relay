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
send, by the operator's choice. The operator can instead require a sign-in (`require_signin`, #175): then /alert
moves to the HTTPS operator port, a phone sends only as a signed-in account holding `alerts.send` and is named
by that account, and a panel still sends by its id. What a stray client can do is bounded -- a few short strings, a fixed number of
buttons and streams, and an alert is never stored.

The flash is once a second, fixed: well under the 3 per second where flashing becomes a seizure risk.

**Levels (#215)**: every alert and stage message has one. *Info* is a steady blue, *attention* flashes amber, and
*urgent* flashes red and white, as alerts always did. A button or preset has its own (an alert button is urgent
and a message is info until the operator says otherwise), and a sender may pick another for one send.

**Replies (#216)**: the operator sets a short list ("On my way", "Can't right now"). A panel or screen that
acknowledges may pick one, so nobody needs a keyboard, and the sender sees it next to "seen on ...".

**History (#217)** is written to the database as each alert ends and each stage message is shown
(app/alerthistory.py). **Events out (#219)**: anything that happens is also handed to `Alerts.listeners`, callables
a module adds in `start()` and removes in `stop()`, so core never names the products that listen.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

from . import alerthistory, config, db

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
# Stage messages (#214): short words from the booth to the stage, shown without the flash.
MAX_PRESETS = 16
TEXT_MAX = 80
MESSAGE_DEFAULT_S = 10
MESSAGE_MIN_S = 3
MESSAGE_MAX_S = 120
BANNER_DEFAULT_S = 300
BANNER_MIN_S = 30
BANNER_MAX_S = 3600
MAX_LIVE_MESSAGES = 8
# Replies (#216): a short fixed list the operator sets, tapped on a panel or screen when it acknowledges.
MAX_REPLIES = 6
REPLY_MAX = 24

_ID_RE = re.compile(r"^[0-9a-f]{8}$")
_ALERT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_PANEL_RE = re.compile(r"^[0-9a-f]{32}$")

# How loud an alert or message is. Each has a colour on every screen; only info is steady.
LEVELS = ("info", "attention", "urgent")
LEVEL_FLASHES = {"info": False, "attention": True, "urgent": True}
DEFAULT_ALERT_LEVEL = "urgent"
DEFAULT_MESSAGE_LEVEL = "info"

STATES = ("idle", "on", "seen", "missed", "cancelled")
# The icon a button shows on the /alert page, picked by the operator. The page draws each one; "bell" is the default.
ICONS = ("bell", "mic", "headphones", "keys", "drums", "guitar", "person", "people", "video", "sliders")
DEFAULT_ICON = "bell"


def clean_icon(value: object) -> str:
    return value if isinstance(value, str) and value in ICONS else DEFAULT_ICON


def clean_reply(value: object) -> str:
    """One reply: printable, one line, at most REPLY_MAX characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:REPLY_MAX]


def clean_replies(raw: object) -> list[str]:
    """The reply list from stored settings: malformed or repeated entries dropped, at most MAX_REPLIES."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        text = clean_reply(item)
        if text and text.lower() not in (o.lower() for o in out) and len(out) < MAX_REPLIES:
            out.append(text)
    return out


def validate_replies(raw: object) -> list[str]:
    """The reply list from admin, refused with a sentence when something is wrong."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise ValueError("replies must be a list of words")
    if len(raw) > MAX_REPLIES:
        raise ValueError(f"at most {MAX_REPLIES} replies")
    out: list[str] = []
    for item in raw:
        text = clean_reply(item)
        if not text:
            raise ValueError("every reply needs words")
        if len(" ".join(item.split())) > REPLY_MAX:
            raise ValueError(f"a reply is at most {REPLY_MAX} characters")
        if text.lower() in (o.lower() for o in out):
            raise ValueError(f"two replies say \"{text}\"")
        out.append(text)
    return out


def clean_level(value: object, default: str) -> str:
    return value if isinstance(value, str) and value in LEVELS else default


def _checked_level(item: dict, label: str, default: str) -> str:
    level = item.get("level", default)
    if not isinstance(level, str) or level not in LEVELS:
        raise ValueError(f"{label}: the level must be one of {', '.join(LEVELS)}")
    return level


def _now() -> float:
    return time.monotonic()


def clean_name(value: object) -> str:
    """A button, screen or sender name: printable, one line, at most NAME_MAX characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:NAME_MAX]


def clean_text(value: object) -> str:
    """A stage message's words: printable, one line, at most TEXT_MAX characters."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:TEXT_MAX]


def _seconds(raw: object, default: int, lo: int, hi: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _targets(item: dict) -> tuple[list[str], list[str]]:
    panels = _unique([p for p in (item.get("panels") if isinstance(item.get("panels"), list) else [])
                      if isinstance(p, str) and _PANEL_RE.fullmatch(p)])
    screens = [s for s in _unique([clean_name(s) for s in (item.get("screens") if isinstance(item.get("screens"), list)
                                                           else [])]) if s]
    return panels, screens


def clean_messages(raw: object) -> dict[str, Any]:
    """The stage-message settings, malformed parts dropped rather than refused. `presets` are the ready-made
    messages; `free` says where words typed on the /alert page go (nowhere until the operator picks); a message
    shows `message_s` seconds, a banner `banner_s`."""
    raw = raw if isinstance(raw, dict) else {}
    presets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw.get("presets") if isinstance(raw.get("presets"), list) else []:
        if not isinstance(item, dict) or len(presets) >= MAX_PRESETS:
            continue
        pid, text = item.get("id"), clean_text(item.get("text"))
        panels, screens = _targets(item)
        if not isinstance(pid, str) or not _ID_RE.fullmatch(pid) or pid in seen_ids or not text \
                or len(panels) + len(screens) > MAX_TARGETS:
            continue
        seen_ids.add(pid)
        presets.append({"id": pid, "text": text, "banner": item.get("banner") is True,
                        "level": clean_level(item.get("level"), DEFAULT_MESSAGE_LEVEL),
                        "panels": panels, "screens": screens})
    free = raw.get("free") if isinstance(raw.get("free"), dict) else {}
    fpanels, fscreens = _targets(free)
    if len(fpanels) + len(fscreens) > MAX_TARGETS:
        fpanels, fscreens = [], []
    return {"presets": presets,
            "free": {"panels": fpanels, "screens": fscreens,
                     "level": clean_level(free.get("level"), DEFAULT_MESSAGE_LEVEL)},
            "message_s": _seconds(raw.get("message_s"), MESSAGE_DEFAULT_S, MESSAGE_MIN_S, MESSAGE_MAX_S),
            "banner_s": _seconds(raw.get("banner_s"), BANNER_DEFAULT_S, BANNER_MIN_S, BANNER_MAX_S)}


def validate_messages(raw: object) -> dict[str, Any]:
    """The stage-message settings from admin, refused with a sentence when something is wrong."""
    if raw is None:
        return clean_messages({})
    if not isinstance(raw, dict):
        raise ValueError("messages must be an object")
    for key, lo, hi, label in (("message_s", MESSAGE_MIN_S, MESSAGE_MAX_S, "a message stays"),
                               ("banner_s", BANNER_MIN_S, BANNER_MAX_S, "a banner stays")):
        v = raw.get(key, MESSAGE_DEFAULT_S if key == "message_s" else BANNER_DEFAULT_S)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise ValueError(f"{label} {lo} to {hi} seconds")
    items = raw.get("presets", [])
    if not isinstance(items, list):
        raise ValueError("presets must be a list")
    if len(items) > MAX_PRESETS:
        raise ValueError(f"at most {MAX_PRESETS} preset messages")
    presets = []
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each preset must be an object")
        text = clean_text(item.get("text"))
        if not text:
            raise ValueError("every preset message needs words")
        banner = item.get("banner", False)
        if not isinstance(banner, bool):
            raise ValueError(f"{text}: banner must be true or false")
        pid = item.get("id")
        if not isinstance(pid, str) or not _ID_RE.fullmatch(pid) or pid in ids:
            pid = secrets.token_hex(4)
        ids.add(pid)
        panels, screens = _checked_targets(item, text)
        if not panels and not screens:
            raise ValueError(f"\"{text}\" goes nowhere; pick a panel or a screen")
        level = _checked_level(item, text, DEFAULT_MESSAGE_LEVEL)
        presets.append({"id": pid, "text": text, "banner": banner, "level": level,
                        "panels": panels, "screens": screens})
    free = raw.get("free", {})
    if not isinstance(free, dict):
        raise ValueError("free text must be an object")
    fpanels, fscreens = _checked_targets(free, "Typed messages")
    flevel = _checked_level(free, "Typed messages", DEFAULT_MESSAGE_LEVEL)
    return {"presets": presets, "free": {"panels": fpanels, "screens": fscreens, "level": flevel},
            "message_s": raw.get("message_s", MESSAGE_DEFAULT_S), "banner_s": raw.get("banner_s", BANNER_DEFAULT_S)}


def _checked_targets(item: dict, label: str) -> tuple[list[str], list[str]]:
    panels = item.get("panels") or []
    screens = item.get("screens") or []
    if not isinstance(panels, list) or not all(isinstance(p, str) and _PANEL_RE.fullmatch(p) for p in panels):
        raise ValueError(f"{label}: panels must be panel ids")
    if not isinstance(screens, list) or not all(isinstance(s, str) for s in screens):
        raise ValueError(f"{label}: screens must be names")
    panels = _unique(panels)
    screens = [s for s in _unique([clean_name(s) for s in screens]) if s]
    if len(panels) + len(screens) > MAX_TARGETS:
        raise ValueError(f"{label}: at most {MAX_TARGETS} panels and screens")
    return panels, screens


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
        buttons.append({"id": bid, "name": name, "icon": clean_icon(item.get("icon")), "panels": panels,
                        "screens": screens, "stage": item.get("stage") is True,
                        "level": clean_level(item.get("level"), DEFAULT_ALERT_LEVEL)})
    return {"auto_stop_s": max(AUTO_STOP_MIN_S, min(AUTO_STOP_MAX_S, auto)), "buttons": buttons,
            "require_signin": raw.get("require_signin") is True, "messages": clean_messages(raw.get("messages")),
            "replies": clean_replies(raw.get("replies"))}


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
    require_signin = raw.get("require_signin", False)
    if not isinstance(require_signin, bool):
        raise ValueError("require sign-in must be true or false")
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
        stage = item.get("stage", False)
        if not isinstance(stage, bool):
            raise ValueError(f"{name}: stage must be true or false")
        if not panels and not screens and not stage:
            raise ValueError(f"{name} flashes nothing; pick a panel or a screen")
        if len(panels) + len(screens) > MAX_TARGETS:
            raise ValueError(f"{name}: at most {MAX_TARGETS} panels and screens")
        icon = item.get("icon", DEFAULT_ICON)
        if icon not in ICONS:
            raise ValueError(f"{name}: the icon must be one of {', '.join(ICONS)}")
        level = _checked_level(item, name, DEFAULT_ALERT_LEVEL)
        buttons.append({"id": bid, "name": name, "icon": icon, "panels": panels, "screens": screens,
                        "stage": stage, "level": level})
    return {"auto_stop_s": auto, "buttons": buttons, "require_signin": require_signin,
            "messages": validate_messages(raw.get("messages")), "replies": validate_replies(raw.get("replies"))}


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
    level: str = DEFAULT_ALERT_LEVEL
    reply: str = ""       # the reply picked when it was acknowledged (#216)
    at: str = ""          # the wall-clock time it started, for the history (#217)
    targets: tuple[str, ...] = ()   # panel and screen names as they were when it started


@dataclass
class Message:
    id: str
    key: str              # the preset id it came from, or "free" for typed words
    text: str
    banner: bool
    sender: str
    started: float
    seconds: int
    panels: list[str]
    screens: list[str]
    level: str = DEFAULT_MESSAGE_LEVEL


# What a sender, panel or screen is told when an alert request is refused: fixed sentences by key, so a response
# never carries an exception's own text.
MESSAGES = {
    "no_button": "no such alert button",
    "no_button_named": "No alert button has that name. Add one under Alerts.",
    "bad_id": "id must be an alert id",
    "no_alert": "no such alert",
    "not_this_panel": "this panel is not flashed by that alert",
    "not_this_screen": "this screen is not flashed by that alert",
    "who_saw": "say which panel or screen saw it",
    "too_many_screens": "too many alert screens are open",
    "no_preset": "no such message",
    "no_words": "write the message first",
    "bad_level": "level must be info, attention or urgent",
    "bad_reply": "that is not one of the replies",
    "no_free_targets": "the operator has not chosen where typed messages go",
}


class Refused(Exception):
    """`key` names the fixed sentence in MESSAGES. `detail` is a fuller sentence for Relay's own history (a
    slide-note tag's, say), never sent in a response."""

    def __init__(self, status: int, key: str, detail: str | None = None) -> None:
        super().__init__(detail or MESSAGES[key])
        self.status = status
        self.key = key


class Alerts:
    def __init__(self) -> None:
        # The newest alert per button id, live or ended. Memory only.
        self.latest: dict[str, Alert] = {}
        self._senders: list[asyncio.Queue] = []
        # Stage messages (#214) showing now, by preset id (or "free"). Memory only, like alerts.
        self.live: dict[str, Message] = {}
        self._msg_senders: list[asyncio.Queue] = []
        self._screens: dict[str, list[asyncio.Queue]] = {}   # by lower-cased name
        self._screen_names: dict[str, str] = {}              # lower-cased -> as written
        # Wired by main: push a message onto one panel's event stream (returns how many streams took it), and a
        # panel's display name.
        self.push_panel: Callable[[str, dict], int] = lambda pid, msg: 0
        self.panel_name: Callable[[str], str] = lambda pid: ""
        # Called when anything the admin page shows has changed.
        self.on_change: Callable[[], None] | None = None
        # ProPresenter's stage display (#138): called with (alert id, text) when an alert on a button with
        # "also show on the stage display" starts, and with (alert id, None) when any alert ends, however.
        self.on_stage: Callable[[str, str | None], None] | None = None
        # Events out (#219): callables a module adds while it is on. Each is called with one dict, {"event": ...},
        # on the event loop, and must not block; a listener that raises is skipped.
        self.listeners: list[Callable[[dict[str, Any]], None]] = []

    # ------------------------------------------------------------ settings
    @staticmethod
    def settings() -> dict[str, Any]:
        return config.get().get("alerts") or clean_stored({})

    def button(self, bid: str) -> dict[str, Any] | None:
        return next((b for b in self.settings()["buttons"] if b["id"] == bid), None)

    def save(self, raw: object) -> dict[str, Any]:
        # A client that sends only the buttons (an older admin page) leaves the stage messages as they are.
        if isinstance(raw, dict):
            keep = {k: self.settings().get(k) for k in ("messages", "replies") if k not in raw}
            if keep:
                raw = {**raw, **keep}
        clean = validate(raw)
        # An alert on a button that is gone ends now, while its targets are still known.
        kept = {b["id"] for b in clean["buttons"]}
        for bid, alert in list(self.latest.items()):
            if bid not in kept:
                if alert.state == "on":
                    self._end(alert, "cancelled")
                self.latest.pop(bid, None)
        config.save({"alerts": clean}, replace=("alerts",))
        for key in list(self.live):
            if key != "free" and not any(p["id"] == key for p in clean["messages"]["presets"]):
                self._end_message(self.live[key])
        self._broadcast_state()
        self._broadcast_messages()
        return self.admin_view()

    # --------------------------------------------------------------- state
    def state(self) -> dict[str, Any]:
        """Every button and how its newest alert stands: what `/stream?stream=alert` carries, whole, each time."""
        self.expire()
        now = _now()
        rows = []
        for b in self.settings()["buttons"]:
            alert = self.latest.get(b["id"])
            row: dict[str, Any] = {"id": b["id"], "name": b["name"], "icon": b.get("icon") or DEFAULT_ICON,
                                   "state": "idle"}
            if alert is not None:
                row["state"] = alert.state
                row["from"] = alert.sender
                row["seen_on"] = alert.seen_on
                row["reply"] = alert.reply
                row["age_s"] = round(now - (alert.ended or alert.started))
                if alert.state == "on":
                    # Until the auto-stop, for the sender's countdown.
                    row["ends_in_s"] = max(0, round(self.settings()["auto_stop_s"] - (now - alert.started)))
            rows.append(row)
        return {"type": "alerts", "buttons": rows}

    def admin_view(self) -> dict[str, Any]:
        cfg = self.settings()
        return {"alerts": cfg, "screens": self.screen_names(), "state": self.state()["buttons"],
                "icons": list(ICONS), "levels": list(LEVELS),
                "limits": {"buttons": MAX_BUTTONS, "targets": MAX_TARGETS, "name": NAME_MAX,
                           "auto_stop_min_s": AUTO_STOP_MIN_S, "auto_stop_max_s": AUTO_STOP_MAX_S,
                           "presets": MAX_PRESETS, "text": TEXT_MAX, "message_min_s": MESSAGE_MIN_S,
                           "message_max_s": MESSAGE_MAX_S, "banner_min_s": BANNER_MIN_S,
                           "banner_max_s": BANNER_MAX_S, "replies": MAX_REPLIES, "reply": REPLY_MAX},
                "live_messages": self.message_state()["live"]}

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
        out.extend(self._message_wire(m) for m in self.live.values() if pid in m.panels)
        return out

    def _alert_message(self, alert: Alert, button: dict[str, Any]) -> dict[str, Any]:
        remaining = self.settings()["auto_stop_s"] - (_now() - alert.started)
        wire = {"type": "alert", "id": alert.id, "from": alert.sender, "to": button["name"],
                "ends_in_s": max(1, round(remaining)), "level": alert.level,
                "hz": FLASH_HZ if LEVEL_FLASHES[alert.level] else 0}
        replies = self.settings().get("replies") or []
        if replies:
            wire["replies"] = list(replies)
        return wire

    # ------------------------------------------------------------- presses
    def press(self, bid: str, sender: str, level: object = None) -> dict[str, Any]:
        """Start an alert on a button, or cancel the one flashing. `level` overrides the button's for this alert
        (#215). Returns the new state."""
        button = self.button(bid) if isinstance(bid, str) else None
        if button is None:
            raise Refused(404, "no_button")
        if level is not None and (not isinstance(level, str) or level not in LEVELS):
            raise Refused(400, "bad_level")
        self.expire()
        alert = self.latest.get(bid)
        now = _now()
        if alert is not None and alert.state == "on":
            if now - alert.started < PRESS_GAP_S:
                return self.state()   # a double tap is one press
            self._end(alert, "cancelled")
        else:
            alert = Alert(secrets.token_hex(8), bid, clean_name(sender), now,
                          level=level or clean_level(button.get("level"), DEFAULT_ALERT_LEVEL), at=db.now(),
                          targets=self._target_names(button))
            self.latest[bid] = alert
            self._emit("alert.sent", alert, button)
            message = self._alert_message(alert, button)
            if button.get("stage"):
                self._stage(alert.id, f"{button['name']} (from {alert.sender})" if alert.sender else button["name"])
            for pid in button["panels"]:
                self.push_panel(pid, message)
            for name in button["screens"]:
                self._push_screen(name, message)
        self._broadcast_state()
        return self.state()

    def raise_named(self, text: str, sender: str) -> str:
        """Start the alert on the button NAMED `text` (any capitals), for something other than a person tapping it,
        such as a slide-note tag. Unlike `press` it never cancels: an alert already flashing stays as it is. Raises
        Refused(404) when no button has that name. Returns a sentence for what happened."""
        want = clean_name(text).lower()
        button = next((b for b in self.settings()["buttons"] if b["name"].lower() == want), None) if want else None
        if button is None:
            raise Refused(404, "no_button_named", f"No alert button is named \"{clean_name(text)}\". Add one under Alerts.")
        self.expire()
        alert = self.latest.get(button["id"])
        if alert is not None and alert.state == "on":
            return f"The {button['name']} alert is already on."
        self.press(button["id"], sender)
        return f"Alert {button['name']} raised."

    def cancel(self, bid: str) -> dict[str, Any]:
        button = self.button(bid) if isinstance(bid, str) else None
        if button is None:
            raise Refused(404, "no_button")
        alert = self.latest.get(bid)
        if alert is not None and alert.state == "on":
            self._end(alert, "cancelled")
            self._broadcast_state()
        return self.state()

    def _target_names(self, item: dict[str, Any]) -> tuple[str, ...]:
        return tuple([self.panel_name(p) or "a panel" for p in item["panels"]] + list(item["screens"]))

    def ack(self, alert_id: str, *, panel: str | None = None, screen: str | None = None,
            reply: object = None) -> dict[str, Any]:
        """Someone tapped a flashing screen: the alert ends everywhere, and the sender is told where. `reply` is
        one of the operator's replies (#216), taken or refused before anything ends."""
        if not isinstance(alert_id, str) or not _ALERT_ID_RE.fullmatch(alert_id):
            raise Refused(400, "bad_id")
        self.expire()
        alert = next((a for a in self.latest.values() if a.id == alert_id), None)
        if alert is None:
            raise Refused(404, "no_alert")
        button = self.button(alert.button)
        if button is None:
            raise Refused(404, "no_alert")
        if panel is not None:
            if panel not in button["panels"]:
                raise Refused(403, "not_this_panel")
            where = self.panel_name(panel) or "a panel"
        elif screen is not None:
            screen = clean_name(screen).lower()
            where = next((s for s in button["screens"] if s.lower() == screen), "")
            if not where:
                raise Refused(403, "not_this_screen")
        else:
            raise Refused(400, "who_saw")
        chosen = ""
        if reply is not None and reply != "":
            chosen = next((r for r in self.settings().get("replies") or []
                           if isinstance(reply, str) and r.lower() == clean_reply(reply).lower()), "")
            if not chosen:
                raise Refused(400, "bad_reply")
        if alert.state == "on":
            alert.seen_on = where
            alert.reply = chosen
            self._end(alert, "seen")
            self._broadcast_state()
        return {"ok": True, "state": alert.state, "seen_on": alert.seen_on, "reply": alert.reply}

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
        for msg in list(self.live.values()):
            if now - msg.started >= msg.seconds:
                self._end_message(msg)
                changed = True
        return changed

    def tick(self) -> None:
        if self.expire():
            self._broadcast_state()
            self._broadcast_messages()

    def _end(self, alert: Alert, state: str) -> None:
        alert.state = state
        alert.ended = _now()
        button = self.button(alert.button)
        message = {"type": "alert_end", "id": alert.id, "why": state}
        self._stage(alert.id, None)
        self._record_alert(alert, button)
        self._emit("alert.acknowledged" if state == "seen" else "alert.ended", alert, button)
        if button is not None:
            for pid in button["panels"]:
                self.push_panel(pid, message)
            for name in button["screens"]:
                self._push_screen(name, message)

    # ------------------------------------------------- history and events out
    def _record_alert(self, alert: Alert, button: dict[str, Any] | None) -> None:
        seen = alert.state == "seen"
        alerthistory.record(kind="alert", at=alert.at or None, name=button["name"] if button else "",
                            level=alert.level, sender=alert.sender, targets=list(alert.targets),
                            outcome=alert.state, seen_on=alert.seen_on,
                            seen_after_s=round(alert.ended - alert.started) if seen else None, reply=alert.reply)

    def _emit(self, event: str, alert: Alert | Message, button: dict[str, Any] | None = None) -> None:
        """Hand an event to every listener (#219). Never raises, never blocks."""
        if not self.listeners:
            return
        if isinstance(alert, Message):
            data: dict[str, Any] = {"event": event, "id": alert.id, "text": alert.text, "level": alert.level,
                                    "banner": alert.banner, "from": alert.sender,
                                    "targets": [self.panel_name(p) or "a panel" for p in alert.panels]
                                    + list(alert.screens)}
        else:
            data = {"event": event, "id": alert.id, "button": button["name"] if button else "",
                    "level": alert.level, "from": alert.sender, "targets": list(alert.targets)}
            if event != "alert.sent":
                data["state"] = alert.state
                data["seen_on"] = alert.seen_on
                data["reply"] = alert.reply
                if alert.state == "seen":
                    data["seen_after_s"] = round(alert.ended - alert.started)
        for fn in list(self.listeners):
            try:
                fn(data)
            except Exception:
                pass

    # ------------------------------------------------------ stage messages
    def _message_wire(self, msg: Message) -> dict[str, Any]:
        remaining = msg.seconds - (_now() - msg.started)
        return {"type": "message", "id": msg.id, "from": msg.sender, "text": msg.text, "banner": msg.banner,
                "level": msg.level, "hz": FLASH_HZ if LEVEL_FLASHES[msg.level] else 0,
                "ends_in_s": max(1, round(remaining))}

    def message_state(self) -> dict[str, Any]:
        """What the /alert page needs for stage messages, whole: the presets to tap, whether typing is allowed, and
        what is showing now (so it can be cleared). Its own stream, so the panels' alert board stays small."""
        self.expire()
        cfg = self.settings()["messages"]
        now = _now()
        free = cfg["free"]
        return {"type": "messages", "free": bool(free["panels"] or free["screens"]), "text_max": TEXT_MAX,
                "free_level": free.get("level", DEFAULT_MESSAGE_LEVEL),
                "presets": [{"id": p["id"], "text": p["text"], "banner": p["banner"],
                             "level": p.get("level", DEFAULT_MESSAGE_LEVEL)} for p in cfg["presets"]],
                "live": [{"id": m.id, "key": m.key, "text": m.text, "banner": m.banner, "level": m.level,
                          "from": m.sender,
                          "ends_in_s": max(0, round(m.seconds - (now - m.started)))}
                         for m in self.live.values()]}

    def send_message(self, sender: str, *, preset: object = None, text: object = None,
                     banner: object = False, level: object = None) -> dict[str, Any]:
        """Show a preset (by id) or typed words on their screens. The same preset, or typed words again, replace
        what is showing from it. `level` overrides the preset's or the typed words' own (#215). Returns the new
        message state."""
        cfg = self.settings()["messages"]
        if level is not None and (not isinstance(level, str) or level not in LEVELS):
            raise Refused(400, "bad_level")
        if preset is not None:
            item = next((p for p in cfg["presets"] if p["id"] == preset), None) if isinstance(preset, str) else None
            if item is None:
                raise Refused(404, "no_preset")
            key, words, is_banner = item["id"], item["text"], item["banner"]
            panels, screens = item["panels"], item["screens"]
            own = item.get("level", DEFAULT_MESSAGE_LEVEL)
        else:
            words = clean_text(text)
            if not words:
                raise Refused(400, "no_words")
            panels, screens = cfg["free"]["panels"], cfg["free"]["screens"]
            if not panels and not screens:
                raise Refused(403, "no_free_targets")
            key, is_banner = "free", banner is True
            own = cfg["free"].get("level", DEFAULT_MESSAGE_LEVEL)
        self.expire()
        old = self.live.get(key)
        if old is not None:
            self._end_message(old)
        while len(self.live) >= MAX_LIVE_MESSAGES:
            self._end_message(next(iter(self.live.values())))
        msg = Message(secrets.token_hex(8), key, words, is_banner, clean_name(sender), _now(),
                      cfg["banner_s"] if is_banner else cfg["message_s"], list(panels), list(screens),
                      level or own)
        self.live[key] = msg
        alerthistory.record(kind="message", text=msg.text, level=msg.level, sender=msg.sender,
                            targets=[self.panel_name(p) or "a panel" for p in msg.panels] + list(msg.screens),
                            outcome="shown")
        self._emit("message.sent", msg)
        wire = self._message_wire(msg)
        for pid in msg.panels:
            self.push_panel(pid, wire)
        for name in msg.screens:
            self._push_screen(name, wire)
        self._broadcast_messages()
        return self.message_state()

    def clear_messages(self, message_id: object = None) -> dict[str, Any]:
        """Take one showing message down (by its id), or every one when no id is given."""
        for msg in list(self.live.values()):
            if message_id is None or msg.id == message_id:
                self._end_message(msg)
        self._broadcast_messages()
        return self.message_state()

    def _end_message(self, msg: Message) -> None:
        if self.live.get(msg.key) is msg:
            del self.live[msg.key]
        end = {"type": "message_end", "id": msg.id}
        for pid in msg.panels:
            self.push_panel(pid, end)
        for name in msg.screens:
            self._push_screen(name, end)

    def subscribe_messages(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        while len(self._msg_senders) >= MAX_SENDER_STREAMS:
            self._close(self._msg_senders.pop(0))
        self._msg_senders.append(q)
        q.put_nowait(sse_frame(self.message_state()))
        return q

    def unsubscribe_messages(self, q: asyncio.Queue) -> None:
        if q in self._msg_senders:
            self._msg_senders.remove(q)

    def _broadcast_messages(self) -> None:
        frame = sse_frame(self.message_state())
        for q in list(self._msg_senders):
            self._put(q, frame)
        self._admin_changed()

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
            raise Refused(429, "too_many_screens")
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
        for msg in self.live.values():
            if key in (s.lower() for s in msg.screens):
                q.put_nowait(sse_frame(self._message_wire(msg)))
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

    def _stage(self, alert_id: str, text: str | None) -> None:
        if self.on_stage is not None:
            try:
                self.on_stage(alert_id, text)
            except Exception:
                pass

    def _admin_changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass


alerts = Alerts()
