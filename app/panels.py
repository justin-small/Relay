"""Caption panels: who is out there, and what each one should show.

A panel (firmware/relay-display) checks in over plain HTTP on the viewer port
every 30 s and keeps `GET /api/panels/{id}/events` open. Nothing here is
authenticated -- panels do not pair, by decision -- so everything a stray LAN
client can do to this module is bounded:

* the registry holds at most MAX_PANELS entries, and a check-in body is at most
  CHECKIN_MAX_BYTES, so a flood cannot grow memory or config.json;
* the event stream carries a feed choice, a rotation, a restart and the panel's service mode. A
  panel allowed to move the Planning Center service on is also sent its
  control key there (and on its check-in reply): plain HTTP, so the key stops
  casual misuse, not someone reading the network. Never the Planning Center
  token;
* nothing a panel sends is ever written to config.json. Only what the operator
  sets from the admin side (a name, a feed, a rotation, a service mode) is persisted -- and that side stays
  under /api/admin/*, which the viewer port refuses.

The registry is in memory. The operator's name and feed per panel live in
config.json under "panels", so they survive a restart.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config, languages, panelfw, propresenter

# A panel not heard from for this long shows as offline. It checks in every 30 s.
OFFLINE_AFTER_S = 90.0
CHECKIN_INTERVAL_S = 30
CHECKIN_MAX_BYTES = 2048
MAX_PANELS = 64
# Streams a panel may hold open per panel id. A reconnect after a Wi-Fi drop
# arrives before the old socket is noticed dead, so one is not enough.
MAX_EVENT_STREAMS_PER_PANEL = 2
EVENT_QUEUE_MAX = 16

# What a panel may say about a firmware update in flight (POST /api/panels/{id}/update), and why one
# failed. The panel's own words (firmware/relay-display/main/update_format.h keeps the same two lists,
# and tests/test_panels.py holds the two sides together); anything else is dropped.
UPDATE_STATES = ("downloading", "ready", "restarting", "failed", "installed", "rolled_back")
UPDATE_ERRORS = {
    "download": "the image did not download completely",
    "signature": "the image is not signed with the key this panel trusts",
    "version": "the image is not newer than the one the panel runs",
    "compatible": "the image is for other hardware",
    "install": "the panel could not write the image",
    "rollback": "the new image did not check in, so the panel went back to the old one",
}
UPDATE_REPORT_MAX_BYTES = 512

# What a panel does with the Planning Center service (#80, #81). Off until the
# operator says otherwise; only the two control modes get a key.
SERVICE_MODES = ("off", "show", "control", "both")
CONTROL_MODES = ("control", "both")
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_ST_RE = re.compile(r"^\d{1,20}$")

TRANSCRIPTION = "transcription"
TRANSLATION = "translation"
# The Planning Center service as a feed (#122): {"stream": "service", "service_type": "<id>"}. The
# panel holds the id where a translation holds its language, in 19 characters.
SERVICE = "service"
# The alert buttons as a feed (#121): {"stream": "alert"}. The pane sends alerts; it needs no setting of its own.
ALERT = "alert"
# ProPresenter (#138): the current slide's lyrics and the timers, as feeds that need no setting of their own:
# {"stream": "lyrics"} and {"stream": "timers"}. Which timers, and their thresholds, are per panel, in the
# stored entry's "timers" block (propresenter.clean_timer_settings), because the panel asks for them by its id.
LYRICS = "lyrics"
TIMERS = "timers"
MAX_FEEDS = 2
_FEED_ST_RE = re.compile(r"^\d{1,19}$")
# What the panel's screen may be turned to (#122), clockwise: 0 and 180 landscape, 90 and 270 upright.
ROTATIONS = (0, 90, 180, 270)
# What a panel may say about its board: RELAY_BOARD_ID in firmware/relay-display/main/boards/.
_BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,31}$")

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SCRIPT_RE = re.compile(r"^[a-z]{2,16}$")


def _now() -> float:
    return time.monotonic()


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _text(value: object, limit: int) -> str:
    """A bounded one-line string; control characters become nothing."""
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit]


def clean_feeds(raw: object) -> list[dict[str, str]] | None:
    """One or two feed choices, or None if `raw` is not a valid list of them.

    A feed is {"stream": "transcription"}, {"stream": "translation", "lang":
    "SPANISH"}, {"stream": "service", "service_type": "123"}, {"stream": "alert"}, {"stream": "lyrics"} or
    {"stream": "timers"} (ProPresenter). The language
    must be one Relay can translate into; whether the service type is one Relay
    follows, and the panel shows the service, is the caller's to check.
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FEEDS:
        return None
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        stream = item.get("stream")
        if stream == TRANSCRIPTION:
            out.append({"stream": TRANSCRIPTION})
        elif stream == TRANSLATION:
            lang = str(item.get("lang") or "").upper()
            if lang not in languages.TARGETS:
                return None
            out.append({"stream": TRANSLATION, "lang": lang})
        elif stream == SERVICE:
            st = str(item.get("service_type") or "")
            if not _FEED_ST_RE.fullmatch(st):
                return None
            out.append({"stream": SERVICE, "service_type": st})
        elif stream == ALERT:
            out.append({"stream": ALERT})
        elif stream in (LYRICS, TIMERS):
            out.append({"stream": stream})
        else:
            return None
    return out


def _reported_feeds(raw: object) -> list[dict[str, str]]:
    """What a panel says it shows. Anything odd is dropped rather than refused:
    a check-in is how the operator sees the panel at all."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw[:MAX_FEEDS]:
        if not isinstance(item, dict):
            continue
        stream = item.get("stream")
        if stream == TRANSCRIPTION:
            out.append({"stream": TRANSCRIPTION})
        elif stream == TRANSLATION:
            lang = _text(item.get("lang"), 24).upper()
            if lang:
                out.append({"stream": TRANSLATION, "lang": lang})
        elif stream == SERVICE:
            st = _text(item.get("service_type"), 24)
            if _FEED_ST_RE.fullmatch(st):
                out.append({"stream": SERVICE, "service_type": st})
        elif stream == ALERT:
            out.append({"stream": ALERT})
        elif stream in (LYRICS, TIMERS):
            out.append({"stream": stream})
    return out


def _int(value: object, low: int, high: int, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))


def clean_stored(raw: object) -> dict[str, dict[str, Any]]:
    """The "panels" block of config.json: what the operator set, validated.
    A hand-edited entry that does not validate is dropped, not fatal."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for pid, spec in raw.items():
        if len(out) >= MAX_PANELS:
            break
        if not valid_id(pid) or not isinstance(spec, dict):
            continue
        entry: dict[str, Any] = {}
        name = _text(spec.get("name"), 64)
        if name:
            entry["name"] = name
        feeds = clean_feeds(spec.get("feeds"))
        if feeds:
            entry["feeds"] = feeds
        entry["feed_rev"] = _int(spec.get("feed_rev"), 0, 2**31 - 1)
        rotation = spec.get("rotation")
        if rotation in ROTATIONS and not isinstance(rotation, bool):
            entry["rotation"] = rotation
        entry["rotation_rev"] = _int(spec.get("rotation_rev"), 0, 2**31 - 1)
        timers = spec.get("timers")
        if isinstance(timers, dict):
            mine = propresenter.timer_fields(timers)[0]
            if mine:
                entry["timers"] = mine
        mode = spec.get("service_mode")
        if mode in SERVICE_MODES and mode != "off":
            entry["service_mode"] = mode
            st = str(spec.get("service_type") or "")
            if _ST_RE.match(st):
                entry["service_type"] = st
            key = str(spec.get("control_key") or "")
            if mode in CONTROL_MODES and _KEY_RE.match(key):
                entry["control_key"] = key
        out[pid] = entry
    return out


@dataclass
class Panel:
    id: str
    name: str = ""
    version: str = ""
    # The board it says it is (#122); "" from firmware that does not say, which is a CrowPanel.
    board: str = ""
    # The rotation it says it shows (#122); None from firmware that does not say.
    rotation: int | None = None
    # The newest operator rotation revision it has applied. The panel keeps it in its own storage, as it
    # restarts to apply one; a turn made at the screen does not move it.
    rotation_rev: int = 0
    rotation_changed_by: str = ""
    scripts: list[str] = field(default_factory=list)
    feeds: list[dict[str, str]] = field(default_factory=list)
    ip: str = ""
    uptime_s: int = 0
    rssi: int | None = None
    last_seen: float = 0.0
    # Newest operator feed revision this panel has said it applied, this run.
    acked_rev: int = 0
    # When the feed it shows last changed (wall clock), and whether that was the
    # operator ("admin") or someone at the screen ("screen"). 0 / "" until a
    # change is seen: a first check-in is not a change.
    feeds_changed_at: float = 0.0
    feeds_changed_by: str = ""
    # A firmware update the operator asked this panel for, and not yet finished: {"version", "now"}.
    # Memory only, like the rest of what a panel is: Relay restarting forgets it, and the panel's own
    # staged image is dropped at its next check-in, which carries no offer.
    update_request: dict[str, Any] | None = None
    # How the last update went, as the panel reported it: {"state", "version", "progress", "error", "at"}.
    update: dict[str, Any] | None = None


class RegistryFull(Exception):
    """Every slot holds a panel that is online, or one the operator named."""


class Registry:
    def __init__(self) -> None:
        self.panels: dict[str, Panel] = {}
        self._subs: dict[str, list[asyncio.Queue]] = {}
        # Called after anything the operator's list shows has changed; main wires
        # it to the admin status stream.
        self.on_change: Callable[[], None] | None = None
        # The `alert` messages a newly opened stream starts with (#121): main wires it to alerts.py, so a panel
        # that reconnects mid-alert still flashes.
        self.alerts_for: Callable[[str], list[dict]] = lambda pid: []
        # Slide-note tags (#138): panels that were switched to the lyrics, with what they showed ("restore") and
        # what Relay sent ("pushed"), so `captions` can put them back and a choice made at the screen since is left alone.
        self._swapped: dict[str, dict[str, list[dict[str, str]]]] = {}
        # The newest feed revision Relay has sent each panel without storing it, so the next one differs.
        self._rev_floor: dict[str, int] = {}

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # a listener must never break a check-in
                pass

    # ---------------------------------------------------------- what is stored
    @staticmethod
    def stored() -> dict[str, dict[str, Any]]:
        return config.get().get("panels") or {}

    def _save_stored(self, stored: dict[str, dict[str, Any]]) -> None:
        config.save({"panels": stored}, replace=("panels",))

    # ------------------------------------------------------------------ views
    def online(self, panel: Panel) -> bool:
        return panel.last_seen > 0 and _now() - panel.last_seen <= OFFLINE_AFTER_S

    def display_name(self, pid: str) -> str:
        """The operator's name, else the panel's own, else a short stand-in."""
        stored = self.stored().get(pid) or {}
        panel = self.panels.get(pid)
        return stored.get("name") or (panel.name if panel else "") or f"Panel {pid[:4]}"

    def offered_languages(self, pid: str) -> list[dict[str, str]]:
        """Languages the operator may give this panel: every translation target
        whose script the panel has said it can draw. A panel that has not
        reported its scripts gets them all; `update` is the one that refuses."""
        panel = self.panels.get(pid)
        scripts = panel.scripts if panel is not None else []
        out = [{"id": name, "label": languages.target_label(name)}
               for name in languages.TARGETS
               if not scripts or languages.target_script(name) in scripts]
        out.sort(key=lambda row: row["label"].lower())
        return out

    def view(self, pid: str) -> dict[str, Any]:
        stored = self.stored().get(pid) or {}
        panel = self.panels.get(pid) or Panel(pid)
        # A panel that does not say its board is a CrowPanel; one never seen this run is not known.
        board = (panel.board or panelfw.DEFAULT_BOARD) if panel.last_seen else ""
        return {
            "id": pid,
            "name": self.display_name(pid),
            "reported_name": panel.name,
            "named": bool(stored.get("name")),
            "online": self.online(panel),
            "seen_ago_s": round(_now() - panel.last_seen, 1) if panel.last_seen else None,
            "version": panel.version,
            "board": board,
            "board_label": panelfw.board_label(board) if board else "",
            "rotation": panel.rotation,
            "set_rotation": stored.get("rotation"),
            "rotation_changed_by": panel.rotation_changed_by or None,
            "can_reboot": bool(self._subs.get(pid)),
            "scripts": panel.scripts,
            "feeds": panel.feeds,
            "set_feeds": stored.get("feeds"),
            "ip": panel.ip,
            "uptime_s": panel.uptime_s,
            "rssi": panel.rssi,
            "languages": self.offered_languages(pid),
            "feeds_changed_ago_s": (round(time.time() - panel.feeds_changed_at, 1)
                                    if panel.feeds_changed_at else None),
            "feeds_changed_by": panel.feeds_changed_by or None,
            "update_available": self.update_available(panel),
            "update_requested": panel.update_request is not None,
            "update": self._update_view(panel),
            "service_mode": stored.get("service_mode") or "off",
            "service_type": stored.get("service_type") or None,
            # This panel's own ProPresenter timer settings (None: it follows the global ones).
            "set_timers": stored.get("timers") or None,
        }

    # ------------------------------------------------------- service mode
    def timer_settings(self, pid: str) -> dict[str, Any] | None:
        """The panel's own ProPresenter timer settings, for the timers feed."""
        return self.stored().get(pid, {}).get("timers") or None

    def service(self, pid: str) -> dict[str, Any]:
        """What the panel is told about the Planning Center service: its mode,
        the service type it follows (None: its own pick), and its control key
        while it may control."""
        stored = self.stored().get(pid) or {}
        mode = stored.get("service_mode") or "off"
        out: dict[str, Any] = {"mode": mode, "service_type": stored.get("service_type") or None}
        if mode in CONTROL_MODES and stored.get("control_key"):
            out["key"] = stored["control_key"]
        return out

    def control_key(self, pid: str) -> str | None:
        """The key a panel must send with a LIVE button, or None if it may not control."""
        stored = self.stored().get(pid) or {}
        if stored.get("service_mode") in CONTROL_MODES:
            return stored.get("control_key") or None
        return None

    def online_signature(self) -> tuple:
        """Which panels are online right now: what changes with the clock alone,
        so the admin stream can notice a panel going quiet without a timer."""
        return tuple(sorted(pid for pid, p in self.panels.items() if self.online(p)))

    def listing(self) -> list[dict[str, Any]]:
        ids = set(self.panels) | set(self.stored())
        rows = [self.view(pid) for pid in ids]
        rows.sort(key=lambda r: (not r["online"], r["name"].lower(), r["id"]))
        return rows

    def known(self, pid: str) -> bool:
        return pid in self.panels or pid in self.stored()

    # -------------------------------------------------------------- check-in
    def _make_room(self) -> None:
        """Free a slot for a new panel by dropping the stalest one that is
        offline and that the operator never touched. Raises RegistryFull when
        there is none."""
        stored = self.stored()
        if len(self.panels) < MAX_PANELS:
            return
        stale = [p for p in self.panels.values()
                 if not self.online(p) and p.id not in stored and not self._subs.get(p.id)]
        if not stale:
            raise RegistryFull()
        victim = min(stale, key=lambda p: p.last_seen)
        del self.panels[victim.id]

    def checkin(self, body: dict, peer_ip: str) -> dict[str, Any]:
        """Record a check-in. Returns what the panel should be told: its name,
        the interval, and the operator's feed if it has not applied it yet."""
        pid = body.get("id")
        if not valid_id(pid):
            raise ValueError("id must be 32 lower-case hex characters")
        panel = self.panels.get(pid)
        if panel is None:
            self._make_room()
            panel = self.panels[pid] = Panel(pid)
        old_feeds = panel.feeds if panel.last_seen else None
        old_acked = panel.acked_rev
        old_rotation = panel.rotation if panel.last_seen else None
        old_rotation_rev = panel.rotation_rev
        panel.name = _text(body.get("name"), 64)
        panel.version = _text(body.get("version"), 32)
        board = body.get("board")
        panel.board = board if isinstance(board, str) and _BOARD_RE.fullmatch(board) else ""
        rotation = body.get("rotation")
        panel.rotation = rotation if rotation in ROTATIONS and not isinstance(rotation, bool) else None
        panel.rotation_rev = _int(body.get("rotation_rev"), 0, 2**31 - 1)
        if old_rotation is not None and panel.rotation is not None and panel.rotation != old_rotation:
            panel.rotation_changed_by = "admin" if panel.rotation_rev != old_rotation_rev else "screen"
        scripts = body.get("scripts")
        panel.scripts = [s for s in (scripts if isinstance(scripts, list) else [])[:8]
                         if isinstance(s, str) and _SCRIPT_RE.match(s)]
        panel.feeds = _reported_feeds(body.get("feeds"))
        panel.ip = _reported_ip(body.get("ip")) or peer_ip
        panel.uptime_s = _int(body.get("uptime_s"), 0, 2**31 - 1)
        rssi = body.get("rssi")
        panel.rssi = _int(rssi, -127, 0) if not isinstance(rssi, bool) and isinstance(rssi, (int, float)) else None
        panel.acked_rev = max(panel.acked_rev, _int(body.get("feed_rev"), 0, 2**31 - 1))
        if old_feeds is not None and panel.feeds != old_feeds:
            # Applying the operator's revision moves `feed_rev`; a choice made at
            # the screen does not.
            panel.feeds_changed_at = time.time()
            panel.feeds_changed_by = "admin" if panel.acked_rev > old_acked else "screen"
        panel.last_seen = _now()
        self._settle_update(panel)
        reply: dict[str, Any] = {
            "ok": True,
            "name": self.display_name(pid),
            "interval_s": CHECKIN_INTERVAL_S,
        }
        pending = self._pending_feed(pid)
        if pending:
            reply["set_feed"] = pending
        offer = self._update_offer(panel)
        if offer:
            reply["update"] = offer
        rotation = self._pending_rotation(pid)
        if rotation:
            reply["set_rotation"] = rotation
        reply["service"] = self.service(pid)
        self._changed()
        return reply

    # ------------------------------------------------------- firmware updates
    @staticmethod
    def firmware_for(panel: Panel) -> panelfw.Info | None:
        """The image stored for this panel's board, or None (none stored, or a board on hold)."""
        board = panelfw.panel_board(panel.board)
        return panelfw.current(board) if board else None

    @classmethod
    def update_available(cls, panel: Panel) -> bool:
        """An image is stored for this panel's board that is a step forward from what it says it runs."""
        info = cls.firmware_for(panel)
        return info is not None and bool(panel.version) and panelfw.newer(info.version, panel.version)

    @staticmethod
    def _update_message(info: panelfw.Info, now: bool) -> dict[str, Any]:
        return {"version": info.version, "size": info.size, "sha256": info.sha256,
                "path": panelfw.image_url(info), "now": bool(now)}

    def _update_offer(self, panel: Panel) -> dict[str, Any] | None:
        """What the panel is told to fetch: the stored image, if the operator asked this panel for
        it and it is still the version asked for. No offer is also how a panel learns the request
        was withdrawn (the image replaced or removed), so it drops what it had staged."""
        request = panel.update_request
        info = self.firmware_for(panel)
        if request is None or info is None or request["version"] != info.version:
            return None
        return self._update_message(info, request["now"])

    def _update_view(self, panel: Panel) -> dict[str, Any] | None:
        u = panel.update
        if u is None:
            return None
        return {"state": u["state"], "version": u["version"], "progress": u["progress"],
                "error": u["error"], "error_text": UPDATE_ERRORS.get(u["error"], ""),
                "ago_s": round(max(0.0, time.time() - u["at"]), 1)}

    def _settle_update(self, panel: Panel) -> None:
        """A check-in from the version that was asked for is the update's last word, whether or not
        the panel got to say so itself."""
        request = panel.update_request
        if request is not None and panel.version == request["version"]:
            panel.update_request = None
            panel.update = {"state": "installed", "version": panel.version, "progress": 100,
                            "error": "", "at": time.time()}

    def request_update(self, pid: str, now: bool = False) -> dict[str, Any]:
        """Ask one panel to install the stored image: at a quiet moment, or at once with `now`.
        Pushed on its event stream if one is open, and carried in its next check-in reply either way.
        Raises KeyError (no such panel), LookupError (no image stored) or ValueError (nothing to do)."""
        panel = self.panels.get(pid) if valid_id(pid) else None
        if panel is None:
            raise KeyError(pid)
        info = self.firmware_for(panel)
        if info is None:
            board = panelfw.panel_board(panel.board)
            raise LookupError(f"no firmware has been uploaded for the {panelfw.board_label(board)}" if board
                              else f"Relay keeps no firmware for this panel's board ({panel.board})")
        if not self.update_available(panel):
            raise ValueError(f"{self.display_name(pid)} already runs {panel.version or 'a version Relay cannot read'}"
                             f" or newer; the image held is {info.version}")
        panel.update_request = {"version": info.version, "now": bool(now)}
        panel.update = {"state": "queued", "version": info.version, "progress": 0, "error": "",
                        "at": time.time()}
        self._push(pid, {"type": "update_available", **self._update_message(info, now)})
        self._changed()
        return self.view(pid)

    def request_update_all(self, now: bool = False, board: str | None = None) -> dict[str, int]:
        """Ask every panel that is online and has an update available (of `board`, or of any board).
        Panels that are offline are left for the operator to ask one by one: an update should not be a
        surprise on a panel that comes back days later."""
        asked = skipped = 0
        for panel in list(self.panels.values()):
            if board is not None and panelfw.panel_board(panel.board) != board:
                continue
            if not self.update_available(panel):
                continue
            if not self.online(panel):
                skipped += 1
                continue
            try:
                self.request_update(panel.id, now)
                asked += 1
            except (KeyError, LookupError, ValueError):
                pass
        return {"requested": asked, "offline": skipped}

    def firmware_changed(self, board: str | None = None) -> None:
        """A board's stored image (or any, with no `board`) was replaced or removed: no panel of it is
        still asked for the old one. A panel that had staged it drops it at its next check-in, which
        carries no offer."""
        for panel in self.panels.values():
            if board is not None and panelfw.panel_board(panel.board) != board:
                continue
            if panel.update_request is not None:
                panel.update_request = None
                if panel.update and panel.update["state"] in ("queued", "downloading", "ready"):
                    panel.update = None
        self._changed()

    def report_update(self, pid: str, body: dict) -> bool:
        """A panel's word on an update. False for a panel Relay does not know. Everything is bounded
        and anything unfamiliar is dropped: nothing here is authenticated."""
        panel = self.panels.get(pid) if valid_id(pid) else None
        if panel is None:
            return False
        state = body.get("state")
        if state not in UPDATE_STATES:
            return True
        error = body.get("error")
        error = error if error in UPDATE_ERRORS else ""
        if state == "rolled_back" and not error:
            error = "rollback"
        version = _text(body.get("version"), 32)
        if panelfw.parse_version(version) is None:
            version = (panel.update_request or {}).get("version", "")
        panel.update = {"state": state, "version": version,
                        "progress": 100 if state in ("ready", "restarting", "installed") else _int(body.get("progress"), 0, 100),
                        "error": error if state in ("failed", "rolled_back") else "", "at": time.time()}
        if state in ("failed", "rolled_back", "installed"):
            # Over, one way or the other. Asking again is the operator's.
            panel.update_request = None
        self._changed()
        return True

    def _pending_feed(self, pid: str) -> dict[str, Any] | None:
        """The operator's feed as a `set_feed` event, if this panel has not said
        it applied it. The panel acknowledges with the `rev` on its check-ins,
        so a screen choice made after it is not undone by the next check-in."""
        stored = self.stored().get(pid) or {}
        feeds = stored.get("feeds")
        if not feeds:
            return None
        rev = int(stored.get("feed_rev") or 0)
        panel = self.panels.get(pid)
        if panel is not None and panel.acked_rev >= rev:
            return None
        return {"type": "set_feed", "rev": rev, "feeds": feeds}

    def _pending_rotation(self, pid: str) -> dict[str, Any] | None:
        """The operator's rotation as a `set_rotation`, if this panel has not applied that revision. As
        with the feed, a turn made at the panel's screen afterwards is not undone by the next check-in:
        the newest change wins, from either side."""
        stored = self.stored().get(pid) or {}
        if stored.get("rotation") is None:
            return None
        rev = int(stored.get("rotation_rev") or 0)
        panel = self.panels.get(pid)
        if panel is not None and panel.rotation_rev == rev:
            return None
        return {"type": "set_rotation", "rev": rev, "deg": stored["rotation"]}

    # ---------------------------------------------------------------- events
    def subscribe(self, pid: str) -> asyncio.Queue:
        """A queue of finished SSE frames for one panel, primed with its pending
        `set_feed`. Opening a stream for a panel that never checked in is the
        caller's to refuse."""
        q: asyncio.Queue = asyncio.Queue(maxsize=EVENT_QUEUE_MAX)
        subs = self._subs.setdefault(pid, [])
        while len(subs) >= MAX_EVENT_STREAMS_PER_PANEL:
            self._close(subs.pop(0))
        subs.append(q)
        pending = self._pending_feed(pid)
        if pending:
            q.put_nowait(sse_frame(pending))
        panel = self.panels.get(pid)
        offer = self._update_offer(panel) if panel is not None else None
        if offer:
            q.put_nowait(sse_frame({"type": "update_available", **offer}))
        service = self.service(pid)
        if service["mode"] != "off":   # off is the default; the check-in reply says it either way
            q.put_nowait(sse_frame({"type": "service_mode", **service}))
        rotation = self._pending_rotation(pid)
        if rotation:
            q.put_nowait(sse_frame(rotation))
        for alert in self.alerts_for(pid)[:4]:
            q.put_nowait(sse_frame(alert))
        return q

    def unsubscribe(self, pid: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(pid)
        if subs and q in subs:
            subs.remove(q)
        if not subs:
            self._subs.pop(pid, None)

    @staticmethod
    def _close(q: asyncio.Queue) -> None:
        """End a stream: the generator stops on None."""
        while True:
            try:
                q.put_nowait(None)
                return
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    return

    def _push(self, pid: str, message: dict) -> int:
        frame = sse_frame(message)
        sent = 0
        for q in list(self._subs.get(pid, [])):
            try:
                q.put_nowait(frame)
                sent += 1
            except asyncio.QueueFull:
                # A reader that stopped reading: cut it, it will reconnect and
                # be told the current state.
                self._close(q)
        return sent

    def stream_count(self) -> int:
        return sum(len(v) for v in self._subs.values())

    # ------------------------------------------------------- operator actions
    # ----------------------------------------------------- feeds set by slide-note tags
    def _rev_base(self, pid: str, entry: dict[str, Any] | None = None) -> int:
        """The highest feed revision this panel has been given or has applied: the next one is above it."""
        entry = entry if entry is not None else (self.stored().get(pid) or {})
        panel = self.panels.get(pid)
        return max(int(entry.get("feed_rev") or 0), panel.acked_rev if panel else 0, self._rev_floor.get(pid, 0))

    def _send_feeds(self, pid: str, feeds: list[dict[str, str]]) -> int:
        """Tell a panel to show `feeds` now, without storing them: the operator's own choice (if any) stays what
        it is, and a panel that comes back later is not told again."""
        rev = self._rev_floor[pid] = self._rev_base(pid) + 1
        sent = self._push(pid, {"type": "set_feed", "rev": rev, "feeds": feeds})
        panel = self.panels.get(pid)
        if sent and panel is not None:
            panel.feeds = [dict(f) for f in feeds]   # what it shows now; its next check-in says it for itself
        return sent

    def _reachable(self) -> list[Panel]:
        return [p for p in self.panels.values() if self.online(p) and self._subs.get(p.id)]

    @staticmethod
    def _with_caption(feeds: list[dict[str, str]], feed: dict[str, str]) -> list[dict[str, str]]:
        """`feeds` with each transcription or translation feed replaced by `feed` (once). A panel on the service,
        the alert buttons or the timers has no captions to replace and comes back unchanged."""
        out: list[dict[str, str]] = []
        for f in feeds:
            f = feed if f.get("stream") in (TRANSCRIPTION, TRANSLATION) else f
            if f not in out:
                out.append(f)
        return out

    def show_lyrics(self) -> str:
        """Every panel showing captions shows the ProPresenter lyrics instead, until `show_captions`."""
        if not propresenter.panel_offers()["lyrics"]:
            raise ValueError("Lyrics on caption panels are switched off (ProPresenter page, Lyrics), so no panel was changed.")
        lyrics = {"stream": LYRICS}
        done = 0
        for panel in self._reachable():
            held = self._swapped.get(panel.id)
            if held is not None and panel.feeds == held["pushed"]:
                continue                       # already showing it
            self._swapped.pop(panel.id, None)  # moved at the screen since: a fresh start
            new = self._with_caption(panel.feeds, lyrics)
            if new == panel.feeds or not clean_feeds(new):
                continue
            before = [dict(f) for f in panel.feeds]
            if self._send_feeds(panel.id, new):
                self._swapped[panel.id] = {"pushed": new, "restore": before}
                done += 1
        self._changed()
        return f"Lyrics shown on {done} panel{'s' if done != 1 else ''}."

    def show_captions(self) -> str:
        """Put back what each panel showed before `show_lyrics`. A panel someone has changed at its screen since
        is left as it is."""
        done = 0
        for pid, held in list(self._swapped.items()):
            panel = self.panels.get(pid)
            if panel is None or (panel.feeds and panel.feeds != held["pushed"]):
                self._swapped.pop(pid, None)   # changed at the screen, or gone
                continue
            if self._send_feeds(pid, held["restore"]):
                self._swapped.pop(pid, None)
                done += 1
        self._changed()
        return f"Captions back on {done} panel{'s' if done != 1 else ''}." if done else "No panel was showing lyrics from a tag."

    def show_language(self, target: str) -> str:
        """Panels showing captions show them in `target` (a name from languages.py). A panel that is on the lyrics
        because of a tag goes back to this language when `show_captions` puts it back."""
        spec = languages.TARGETS.get(target)
        if spec is None:
            raise ValueError("That is not a language Relay translates into.")
        cfg = config.get()
        if target == str(cfg.get("source_language") or "").upper():
            feed = {"stream": TRANSCRIPTION}
        elif (cfg.get("targets") or {}).get(target, {}).get("enabled"):
            feed = {"stream": TRANSLATION, "lang": target}
        else:
            raise ValueError(f"{spec['label']} is not switched on under Languages, so no panel was changed.")
        done = skipped = 0
        for panel in self._reachable():
            if feed["stream"] == TRANSLATION and panel.scripts and spec["script"] not in panel.scripts:
                skipped += 1
                continue
            held = self._swapped.get(panel.id)
            if held is not None and panel.feeds == held["pushed"]:
                held["restore"] = self._with_caption(held["restore"], feed)
                done += 1
                continue
            self._swapped.pop(panel.id, None)
            new = self._with_caption(panel.feeds, feed)
            if new != panel.feeds and self._send_feeds(panel.id, new):
                done += 1
        self._changed()
        out = f"{spec['label']} set on {done} panel{'s' if done != 1 else ''}."
        return out + (f" {skipped} cannot draw it." if skipped else "")

    def update(self, pid: str, *, name: str | None = None, feeds: Any = ...,
               service_mode: str | None = None, service_type: Any = ...,
               rotation: Any = ..., timers: Any = ...) -> dict[str, Any]:
        """Rename a panel, set its feed, its rotation and/or its service mode.
        `feeds=None` clears the operator's feed; leaving `feeds` out changes
        nothing. `rotation` is 0 or 180, or None to leave the panel as it is.
        `service_type=None` lets the panel pick. Pushes `set_feed`,
        `set_rotation` and `service_mode` at once. `timers` is this panel's own ProPresenter timer settings
        ({"amber_s", "red_s", "show"}, any of them), or None to follow the global ones; the panel asks for them
        when it opens its timers stream, so nothing is pushed. A control mode gets a new
        random key when the panel had none; leaving control drops it. A Service
        feed needs a service mode that shows the service.
        Raises KeyError for an unknown panel and ValueError for a bad value."""
        if not valid_id(pid) or not self.known(pid):
            raise KeyError(pid)
        stored = {k: dict(v) for k, v in self.stored().items()}
        if len(stored) >= MAX_PANELS and pid not in stored:
            raise ValueError("too many named panels; forget one first")
        entry = stored.setdefault(pid, {"feed_rev": 0})
        if name is not None:
            name = _text(name, 64)
            if name:
                entry["name"] = name
            else:
                entry.pop("name", None)
        push = None
        if feeds is not ...:
            if feeds is None:
                entry.pop("feeds", None)
            else:
                clean = clean_feeds(feeds)
                if clean is None:
                    raise ValueError("feeds must be one or two of transcription, a translation language, "
                                     "the service, the alert buttons, the lyrics or the timers")
                panel = self.panels.get(pid)
                if panel is not None and panel.scripts:
                    for feed in clean:
                        if feed["stream"] == TRANSLATION:
                            script = languages.target_script(feed["lang"])
                            if script not in panel.scripts:
                                raise ValueError(f"this panel cannot draw {script} text")
                entry["feeds"] = clean
                entry["feed_rev"] = self._rev_base(pid, entry) + 1
                self._swapped.pop(pid, None)   # the operator's choice is the newest
                push = {"type": "set_feed", "rev": entry["feed_rev"], "feeds": clean}
        service_push = False
        if service_mode is not None:
            if service_mode not in SERVICE_MODES:
                raise ValueError("service mode must be off, show, control or both")
            if service_mode == "off":
                for k in ("service_mode", "control_key"):
                    entry.pop(k, None)
            else:
                entry["service_mode"] = service_mode
                if service_mode in CONTROL_MODES:
                    entry.setdefault("control_key", secrets.token_hex(16))
                else:
                    entry.pop("control_key", None)
            service_push = True
        if service_type is not ...:
            st = "" if service_type is None else str(service_type).strip()
            if st and not _ST_RE.match(st):
                raise ValueError("service type must be a Planning Center service type id")
            if st:
                entry["service_type"] = st
            else:
                entry.pop("service_type", None)
            service_push = True
        if not entry.get("service_mode"):
            entry.pop("service_type", None)
        # A Service feed is shown only while the service mode shows the service, and only the type the
        # panel is held to, if it is held to one (the panel offers nothing else).
        for feed in entry.get("feeds") or []:
            if feed["stream"] != SERVICE or (push is None and not service_push):
                continue
            if entry.get("service_mode") not in ("show", "both"):
                raise ValueError("a Service feed needs the panel's service mode to be Show only or Show and control")
            if entry.get("service_type") and entry["service_type"] != feed["service_type"]:
                raise ValueError("a Service feed must be the service type this panel is set to")
        if timers is not ...:
            if timers is None:
                entry.pop("timers", None)
            elif isinstance(timers, dict):
                mine, problem = propresenter.timer_fields(timers)
                if problem:
                    raise ValueError(problem)
                if mine:
                    entry["timers"] = mine
                else:
                    entry.pop("timers", None)
            else:
                raise ValueError("timers must be an object, or null to follow the global settings")
        rotation_push = None
        if rotation is not ...:
            if rotation is None:
                entry.pop("rotation", None)
            elif rotation in ROTATIONS and not isinstance(rotation, bool):
                # A new revision every time, as with the feed: setting it again wins over a turn made at
                # the screen since.
                entry["rotation"] = rotation
                entry["rotation_rev"] = int(entry.get("rotation_rev") or 0) + 1
                rotation_push = {"type": "set_rotation", "rev": entry["rotation_rev"], "deg": rotation}
            else:
                raise ValueError("rotation must be 0, 90, 180 or 270")
        if (not entry.get("name") and not entry.get("feeds") and not entry.get("service_mode")
                and entry.get("rotation") is None and not entry.get("timers")):
            stored.pop(pid, None)
        self._save_stored(stored)
        if push is not None:
            self._push(pid, push)
        if service_push:
            self._push(pid, {"type": "service_mode", **self.service(pid)})
        if rotation_push is not None:
            self._push(pid, rotation_push)
        self._changed()
        return self.view(pid)

    def reboot(self, pid: str) -> dict[str, Any]:
        """Restart a panel. Sent on its event stream only, never in a check-in reply, so a panel cannot
        be left restarting at every check-in. Raises KeyError for an unknown panel and LookupError for
        one with no stream open (it is offline, or runs firmware from before #122 and has just come
        back)."""
        if not valid_id(pid) or not self.known(pid):
            raise KeyError(pid)
        if self._push(pid, {"type": "reboot"}) == 0:
            raise LookupError(f"{self.display_name(pid)} is not connected; it can only be restarted while online")
        return self.view(pid)

    def forget(self, pid: str) -> bool:
        """Drop a panel from the list and from config.json. A panel that is
        still checking in comes straight back, unnamed."""
        if not valid_id(pid) or not self.known(pid):
            return False
        stored = {k: dict(v) for k, v in self.stored().items() if k != pid}
        self._save_stored(stored)
        self.panels.pop(pid, None)
        self._swapped.pop(pid, None)
        for q in self._subs.pop(pid, []):
            self._close(q)
        self._changed()
        return True

    def push_alert(self, pid: str, message: dict) -> int:
        """An `alert` or `alert_end` (#121) onto a panel's event stream. Returns how many streams took it."""
        return self._push(pid, message)

    def push_update_available(self, message: dict[str, Any], pid: str | None = None) -> int:
        """Tell one panel, or every panel with a stream open, that a firmware
        update is waiting. `message` is the update's own description (version,
        size, ...); only plain scalars are passed on. Returns how many streams
        it was written to."""
        body = {"type": "update_available"}
        for key, value in message.items():
            if key != "type" and isinstance(value, (str, int, float, bool)):
                body[key] = value
        targets = [pid] if pid else list(self._subs)
        return sum(self._push(t, body) for t in targets)


def _reported_ip(value: object) -> str:
    try:
        return str(ipaddress.ip_address(_text(value, 45)))
    except ValueError:
        return ""


def sse_frame(message: dict) -> bytes:
    return f"data: {json.dumps(message, ensure_ascii=False)}\n\n".encode("utf-8")


registry = Registry()
