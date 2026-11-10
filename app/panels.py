"""Caption panels: who is out there, and what each one should show.

A panel (firmware/relay-display) checks in over plain HTTP on the viewer port
every 30 s and keeps `GET /api/panels/{id}/events` open. Nothing here is
authenticated -- panels do not pair, by decision -- so everything a stray LAN
client can do to this module is bounded:

* the registry holds at most MAX_PANELS entries, and a check-in body is at most
  CHECKIN_MAX_BYTES, so a flood cannot grow memory or config.json;
* the event stream carries only a feed choice, never a secret;
* nothing a panel sends is ever written to config.json. Only what the operator
  sets from the admin side (a name, a feed) is persisted -- and that side stays
  under /api/admin/*, which the viewer port refuses.

The registry is in memory. The operator's name and feed per panel live in
config.json under "panels", so they survive a restart.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config, languages, panelfw

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

TRANSCRIPTION = "transcription"
TRANSLATION = "translation"
MAX_FEEDS = 2

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

    A feed is {"stream": "transcription"} or {"stream": "translation", "lang":
    "SPANISH"}. The language must be one Relay can translate into.
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
        out[pid] = entry
    return out


@dataclass
class Panel:
    id: str
    name: str = ""
    version: str = ""
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
        return {
            "id": pid,
            "name": self.display_name(pid),
            "reported_name": panel.name,
            "named": bool(stored.get("name")),
            "online": self.online(panel),
            "seen_ago_s": round(_now() - panel.last_seen, 1) if panel.last_seen else None,
            "version": panel.version,
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
        }

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
        panel.name = _text(body.get("name"), 64)
        panel.version = _text(body.get("version"), 32)
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
        self._changed()
        return reply

    # ------------------------------------------------------- firmware updates
    @staticmethod
    def update_available(panel: Panel) -> bool:
        """An image is stored that is a step forward from what this panel says it runs."""
        info = panelfw.current()
        return info is not None and bool(panel.version) and panelfw.newer(info.version, panel.version)

    @staticmethod
    def _update_message(info: panelfw.Info, now: bool) -> dict[str, Any]:
        return {"version": info.version, "size": info.size, "sha256": info.sha256,
                "path": f"/api/panels/firmware/{info.version}.bin", "now": bool(now)}

    def _update_offer(self, panel: Panel) -> dict[str, Any] | None:
        """What the panel is told to fetch: the stored image, if the operator asked this panel for
        it and it is still the version asked for. No offer is also how a panel learns the request
        was withdrawn (the image replaced or removed), so it drops what it had staged."""
        request = panel.update_request
        info = panelfw.current()
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
        info = panelfw.current()
        if info is None:
            raise LookupError("no panel firmware has been uploaded")
        if not self.update_available(panel):
            raise ValueError(f"{self.display_name(pid)} already runs {panel.version or 'a version Relay cannot read'}"
                             f" or newer; the image held is {info.version}")
        panel.update_request = {"version": info.version, "now": bool(now)}
        panel.update = {"state": "queued", "version": info.version, "progress": 0, "error": "",
                        "at": time.time()}
        self._push(pid, {"type": "update_available", **self._update_message(info, now)})
        self._changed()
        return self.view(pid)

    def request_update_all(self, now: bool = False) -> dict[str, int]:
        """Ask every panel that is online and has an update available. Panels that are offline are
        left for the operator to ask one by one: an update should not be a surprise on a panel that
        comes back days later."""
        asked = skipped = 0
        for panel in list(self.panels.values()):
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

    def firmware_changed(self) -> None:
        """The stored image was replaced or removed: no panel is still asked for the old one. A panel
        that had staged it drops it at its next check-in, which carries no offer."""
        for panel in self.panels.values():
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
    def update(self, pid: str, *, name: str | None = None, feeds: Any = ...) -> dict[str, Any]:
        """Rename a panel and/or set its feed. `feeds=None` clears the operator's
        feed; leaving `feeds` out changes nothing. Pushes `set_feed` at once.
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
                    raise ValueError("feeds must be one or two of transcription or a translation language")
                panel = self.panels.get(pid)
                if panel is not None and panel.scripts:
                    for feed in clean:
                        if feed["stream"] == TRANSLATION:
                            script = languages.target_script(feed["lang"])
                            if script not in panel.scripts:
                                raise ValueError(f"this panel cannot draw {script} text")
                entry["feeds"] = clean
                entry["feed_rev"] = int(entry.get("feed_rev") or 0) + 1
                push = {"type": "set_feed", "rev": entry["feed_rev"], "feeds": clean}
        if not entry.get("name") and not entry.get("feeds"):
            stored.pop(pid, None)
        self._save_stored(stored)
        if push is not None:
            self._push(pid, push)
        self._changed()
        return self.view(pid)

    def forget(self, pid: str) -> bool:
        """Drop a panel from the list and from config.json. A panel that is
        still checking in comes straight back, unnamed."""
        if not valid_id(pid) or not self.known(pid):
            return False
        stored = {k: dict(v) for k, v in self.stored().items() if k != pid}
        self._save_stored(stored)
        self.panels.pop(pid, None)
        for q in self._subs.pop(pid, []):
            self._close(q)
        self._changed()
        return True

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
