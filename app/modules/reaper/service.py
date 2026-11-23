"""Settings, auto-record, markers and alarms for each REAPER computer (#413).

Each computer is a `client.Link`. Once a second the service:

- **follows the service**: when "record while the stream is on air" is set and any stream source reports on air, or
  "record during schedule windows" is set and a schedule window is open, it starts REAPER recording; when neither
  holds any more it stops, after the computer's stop delay, a recording it started itself. A recording someone
  stopped (from Relay or in REAPER) while it should run is not started again until the reason ends and comes back,
  and it is an error ("stopped while the service is live");
- **checks the recording**: no track armed, an expected track not armed, or an armed track silent for the silence
  time while recording are warnings; REAPER not answering (after it answered once in this run) and a recording that
  should be running but is not are errors. A problem raises the operator's chosen alert button once.

Markers (each inserted at REAPER's play cursor, named): a new ProPresenter presentation (core's
`listen("presentation.up")`), a stage message, a slide tag (`[relay:reaper=marker=Sermon]`), a board button and the
admin page. Everything that starts, stops or marks from outside the admin page is a system audit entry naming where it
came from.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from typing import Any

from ... import audit, config, netroute, netsock, schedules
from ...text import same_name
from ...alerts import alerts
from ...stream import stream as show_stream
from ...streamstatus import streams
from .client import DEFAULT_LISTEN, DEFAULT_PORT, DEFAULT_TRACKS, QUIET_S, TRACKS, TRAFFIC, Link

log = logging.getLogger("relay.reaper")

KEY = "reaper"
MAX_MACHINES = 4
MAX_EXPECT = 16
TICK_S = 1.0
START_GRACE_S = 15.0          # how long REAPER has to report recording after Relay asked (it answers late after a refresh)
HEARTBEAT_S = 5.0
RETRY_S = 5.0                 # try a taken listen port again this often
STOP_CONFIRM_S = 30.0         # REAPER should say it stopped by now (it took 9.9 s live, finishing the files)
STOP_DELAY_MAX = 3600
SILENT_MIN, SILENT_MAX = 5, 600
SILENT_VU = 0.1               # REAPER's meter (0..1) below this is silence; about -60 dB (0.5 read -27 dB live)
MARKER_MAX = 60
UNEXPECTED_S = 600.0          # an unexpected stop stays an error this long unless recording starts again

_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-:]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[a-z0-9]{1,8}")
_BUTTON_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join("".join(ch for ch in value if ch.isprintable()).split())[:limit]


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return default
    return value


def clean_machine(m: Any, seen: set[str], ports: set[int]) -> dict | None:
    if not isinstance(m, dict):
        return None
    mid = m.get("id") if isinstance(m.get("id"), str) and _ID_RE.fullmatch(m["id"]) else ""
    if not mid or mid in seen:
        mid = uuid.uuid4().hex[:8]
    seen.add(mid)
    host = m.get("host").strip() if isinstance(m.get("host"), str) else ""
    if not _HOST_RE.fullmatch(host):
        host = ""
    listen = _int(m.get("listen_port"), DEFAULT_LISTEN, 1024, 65535)
    while listen in ports:                      # each computer answers on its own port
        listen = listen + 1 if listen < 65535 else 1024
    ports.add(listen)

    def button(k: str) -> str:
        v = m.get(k)
        return v if isinstance(v, str) and _BUTTON_RE.fullmatch(v) else ""

    expect = []
    for name in m.get("expect") if isinstance(m.get("expect"), list) else []:
        t = _text(name, 60)
        if t and t.lower() not in (e.lower() for e in expect):
            expect.append(t)
    return {"id": mid, "name": _text(m.get("name"), 24) or "REAPER", "host": host,
            "port": _int(m.get("port"), DEFAULT_PORT, 1, 65535), "listen_port": listen,
            "tracks": _int(m.get("tracks"), DEFAULT_TRACKS, 1, TRACKS),
            "follow_stream": m.get("follow_stream") is True, "follow_start": m.get("follow_start") is True,
            "follow_schedule": m.get("follow_schedule") is True,
            "stop_delay": _int(m.get("stop_delay"), 60, 0, STOP_DELAY_MAX),
            "song_markers": m.get("song_markers") is not False, "message_markers": m.get("message_markers") is True,
            "check_armed": m.get("check_armed") is not False, "expect": expect[:MAX_EXPECT],
            "silent_s": _int(m.get("silent_s"), 30, 0, SILENT_MAX),
            "alert_error": button("alert_error"), "alert_warn": button("alert_warn")}


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    seen: set[str] = set()
    ports: set[int] = set()
    sent = raw.get("machines") if isinstance(raw.get("machines"), list) else []
    machines = [c for c in (clean_machine(m, seen, ports) for m in sent[:MAX_MACHINES]) if c]
    for m in machines:
        if m["silent_s"] and m["silent_s"] < SILENT_MIN:
            m["silent_s"] = SILENT_MIN
    return {"machines": machines}


def clean_marker(name: Any) -> str:
    return _text(name, MARKER_MAX)


def stream_on_air() -> bool:
    return any(e.get("on_air") for e in streams.entries())


def schedule_open() -> bool:
    from ...scheduler import Scheduler, engine_schedules, plan_source

    try:
        return schedules.current_window(engine_schedules(), Scheduler.now(), config.timezone(), plan_source()) is not None
    except Exception:
        return False


def _clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class Machine:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.id = cfg["id"]
        self.link = Link(cfg["host"], cfg["port"], cfg["listen_port"], SILENT_VU, self._changed, cfg["tracks"])
        self.error = ""                    # a fixed sentence when the socket could not be opened
        self.ever_reached = False
        self.wanted = False                # the service says record
        self.wanted_since = 0.0
        self.unwanted_since = 0.0
        self.ours = False                  # Relay's auto-record started the recording that is running
        self.asked_at = 0.0                # Relay sent /record at this time and waits for REAPER to say so
        self.held = False                  # someone stopped while wanted: do not start again until wanted ends
        self.stopping = False              # Relay sent /stop
        self.stop_sent_at = 0.0            # when, until REAPER says it stopped
        self.unexpected_at = 0.0
        self.alerted = ""
        self._task: asyncio.Task | None = None

    # -- life ------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        if not self.cfg["host"]:
            self.error = "Set REAPER's address."
            return
        try:
            r = netroute.resolve(TRAFFIC)
        except KeyError:
            r = None
        if r is not None and not r.available:
            self.error = r.reason or "The network port for REAPER is not available."
            return
        if not await self._open():
            self._task = asyncio.create_task(self._retry())
            return
        self._task = asyncio.create_task(self._heartbeat())

    async def _open(self) -> bool:
        try:
            await self.link.open()
            self.error = ""
            return True
        except netsock.RouteError:
            self.error = netsock.failure(TRAFFIC, f"Could not listen on UDP port {self.cfg['listen_port']}: is "
                                                  "another program using it?")
            return False

    async def _retry(self) -> None:
        """The listen port was taken (often by a Relay that is still closing): try again until it is free."""
        while not await self._open():
            await asyncio.sleep(RETRY_S)
        await self._heartbeat()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        self.link.close()

    async def _heartbeat(self) -> None:
        """Ask for the whole state only when REAPER has been quiet: a refresh delays its live answers."""
        last = time.time()
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            now = time.time()
            if now - max(self.link.state.heard_at, last) >= QUIET_S:
                last = now
                self.link.refresh()

    # -- what REAPER reports -----------------------------------------------------------------------------------
    def _changed(self, kind: str) -> None:
        st = self.link.state
        self.ever_reached = True
        if kind != "record":
            return
        if st.recording:
            self.asked_at = 0.0
            self.unexpected_at = 0.0
            self.held = False
            for t in st.tracks.values():          # silence is counted from the start of the take
                t.loud_at = max(t.loud_at, time.time())
        else:
            self.stop_sent_at = 0.0
            if self.wanted and self.ours and not self.stopping:
                self.unexpected_at = time.time()
                self.held = True
                audit.record("reaper.stopped", f"REAPER ({self.cfg['name']}): recording stopped while the service "
                                               "is live", target=self.id)
            self.ours = False
            self.stopping = False

    # -- the service -------------------------------------------------------------------------------------------
    def tick(self, on_air: bool, window: bool, now: float, started: bool = False) -> None:
        cfg, st = self.cfg, self.link.state
        if not self.link.reached(now):
            return
        want = (cfg["follow_stream"] and on_air) or (cfg["follow_schedule"] and window) or (cfg["follow_start"] and started)
        if want != self.wanted:
            self.wanted = want
            if want:
                self.wanted_since, self.held = now, False
            else:
                self.unwanted_since = now
        why = ("the stream is on air" if cfg["follow_stream"] and on_air else
               "Start was pressed" if cfg["follow_start"] and started else "a schedule window is open")
        if want and not st.recording and not self.held and not self.asked_at:
            if self.link.record():
                self.asked_at, self.ours = now, True
                audit.record("reaper.record", f"REAPER ({cfg['name']}): started recording because {why}",
                             target=self.id)
        if self.asked_at and now - self.asked_at > START_GRACE_S and not st.recording:
            self.asked_at = 0.0
            self.held = True                    # it did not start: say so and do not keep pressing record
            self.unexpected_at = now
        if not want and self.ours and st.recording and now - self.unwanted_since >= cfg["stop_delay"]:
            if self.link.stop():
                self.stopping, self.stop_sent_at = True, now
                audit.record("reaper.stop", f"REAPER ({cfg['name']}): stopped recording, the service is over",
                             target=self.id)
        if self.unexpected_at and (not want or now - self.unexpected_at > UNEXPECTED_S):
            self.unexpected_at = 0.0

    # -- health ------------------------------------------------------------------------------------------------
    def tracks(self) -> list[dict]:
        out = []
        for n in sorted(self.link.state.tracks):
            t = self.link.state.tracks[n]
            if t.real():
                out.append({"n": n, "name": t.name, "armed": t.armed, "muted": t.muted, "vu": round(t.vu, 3)})
        return out

    def problems(self, now: float | None = None) -> list[tuple[str, str]]:
        """[(level, sentence)], worst first. Sentences are fixed apart from names the operator gave."""
        now = time.time() if now is None else now
        cfg, st = self.cfg, self.link.state
        if self.error:
            return [("error", self.error)]
        if self.stop_sent_at and now - self.stop_sent_at > STOP_CONFIRM_S:
            return [("warn", "REAPER has not confirmed the stop. It may be waiting on its prompt to save, delete or "
                             "rename the new files: answer it in REAPER, and turn it off under Settings > Recording.")]
        if not self.link.reached(now):
            if self.ever_reached:
                return [("error", "REAPER stopped answering. Is it running, with the OSC surface on?")]
            return [("idle", "Waiting for REAPER to answer. Check the OSC surface's device IP and port.")]
        out: list[tuple[str, str]] = []
        if self.unexpected_at:
            out.append(("error", "REAPER is not recording while the service is live."))
        real = [t for t in st.tracks.values() if t.real()]
        armed = [t for t in real if t.armed]
        if cfg["check_armed"] and not armed:
            out.append(("warn", "No track is armed to record."))
        for name in cfg["expect"]:
            hit = [t for t in real if same_name(t.name, name)]
            if not hit:
                out.append(("warn", f"There is no track called {name}."))
            elif not any(t.armed for t in hit):
                out.append(("warn", f"{name} is not armed."))
        if st.recording and cfg["silent_s"]:
            for t in armed:
                quiet = now - (t.loud_at or st.record_since or now)
                if quiet >= cfg["silent_s"]:
                    out.append(("warn", f"{t.name or 'Track ' + str(t.n)} has been silent for {int(quiet)} s."))
        return out

    def health(self, now: float | None = None) -> tuple[str, str]:
        probs = self.problems(now)
        for level in ("error", "warn", "idle"):
            hit = [p for p in probs if p[0] == level]
            if hit:
                return level, " ".join(p[1] for p in hit)
        st = self.link.state
        if st.recording:
            return "ok", f"Recording, {_clock(time.time() - (st.record_since or time.time()))}."
        return "idle", "Connected, not recording."

    def alarm(self, now: float) -> None:
        """Raise the operator's chosen alert button once when this computer turns error (or warn)."""
        level, _ = self.health(now)
        if level in ("ok", "idle"):
            self.alerted = ""
            return
        rank = {"": 0, "warn": 1, "error": 2}
        if rank.get(level, 0) <= rank.get(self.alerted, 0):
            return
        self.alerted = level
        bid = self.cfg["alert_error"] if level == "error" else self.cfg["alert_warn"]
        if not bid or alerts.button(bid) is None:
            return
        live = alerts.latest.get(bid)
        if live is not None and live.state == "on":
            return
        with contextlib.suppress(Exception):
            alerts.press(bid, f"REAPER ({self.cfg['name']})")

    def snapshot(self) -> dict:
        st = self.link.state
        level, detail = self.health()
        return {"id": self.id, "name": self.cfg["name"], "reached": self.link.reached(), "health": level,
                "detail": detail, "recording": st.recording, "playing": st.playing, "time": round(st.time, 2),
                "record_since": st.record_since, "marker": st.marker_name, "tracks": self.tracks(),
                "wanted": self.wanted, "auto": self.ours}


class Service:
    def __init__(self) -> None:
        self.machines: dict[str, Machine] = {}
        self._task: asyncio.Task | None = None
        self._last_presentation = ""

    @staticmethod
    def settings() -> dict:
        return clean_settings(config.get().get(KEY))

    def machine(self, mid: Any) -> Machine | None:
        return self.machines.get(mid) if isinstance(mid, str) else None

    async def start(self) -> None:
        await self.stop()
        for cfg in self.settings()["machines"]:
            m = Machine(cfg)
            self.machines[m.id] = m
            await m.start()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._task = None
        for m in self.machines.values():
            await m.stop()
        self.machines = {}

    restart = start

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.exception("REAPER tick failed")
            await asyncio.sleep(TICK_S)

    def tick(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        on_air, window, started = stream_on_air(), schedule_open(), show_stream.running
        for m in self.machines.values():
            m.tick(on_air, window, now, started)
            m.alarm(now)

    # -- acting -------------------------------------------------------------------------------------------------
    def _targets(self, mid: Any = None) -> list[Machine]:
        if mid:
            m = self.machine(mid)
            return [m] if m else []
        return [m for m in self.machines.values() if m.link.reached()]

    def record(self, mid: Any = None) -> list[str]:
        """Start recording; the names of the computers asked. A computer already recording is left alone."""
        done = []
        for m in self._targets(mid):
            if m.link.state.recording or m.link.record():
                m.held, m.unexpected_at = False, 0.0
                done.append(m.cfg["name"])
        return done

    def stop_recording(self, mid: Any = None) -> list[str]:
        done = []
        for m in self._targets(mid):
            if not m.link.state.recording:
                done.append(m.cfg["name"])
                continue
            if m.link.stop():
                m.stopping, m.stop_sent_at = True, time.time()
                m.held = m.wanted               # stopped by hand while the service is live: do not start again
                m.ours = False
                done.append(m.cfg["name"])
        return done

    async def marker(self, name: str, mid: Any = None) -> list[str]:
        done = []
        for m in self._targets(mid):
            if await m.link.marker(name):
                done.append(m.cfg["name"])
        return done

    def action(self, command: int, mid: Any = None) -> list[str]:
        return [m.cfg["name"] for m in self._targets(mid) if m.link.action(command)]

    # -- hooks into core ----------------------------------------------------------------------------------------
    async def control(self, command: str, value: str = "", *, source: str = "") -> dict:
        """core's `control_recording`: record, stop, marker (named by `value`) or action (a REAPER command id), on
        every REAPER computer that answers. A system audit entry says where it came from."""
        command = (command or "").strip().lower()
        if not self.machines:
            return {"ok": False, "error": "No REAPER computer is set up."}
        if command == "record":
            done, what = self.record(), "started recording"
        elif command == "stop":
            done, what = self.stop_recording(), "stopped recording"
        elif command == "marker":
            name = clean_marker(value)
            done, what = await self.marker(name), f"marker {name}" if name else "marker"
        elif command == "action":
            if not (value or "").strip().isdigit() or not 1 <= int(value) <= 99_999_999:
                return {"ok": False, "error": "Give REAPER's action number, like action=40157."}
            done, what = self.action(int(value)), f"ran action {int(value)}"
        else:
            return {"ok": False, "error": "REAPER can record, stop, marker or action."}
        if not done:
            return {"ok": False, "error": "No REAPER computer answered."}
        audit.record(f"reaper.{command}", f"REAPER ({', '.join(done)}): {what}"
                     + (f" (from {source})" if source else ""), target=command)
        return {"ok": True, "done": done}

    def presentation(self, name: str) -> None:
        """A new presentation is up in ProPresenter: a marker named after it on every computer that wants one."""
        name = clean_marker(name)
        if not name or name == self._last_presentation:
            return
        self._last_presentation = name
        for m in self.machines.values():
            if m.cfg["song_markers"] and m.link.reached() and m.link.state.recording:
                asyncio.ensure_future(m.link.marker(name))

    def alert_event(self, data: dict) -> None:
        if data.get("event") != "message.sent":
            return
        name = clean_marker(f"Message: {data.get('text') or ''}")
        for m in self.machines.values():
            if m.cfg["message_markers"] and m.link.reached() and m.link.state.recording:
                asyncio.ensure_future(m.link.marker(name))

    # -- reading ------------------------------------------------------------------------------------------------
    def health(self) -> tuple[str, str]:
        if not self.machines:
            return "idle", "Add a REAPER computer."
        rank = {"idle": 0, "ok": 1, "warn": 2, "error": 3}
        worst, parts = "idle", []
        for m in self.machines.values():
            level, detail = m.health()
            if rank[level] > rank[worst]:
                worst = level
            parts.append(f"{m.cfg['name']}: {detail}" if len(self.machines) > 1 else detail)
        return worst, " ".join(parts)

    def recording(self) -> bool:
        return any(m.link.state.recording for m in self.machines.values())

    def snapshot(self) -> dict:
        s = self.settings()
        s["machines"] = [{**c, "state": self.machines[c["id"]].snapshot() if c["id"] in self.machines else None}
                         for c in s["machines"]]
        s["alert_buttons"] = [{"id": b["id"], "name": b["name"]} for b in alerts.settings().get("buttons") or []
                              if isinstance(b, dict) and b.get("id")]
        s["now"] = time.time()
        s["tracks_max"] = TRACKS
        return s


service = Service()


def attach_hooks() -> None:
    from ... import modules as registry

    registry.provide("reaper.recording", service.control)
    registry.listen("presentation.up", service.presentation)
    if service.alert_event not in alerts.listeners:
        alerts.listeners.append(service.alert_event)


def detach_hooks() -> None:
    from ... import modules as registry

    registry.withdraw("reaper.recording")
    registry.unlisten("presentation.up", service.presentation)
    with contextlib.suppress(ValueError):
        alerts.listeners.remove(service.alert_event)
