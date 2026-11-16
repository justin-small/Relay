"""Settings and one held connection per OBS machine (#250).

Each machine keeps a single obs-websocket connection for everything Relay does with it: following the scene and the
outputs, switching scenes (directly or through studio mode), starting and stopping the stream, recording and replay
buffer, audio meters and mute, the admin preview and the caption sender (#140). It reconnects with a growing wait,
polls the stream, record and stats every POLL_S, publishes its own entry in the shared stream status and, when scenes
are mapped to camera numbers, in the shared tally.

Health, per machine (fixed, not settings):
- error: OBS cannot be reached, or the stream stopped without anyone stopping it (it went from live to stopped
  without passing through "stopping"), shown for UNEXPECTED_S unless the stream starts again;
- warn: the stream is reconnecting, over 1% of frames were dropped (network), skipped while encoding or lagged while
  rendering over the last minute, or a watched audio input marked "warn if muted" is muted while streaming;
- ok: streaming or recording with none of the above;
- idle: connected and neither streaming nor recording.

A machine can raise one of the operator's alert buttons when it turns error, and another when it turns warn: once per
problem, never repeated while it lasts. "Cannot be reached" raises only after the machine was connected in this run,
so a record PC that is switched off all week does not alert at every start.

The stream key is never read into Relay: of OBS's stream settings only the service's name and YouTube's broadcast id
are kept.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import math
import posixpath
import re
import secrets
import time
import uuid
from collections import deque
from typing import Any

from ... import audit, config, languages, tally
from ...alerts import alerts
from ...hub import hub
from ...streamstatus import streams
from .client import DEFAULT_PORT, EVENTS, METERS, Connection, ObsError

KEY = "obs"
MAX_MACHINES = 4
MAX_TALLY = 16
MAX_AUDIO = 8
MAX_INPUTS = 64
POLL_S = 2.0
RETRY_S = (2.0, 4.0, 8.0, 15.0, 30.0)
WINDOW_S = 60.0              # frames are judged over the last minute
DROP_WARN = 0.01             # more than 1% dropped or skipped frames: warn
UNEXPECTED_S = 600.0         # how long an unexpected stop stays an error
CAPTION_LINE = 32            # CEA-608 shows about 32 characters a line
CAPTION_QUEUE = 20
CAPTION_GAP_S = 0.4
CAPTION_DELAY_MAX = 30.0
PREVIEW_WIDTH = 480
PREVIEW_MIN_S = 0.5          # a screenshot is taken at most this often, however many pages ask
HISTORY_S = 15 * 60          # the bitrate history the admin page draws
METER_HOLD_S = 1.0
FLOOR_DB = -60.0
PAUSE_CHECK_S = 0.4
SOURCE_LANGUAGE = "source"
OUTPUTS = {"stream": {"start": "StartStream", "stop": "StopStream"},
           "record": {"start": "StartRecord", "stop": "StopRecord", "pause": "PauseRecord", "resume": "ResumeRecord"},
           "replay": {"start": "StartReplayBuffer", "stop": "StopReplayBuffer", "save": "SaveReplayBuffer"}}

_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-:]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[a-z0-9]{1,8}")
_KEY_RE = re.compile(r"[0-9a-f]{32}")
_BUTTON_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")
_YT_ID_RE = re.compile(r"[A-Za-z0-9_\-]{11}")


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _name(value: Any, limit: int = 100) -> str:
    """An OBS scene or input name as OBS spells it (spaces kept), or "" when it is not usable."""
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if ch.isprintable())
    return value if value.strip() and len(value) <= limit else ""


def new_key() -> str:
    """A machine's scene-screen key: 128 random bits, as 32 hex digits."""
    return secrets.token_hex(16)


def _passwords(raw: Any) -> dict[str, str]:
    """The `passwords` setting: one sealed string holding {machine id: password} as JSON, because config.json seals
    only top-level string fields."""
    if isinstance(raw, dict):
        d = raw
    else:
        try:
            d = json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            d = {}
    return {k: v[:200] for k, v in (d.items() if isinstance(d, dict) else ())
            if isinstance(k, str) and _ID_RE.fullmatch(k) and isinstance(v, str) and v}


def _tally(raw: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    for t in (raw if isinstance(raw, list) else [])[:MAX_TALLY]:
        if not isinstance(t, dict):
            continue
        n, scene = t.get("n"), _name(t.get("scene"))
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 999 or n in seen or not scene:
            continue
        seen.add(n)
        out.append({"n": n, "scene": scene})
    return out


def _audio(raw: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for a in (raw if isinstance(raw, list) else [])[:MAX_AUDIO]:
        if not isinstance(a, dict):
            continue
        name = _name(a.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "warn_muted": a.get("warn_muted") is True})
    return out


def clean_machine(m: Any, seen: set[str]) -> dict | None:
    if not isinstance(m, dict):
        return None
    mid = m.get("id") if isinstance(m.get("id"), str) and _ID_RE.fullmatch(m["id"]) else ""
    if not mid or mid in seen:
        mid = uuid.uuid4().hex[:8]
    seen.add(mid)
    host = m.get("host").strip() if isinstance(m.get("host"), str) else ""
    if not _HOST_RE.fullmatch(host):
        host = ""
    port = m.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = DEFAULT_PORT
    lang = m.get("caption_lang") if isinstance(m.get("caption_lang"), str) else SOURCE_LANGUAGE
    if lang != SOURCE_LANGUAGE and lang not in languages.TARGETS:
        lang = SOURCE_LANGUAGE
    delay = m.get("caption_delay")
    delay = round(float(delay), 1) if isinstance(delay, (int, float)) and not isinstance(delay, bool) \
        and 0 <= delay <= CAPTION_DELAY_MAX else 0.0
    key = m.get("key") if isinstance(m.get("key"), str) and _KEY_RE.fullmatch(m["key"]) else ""

    def button(k: str) -> str:
        v = m.get(k)
        return v if isinstance(v, str) and _BUTTON_RE.fullmatch(v) else ""

    return {"id": mid, "name": _text(m.get("name"), 24) or "OBS", "host": host, "port": port,
            "captions": m.get("captions") is True, "caption_lang": lang, "caption_delay": delay,
            "preview": m.get("preview") is True, "alert_error": button("alert_error"), "alert_warn": button("alert_warn"),
            "tally": _tally(m.get("tally")), "audio": _audio(m.get("audio")),
            "screen_on": bool(key) and m.get("screen_on") is True, "key": key}


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    seen: set[str] = set()
    machines = [c for c in (clean_machine(m, seen) for m in (raw.get("machines") if isinstance(raw.get("machines"), list)
                                                           else [])[:MAX_MACHINES]) if c]
    pw = _passwords(raw.get("passwords"))
    pw = {k: v for k, v in pw.items() if k in seen}
    return {"machines": machines, "passwords": json.dumps(pw, sort_keys=True) if pw else ""}


def public(settings: dict) -> dict:
    s = clean_settings(settings)
    pw = _passwords(s.pop("passwords"))
    for m in s["machines"]:
        m["has_password"] = m["id"] in pw
        m["has_key"] = bool(m.pop("key"))     # a key reaches only its own manage route, never a config read
    return s


def caption_lines(text: str, width: int = CAPTION_LINE) -> list[str]:
    """`text` split at spaces into lines of at most `width` characters; a longer word is cut."""
    out: list[str] = []
    line = ""
    for word in text.split():
        while len(word) > width:
            if line:
                out.append(line)
                line = ""
            out.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        out.append(line)
    return out


def _rate(samples: deque, i: int) -> float:
    """Skipped over total for counter pair `i` between the oldest and newest sample in the window."""
    if len(samples) < 2:
        return 0.0
    a, b = samples[0], samples[-1]
    total = b[i + 1] - a[i + 1]
    return (b[i] - a[i]) / total if total > 0 else 0.0


def _db(mul: float) -> float:
    return max(FLOOR_DB, 20 * math.log10(mul)) if mul > 0 else FLOOR_DB


class Machine:
    """One OBS computer: its connection, what it last said, and its caption queue."""

    def __init__(self, cfg: dict, password: str) -> None:
        self.cfg = cfg
        self.password = password
        self.conn: Connection | None = None
        self.task: asyncio.Task | None = None
        self.state = "connecting"          # connecting | connected | unreachable
        self.ever_connected = False
        self.error = ""
        self.version = ""
        self.scenes: list[str] = []
        self.program = ""
        self.studio = False
        self.preview_scene = ""            # studio mode only
        self.stream: dict = {}
        self.record: dict = {}
        self.stats: dict = {}
        self.replay: dict = {"available": False, "active": False, "last": ""}
        self.service = ""                  # "YouTube - RTMPS", "Twitch", "Custom server"
        self.youtube = ""                  # the broadcast id OBS picked, when it streams to YouTube
        self.inputs: list[str] = []        # every audio input
        self.muted: dict[str, bool] = {}
        self.levels: dict[str, tuple[float, float, float]] = {}   # name -> (dB now, peak dB held, when the peak was)
        self.stream_state = ""
        self.unexpected_at = 0.0
        self.samples: deque = deque()
        self.history: deque = deque()      # (epoch, kbps, congestion %, dropped % this interval)
        self._last_bytes: tuple[float, int, int, int] | None = None   # (monotonic, bytes, skipped, total)
        self.alerted = ""                  # the worst level an alert was raised for in this problem
        self.captions: asyncio.Queue = asyncio.Queue(CAPTION_QUEUE)
        self.captions_sent = 0
        self.caption_error = ""
        self._shot: tuple[float, bytes] = (0.0, b"")
        self._shot_lock = asyncio.Lock()

    @property
    def id(self) -> str:
        return self.cfg["id"]

    @property
    def source(self) -> str:
        return f"obs_{self.id}"

    def watched(self) -> list[str]:
        return [a["name"] for a in self.cfg["audio"]]

    # ------------------------------------------------------------------------------------------ life cycle
    def start(self) -> None:
        if self.cfg["host"] and (self.task is None or self.task.done()):
            self.task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        self.conn = None
        streams.clear(self.source)
        tally.tally.clear(self.source)

    async def _run(self) -> None:
        attempt = 0
        events = EVENTS | (METERS if self.cfg["audio"] else 0)
        while True:
            try:
                async with Connection.open(self.cfg["host"], self.cfg["port"], self.password, self._event,
                                           events) as conn:
                    self.conn = conn
                    attempt = 0
                    await self._first_read(conn)
                    self.state, self.error = "connected", ""
                    self.ever_connected = True
                    self.publish()
                    sender = asyncio.get_running_loop().create_task(self._send_captions(conn))
                    try:
                        while not conn.closed.is_set():
                            await self._poll(conn)
                            self.publish()
                            with contextlib.suppress(asyncio.TimeoutError):
                                await asyncio.wait_for(conn.closed.wait(), POLL_S)
                    finally:
                        sender.cancel()
                        with contextlib.suppress(BaseException):
                            await sender
                self.error = "The connection to OBS closed."
            except asyncio.CancelledError:
                raise
            except ObsError as e:
                self.error = str(e)
            except Exception:
                self.error = "OBS did not answer."
            self.conn = None
            self.state = "unreachable"
            self.samples.clear()
            self._last_bytes = None
            self.publish()
            await asyncio.sleep(RETRY_S[min(attempt, len(RETRY_S) - 1)])
            attempt += 1

    # ------------------------------------------------------------------------------------------ reading
    async def _first_read(self, conn: Connection) -> None:
        v = await conn.request("GetVersion")
        self.version = _text(v.get("obsVersion"), 20)
        await self._read_scenes(conn)
        with contextlib.suppress(ObsError):
            self.studio = bool((await conn.request("GetStudioModeEnabled")).get("studioModeEnabled"))
        await self._read_replay(conn)
        await self._read_service(conn)
        await self._read_inputs(conn)
        await self._poll(conn)

    async def _read_scenes(self, conn: Connection) -> None:
        d = await conn.request("GetSceneList")
        scenes = [s for s in d.get("scenes") or [] if isinstance(s, dict) and isinstance(s.get("sceneName"), str)]
        # OBS gives the top scene the highest index: show them in OBS's own order, top first.
        scenes.sort(key=lambda s: -int(s.get("sceneIndex") or 0))
        self.scenes = [s["sceneName"] for s in scenes][:200]
        self.program = d.get("currentProgramSceneName") or ""
        self.preview_scene = d.get("currentPreviewSceneName") or ""

    async def _read_replay(self, conn: Connection) -> None:
        try:
            d = await conn.request("GetReplayBufferStatus")
            self.replay.update(available=True, active=bool(d.get("outputActive")))
        except ObsError:
            self.replay.update(available=False, active=False)   # not switched on in OBS's output settings

    async def _read_service(self, conn: Connection) -> None:
        """Only the service's name and YouTube's broadcast id: the key and server never leave this function."""
        try:
            d = await conn.request("GetStreamServiceSettings")
        except ObsError:
            return
        kind = d.get("streamServiceType")
        ss = d.get("streamServiceSettings") if isinstance(d.get("streamServiceSettings"), dict) else {}
        name = _text(ss.get("service"), 40) if kind == "rtmp_common" else "Custom server" if kind else ""
        bid = ss.get("broadcast_id")
        self.service = name
        self.youtube = bid if "youtube" in name.lower() and isinstance(bid, str) and _YT_ID_RE.fullmatch(bid) else ""

    async def _read_inputs(self, conn: Connection) -> None:
        """Every input that has audio (GetInputMute answers only for those), and the mute of each."""
        d = await conn.request("GetInputList")
        names = [i.get("inputName") for i in d.get("inputs") or [] if isinstance(i, dict)]
        audio: list[str] = []
        muted: dict[str, bool] = {}
        for n in [n for n in names if isinstance(n, str)][:MAX_INPUTS]:
            try:
                muted[n] = bool((await conn.request("GetInputMute", {"inputName": n})).get("inputMuted"))
                audio.append(n)
            except ObsError:
                continue
        self.inputs, self.muted = audio, muted

    async def _poll(self, conn: Connection) -> None:
        st = await conn.request("GetStreamStatus")
        rec = await conn.request("GetRecordStatus")
        stats = await conn.request("GetStats")
        self.stream, self.record, self.stats = st, rec, stats
        now = time.time()
        while self.history and now - self.history[0][0] > HISTORY_S:
            self.history.popleft()
        if not st.get("outputActive"):
            self.samples.clear()
            self._last_bytes = None
            return
        if self.stream_state in ("", "OBS_WEBSOCKET_OUTPUT_STOPPED"):
            self.stream_state = "OBS_WEBSOCKET_OUTPUT_STARTED"
        mono = time.monotonic()
        nbytes = int(st.get("outputBytes") or 0)
        skipped, total = int(st.get("outputSkippedFrames") or 0), int(st.get("outputTotalFrames") or 0)
        last = self._last_bytes
        if last is not None and mono > last[0] and nbytes >= last[1] and total >= last[3]:
            frames = total - last[3]
            self.history.append((now, round((nbytes - last[1]) * 8 / 1000 / (mono - last[0])),
                                 round(float(st.get("outputCongestion") or 0.0) * 100, 1),
                                 round((skipped - last[2]) * 100 / frames, 2) if frames > 0 else 0.0))
        self._last_bytes = (mono, nbytes, skipped, total)
        row = (mono, skipped, total,
               int(stats.get("renderSkippedFrames") or 0), int(stats.get("renderTotalFrames") or 0),
               int(stats.get("outputSkippedFrames") or 0), int(stats.get("outputTotalFrames") or 0))
        if self.samples and any(row[i] < self.samples[-1][i] for i in range(1, 7)):
            self.samples.clear()             # counters reset: a new stream
        self.samples.append(row)
        while len(self.samples) > 2 and row[0] - self.samples[1][0] >= WINDOW_S:
            self.samples.popleft()

    async def _event(self, kind: str, data: dict) -> None:
        if kind == "InputVolumeMeters":
            self._meters(data)
            return                           # 20 a second: never publish from here
        if kind == "CurrentProgramSceneChanged":
            self.program = data.get("sceneName") or self.program
        elif kind == "CurrentPreviewSceneChanged":
            self.preview_scene = data.get("sceneName") or ""
        elif kind == "StudioModeStateChanged":
            self.studio = bool(data.get("studioModeEnabled"))
            if not self.studio:
                self.preview_scene = ""
            else:
                self._later(self._rescan)
        elif kind in ("SceneListChanged", "SceneCreated", "SceneRemoved", "SceneNameChanged"):
            self._later(self._rescan)
        elif kind in ("InputCreated", "InputRemoved", "InputNameChanged"):
            self._later(self._rescan_inputs)
        elif kind == "InputMuteStateChanged":
            name = data.get("inputName")
            if isinstance(name, str) and name in self.muted:
                self.muted[name] = bool(data.get("inputMuted"))
        elif kind == "StreamStateChanged":
            new = str(data.get("outputState") or "")
            if new == "OBS_WEBSOCKET_OUTPUT_STOPPED" and self.stream_state not in (
                    "OBS_WEBSOCKET_OUTPUT_STOPPING", "OBS_WEBSOCKET_OUTPUT_STOPPED"):
                self.unexpected_at = time.time()
            elif new in ("OBS_WEBSOCKET_OUTPUT_STARTING", "OBS_WEBSOCKET_OUTPUT_STARTED"):
                self.unexpected_at = 0.0
                if new == "OBS_WEBSOCKET_OUTPUT_STARTING":
                    self._later(self._reread_service)   # the broadcast OBS picked may have changed
            self.stream_state = new
            self.stream = {**self.stream, "outputActive": bool(data.get("outputActive"))}
        elif kind == "RecordStateChanged":
            self.record = {**self.record, "outputActive": bool(data.get("outputActive")),
                           "outputPaused": data.get("outputState") == "OBS_WEBSOCKET_OUTPUT_PAUSED"}
        elif kind == "ReplayBufferStateChanged":
            self.replay.update(available=True, active=bool(data.get("outputActive")))
        elif kind == "ReplayBufferSaved":
            path = data.get("savedReplayPath")
            if isinstance(path, str):
                self.replay["last"] = posixpath.basename(path.replace("\\", "/"))[:120]
        self.publish()

    def _meters(self, data: dict) -> None:
        watch = set(self.watched())
        now = time.monotonic()
        for i in data.get("inputs") or []:
            name = i.get("inputName") if isinstance(i, dict) else None
            if name not in watch:
                continue
            peaks = [c[1] for c in i.get("inputLevelsMul") or [] if isinstance(c, list) and len(c) >= 2
                     and isinstance(c[1], (int, float))]
            db = _db(max(peaks)) if peaks else FLOOR_DB
            _, held, at = self.levels.get(name, (FLOOR_DB, FLOOR_DB, 0.0))
            if db >= held or now - at > METER_HOLD_S:
                held, at = db, now
            self.levels[name] = (db, held, at)

    def _later(self, fn) -> None:
        if self.conn is not None:
            asyncio.get_running_loop().create_task(fn())

    async def _rescan(self) -> None:
        conn = self.conn
        if conn is None:
            return
        with contextlib.suppress(Exception):
            await self._read_scenes(conn)
            self.publish()

    async def _rescan_inputs(self) -> None:
        conn = self.conn
        if conn is not None:
            with contextlib.suppress(Exception):
                await self._read_inputs(conn)

    async def _reread_service(self) -> None:
        conn = self.conn
        if conn is not None:
            with contextlib.suppress(Exception):
                await self._read_service(conn)

    # ------------------------------------------------------------------------------------------ health
    def rates(self) -> dict[str, float]:
        return {"network": _rate(self.samples, 1), "render": _rate(self.samples, 3), "encode": _rate(self.samples, 5)}

    def health(self, now: float | None = None) -> tuple[str, str]:
        now = time.time() if now is None else now
        where = self.cfg["name"]
        if not self.cfg["host"]:
            return "warn", f"{where}: no address"
        if self.state != "connected":
            if self.state == "connecting":
                return "idle", f"{where}: connecting"
            return "error", f"{where}: {self.error or 'OBS cannot be reached.'}"
        live = bool(self.stream.get("outputActive"))
        recording = bool(self.record.get("outputActive"))
        if not live and self.unexpected_at and now - self.unexpected_at < UNEXPECTED_S:
            return "error", f"{where}: the stream stopped by itself at {time.strftime('%H:%M', time.localtime(self.unexpected_at))}"
        if live and self.stream.get("outputReconnecting"):
            return "warn", f"{where}: the stream is reconnecting"
        if live:
            muted = [a["name"] for a in self.cfg["audio"] if a["warn_muted"] and self.muted.get(a["name"])]
            if muted:
                return "warn", f"{where}: {', '.join(muted)} {'is' if len(muted) == 1 else 'are'} muted while live"
        if live or recording:
            r = self.rates()
            words = {"network": "dropped (network)", "render": "lagged (rendering)", "encode": "skipped (encoding)"}
            bad = [f"{r[k] * 100:.1f}% of frames {words[k]}" for k in ("network", "encode", "render") if r[k] > DROP_WARN]
            if bad:
                return "warn", f"{where}: " + ", ".join(bad) + " in the last minute"
            return "ok", f"{where}: {'streaming' if live else 'recording'}"
        return "idle", f"{where}: connected, not streaming"

    def publish(self) -> None:
        if not self.cfg["host"]:
            return
        health, detail = self.health()
        live = bool(self.stream.get("outputActive")) if self.state == "connected" else None
        since = None
        if live:
            since = time.time() - int(self.stream.get("outputDuration") or 0) / 1000.0
        try:
            streams.publish(self.source, label=self.cfg["name"], on_air=live, health=health, live_since=since,
                            detail=detail)
        except ValueError:
            pass
        self._publish_tally()
        self._alert(health)

    def _publish_tally(self) -> None:
        if not self.cfg["tally"]:
            tally.tally.clear(self.source)
            return
        if self.state != "connected":
            tally.tally.clear(self.source)   # unknown is not "off air"
            return
        tally.tally.publish(self.source, [
            {"n": t["n"], "name": t["scene"], "program": t["scene"] == self.program,
             "preview": self.studio and t["scene"] == self.preview_scene} for t in self.cfg["tally"]])

    def _alert(self, health: str) -> None:
        """Raise the operator's chosen alert button once when this machine turns error (or warn)."""
        if health in ("ok", "idle"):
            self.alerted = ""
            return
        if health == "error" and self.state == "unreachable" and not self.ever_connected:
            return
        rank = {"": 0, "warn": 1, "error": 2}
        if rank.get(health, 0) <= rank.get(self.alerted, 0):
            return
        self.alerted = health
        bid = self.cfg["alert_error"] if health == "error" else self.cfg["alert_warn"]
        if not bid or alerts.button(bid) is None:
            return
        live = alerts.latest.get(bid)
        if live is not None and live.state == "on":
            return
        try:
            alerts.press(bid, f"OBS ({self.cfg['name']})")
        except Exception:
            pass

    # ------------------------------------------------------------------------------------------ acting
    def _connected(self) -> Connection:
        if self.conn is None:
            raise ObsError("OBS is not connected.")
        return self.conn

    def scene_named(self, name: str) -> str:
        """OBS's own spelling of the scene called `name`, exact first, then any case; "" when there is none."""
        if name in self.scenes:
            return name
        want = " ".join(name.split()).casefold()
        return next((s for s in self.scenes if " ".join(s.split()).casefold() == want), "")

    async def set_scene(self, scene: str) -> None:
        conn = self._connected()
        if scene not in self.scenes:
            raise ObsError("OBS has no scene with that name.")
        await conn.request("SetCurrentProgramScene", {"sceneName": scene})
        self.program = scene
        self.publish()

    async def set_preview(self, scene: str) -> None:
        conn = self._connected()
        if not self.studio:
            raise ObsError("Studio mode is off in OBS.")
        if scene not in self.scenes:
            raise ObsError("OBS has no scene with that name.")
        await conn.request("SetCurrentPreviewScene", {"sceneName": scene})
        self.preview_scene = scene
        self.publish()

    async def take(self) -> None:
        conn = self._connected()
        if not self.studio:
            raise ObsError("Studio mode is off in OBS.")
        await conn.request("TriggerStudioModeTransition")

    async def set_studio(self, on: bool) -> None:
        conn = self._connected()
        await conn.request("SetStudioModeEnabled", {"studioModeEnabled": bool(on)})
        self.studio = bool(on)
        if on:
            await self._read_scenes(conn)
        else:
            self.preview_scene = ""
        self.publish()

    async def output(self, which: str, action: str) -> None:
        """Start, stop, pause or resume an output, or save the replay buffer. Sent once, never retried."""
        req = OUTPUTS.get(which, {}).get(action)
        if req is None:
            raise ObsError("That is not something OBS can do.")
        if which == "replay" and not self.replay["available"]:
            raise ObsError("The replay buffer is off in OBS: switch it on under Settings > Output.")
        conn = self._connected()
        if which == "stream" and action == "stop":
            self.stream_state = "OBS_WEBSOCKET_OUTPUT_STOPPING"   # an operator's stop is not "stopped by itself"
        await conn.request(req)
        if which == "record" and action == "pause":
            # OBS accepts PauseRecord and does nothing when the recording cannot pause (Simple output mode with
            # recording quality "Same as stream"), so look before saying it worked.
            await asyncio.sleep(PAUSE_CHECK_S)
            rec = await conn.request("GetRecordStatus")
            self.record = rec
            if rec.get("outputActive") and not rec.get("outputPaused"):
                raise ObsError("OBS did not pause the recording. In OBS's Simple output mode, set Recording Quality "
                               "to anything but \"Same as stream\", or use Advanced mode.")

    async def set_mute(self, name: str, muted: bool) -> None:
        conn = self._connected()
        if name not in self.inputs:
            raise ObsError("OBS has no audio input with that name.")
        await conn.request("SetInputMute", {"inputName": name, "inputMuted": bool(muted)})
        self.muted[name] = bool(muted)
        self.publish()

    def audio(self) -> list[dict]:
        now = time.monotonic()
        out = []
        for a in self.cfg["audio"]:
            db, held, at = self.levels.get(a["name"], (FLOOR_DB, FLOOR_DB, 0.0))
            out.append({"name": a["name"], "present": a["name"] in self.muted, "muted": bool(self.muted.get(a["name"])),
                        "db": round(db, 1), "peak": round(held if now - at <= METER_HOLD_S * 2 else db, 1)})
        return out

    async def screenshot(self) -> bytes:
        """A small JPEG of the program scene, shared by every page asking within PREVIEW_MIN_S."""
        async with self._shot_lock:
            at, img = self._shot
            if img and time.monotonic() - at < PREVIEW_MIN_S:
                return img
            conn = self._connected()
            if not self.program:
                raise ObsError("OBS is not connected.")
            d = await conn.request("GetSourceScreenshot", {"sourceName": self.program, "imageFormat": "jpg",
                                                           "imageWidth": PREVIEW_WIDTH, "imageCompressionQuality": 60})
            data = d.get("imageData") or ""
            try:
                img = base64.b64decode(data.split(",", 1)[1] if "," in data else data, validate=True)
            except Exception:
                raise ObsError("OBS sent a picture Relay could not read.") from None
            if not img.startswith(b"\xff\xd8"):
                raise ObsError("OBS sent a picture Relay could not read.")
            self._shot = (time.monotonic(), img)
            return img

    # ------------------------------------------------------------------------------------------ captions
    def wants(self, stream: str, lang: str) -> bool:
        if not self.cfg["captions"]:
            return False
        want = self.cfg["caption_lang"]
        if want == SOURCE_LANGUAGE:
            return stream == "transcription"
        return stream == "translation" and lang == want

    def caption_line(self, text: str) -> None:
        """A finished line, held for the caption delay (to line up with a delayed stream) before it is queued."""
        delay = self.cfg.get("caption_delay") or 0.0
        if delay > 0:
            asyncio.get_running_loop().call_later(delay, self.queue_caption, text)
        else:
            self.queue_caption(text)

    def queue_caption(self, text: str) -> None:
        if self.conn is None or not self.stream.get("outputActive"):
            return                          # nothing to caption: OBS refuses captions while not streaming
        while self.captions.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self.captions.get_nowait()   # behind: drop the oldest rather than fall further behind
        self.captions.put_nowait(text)

    async def _send_captions(self, conn: Connection) -> None:
        while True:
            text = await self.captions.get()
            for line in caption_lines(text):
                try:
                    await conn.request("SendStreamCaption", {"captionText": line})
                    self.captions_sent += 1
                    self.caption_error = ""
                except ObsError as e:
                    self.caption_error = str(e)
                await asyncio.sleep(CAPTION_GAP_S)

    # ------------------------------------------------------------------------------------------ admin view
    def snapshot(self) -> dict:
        health, detail = self.health()
        st, rec, stats = self.stream, self.record, self.stats
        r = self.rates()
        yt = self.youtube
        return {
            "id": self.id, "state": self.state, "error": self.error, "health": health, "detail": detail,
            "version": self.version, "scenes": list(self.scenes), "program": self.program,
            "studio": self.studio, "preview_scene": self.preview_scene,
            "stream": {"active": bool(st.get("outputActive")), "reconnecting": bool(st.get("outputReconnecting")),
                       "duration_ms": int(st.get("outputDuration") or 0), "bytes": int(st.get("outputBytes") or 0),
                       "congestion": float(st.get("outputCongestion") or 0.0),
                       "skipped": int(st.get("outputSkippedFrames") or 0),
                       "total": int(st.get("outputTotalFrames") or 0)},
            "record": {"active": bool(rec.get("outputActive")), "paused": bool(rec.get("outputPaused")),
                       "duration_ms": int(rec.get("outputDuration") or 0), "bytes": int(rec.get("outputBytes") or 0)},
            "replay": dict(self.replay),
            "stats": {"cpu": round(float(stats.get("cpuUsage") or 0.0), 1),
                      "memory_mb": round(float(stats.get("memoryUsage") or 0.0)),
                      "fps": round(float(stats.get("activeFps") or 0.0), 1),
                      "render_ms": round(float(stats.get("averageFrameRenderTime") or 0.0), 1),
                      "disk_mb": round(float(stats.get("availableDiskSpace") or 0.0)),
                      "render_skipped": int(stats.get("renderSkippedFrames") or 0),
                      "render_total": int(stats.get("renderTotalFrames") or 0),
                      "encode_skipped": int(stats.get("outputSkippedFrames") or 0),
                      "encode_total": int(stats.get("outputTotalFrames") or 0)},
            "minute": {k: round(v * 100, 2) for k, v in r.items()},
            "history": [list(h) for h in self.history],
            "service": self.service,
            "youtube": {"id": yt, "watch": f"https://www.youtube.com/watch?v={yt}",
                        "studio": f"https://studio.youtube.com/video/{yt}/livestreaming"} if yt else None,
            "inputs": list(self.inputs),
            "captions_sent": self.captions_sent, "caption_error": self.caption_error,
        }


class Service:
    def __init__(self) -> None:
        self.machines: dict[str, Machine] = {}
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def settings(self) -> dict:
        return clean_settings(config.get().get(KEY))

    async def start(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()
        await self.restart()

    async def stop(self) -> None:
        self._running = False
        await self._close_all()

    async def _close_all(self) -> None:
        old, self.machines = self.machines, {}
        for m in old.values():
            await m.stop()

    async def restart(self) -> None:
        await self._close_all()
        if not self._running:
            return
        self._loop = asyncio.get_running_loop()
        s = self.settings()
        pw = _passwords(s["passwords"])
        for cfg in s["machines"]:
            m = Machine(cfg, pw.get(cfg["id"], ""))
            self.machines[cfg["id"]] = m
            m.start()

    def machine(self, mid: Any) -> Machine | None:
        return self.machines.get(mid) if isinstance(mid, str) else None

    def screen_machine(self, mid: str, key: str) -> Machine | None:
        """The machine whose scene-screen key is `key`, compared in constant time, only while its screen is on."""
        m = self.machine(mid)
        if m is None:
            return None
        ok = m.cfg["screen_on"] and bool(m.cfg["key"]) and hmac.compare_digest(m.cfg["key"].encode(), str(key).encode())
        return m if ok else None

    # The caption sink core calls with every committed line (hub.line_sinks). It may run off the event loop.
    def line_sink(self, stream: str, lang: str, seq: int, text: str, started: float, ended: float) -> None:
        loop = self._loop
        if loop is None or not text:
            return
        for m in list(self.machines.values()):
            if m.wants(stream, lang):
                loop.call_soon_threadsafe(m.caption_line, text)

    async def switch_scene(self, name: str, *, source: str = "") -> dict:
        """The hook behind core's `switch_scene`: every connected computer that has the scene switches to it."""
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "Name the scene."}
        found = [(m, m.scene_named(name)) for m in self.machines.values() if m.conn is not None]
        found = [(m, s) for m, s in found if s]
        if not found:
            return {"ok": False, "error": f"No connected OBS computer has a scene called \"{_text(name, 60)}\"."}
        failed = []
        for m, scene in found:
            try:
                await m.set_scene(scene)
                audit.record("obs.scene", f"OBS ({m.cfg['name']}): switched to {scene[:60]}"
                             + (f" (from {source})" if source else ""), target=m.id)
            except ObsError:
                failed.append(m.cfg["name"])
        if failed:
            return {"ok": False, "error": f"OBS did not switch on {', '.join(failed)}."}
        return {"ok": True}

    def health(self) -> tuple[str, str]:
        if not self.machines:
            return "warn", "Not set up: add an OBS computer"
        order = {"error": 0, "warn": 1, "ok": 2, "idle": 3}
        return min((m.health() for m in self.machines.values()), key=lambda h: order[h[0]])

    def snapshot(self) -> dict:
        out = public(self.settings())
        out["languages"] = [{"id": SOURCE_LANGUAGE, "label": "Source language (the captions)"}] + [
            {"id": t, "label": languages.target_label(t)} for t in languages.target_names(config.get().get("source_language"))]
        out["alert_buttons"] = [{"id": b["id"], "name": b["name"]} for b in alerts.settings().get("buttons") or []
                                if isinstance(b, dict) and isinstance(b.get("id"), str)]
        live = {mid: m.snapshot() for mid, m in self.machines.items()}
        for m in out["machines"]:
            m["live"] = live.get(m["id"])
        return out


service = Service()


def attach_hooks() -> None:
    from ... import modules as registry

    hub.add_line_sink(service.line_sink)
    registry.scene_hook = service.switch_scene


def detach_hooks() -> None:
    from ... import modules as registry

    hub.remove_line_sink(service.line_sink)
    registry.scene_hook = None
