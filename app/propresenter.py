"""ProPresenter: follows the slide that is up, feeds the lyrics to screens and panels, and can put a short message
on ProPresenter's stage display.

Three pieces, none of which ProPresenter has to know about:

- `Client`: the ONE place Relay talks HTTP to ProPresenter. Every read and every write goes through it, which is
  what makes view-only mode a guarantee (see `Client`): reads are allowed, every write is refused there, whoever
  calls. Standard library only, like planningcenter.py (httpx is not in requirements.txt).
- `Follower`: a background task holding one `POST /v1/status/updates` connection with a FIXED list of paths.
  What a live ProPresenter 22 does that the published description does not say is written up in
  docs/reference/propresenter-live-test.md; the code follows that file:
    * messages are `{"url": ..., "data": ...}` separated by a blank line (`\\r\\n\\r\\n`), not by HTTP chunks;
    * the stream MISSES slide changes, so a slide, slide-index or presentation message is only a signal and
      `GET /v1/status/slide` is re-read; a result with the same text is ignored;
    * no presentation means no lyrics, whatever status/slide still says;
    * `timer/system_time` arrives every 1-3 s unasked and is the heartbeat; when it stops the connection is
      dropped and reopened with backoff;
    * one unknown path fails the whole request (400), so the list is never built from outside input;
    * ProPresenter can stall for minutes, so every request has a short timeout.
- `LyricsFeed`: what the follower knows, as `/stream?stream=lyrics` messages (see `LyricsFeed`).

Relay only ever talks out to the address the operator typed; ProPresenter's API has no authentication.
Nothing is sent until the operator has switched the integration on and entered an address.
"""
from __future__ import annotations

import asyncio
import http.client
import ipaddress
import json
import logging
import re
import socket
import struct
import threading
import time
import urllib.parse
from typing import Any, Callable

from . import config
from .hub import sse_frame

log = logging.getLogger("relay.propresenter")

# What the stream is asked for. Fixed: one path ProPresenter does not know fails the whole request.
STREAM_PATHS = ["status/slide", "presentation/current", "presentation/slide_index", "timers/current",
                "stage/message", "status/layers", "playlist/active", "look/current"]
HEARTBEAT_PATH = "timer/system_time"

REQUEST_TIMEOUT_S = 3.0
RESYNC_S = 1.0           # the stream misses slide changes, so the slide position is also read this often
STALL_S = 10.0            # no heartbeat for this long: the connection is dropped and reopened
BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0)
MISSES_FOR_DOWN = 2       # one miss is noise; two in a row is "down"
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
STAGE_MESSAGE_MAX = 200
QUEUE_MAX = 16
SONG_GROUPS = re.compile(r"^\s*(verse|chorus|pre[- ]?chorus|bridge|tag|refrain|intro|outro|interlude|ending|vamp)\b",
                         re.I)
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$")

AUDIENCES = ("viewer", "screen", "overlay", "panel")


# ----------------------------------------------------------------------------------------------- settings
def default_settings() -> dict[str, Any]:
    return {"enabled": False, "host": "", "port": 0, "view_only": False,
            "songs_library": "Songs", "songs_only": True,
            # Where the lyrics feed may be read. Song lyrics are licensed: the public phone viewer is off.
            "lyrics": {"viewer": False, "screen": True, "overlay": False, "panels": True}}


def clean_host(value: object) -> str:
    """A bare host name or address; "" when it is anything else (a URL, a path, a space)."""
    host = str(value or "").strip()
    return host if host and HOST_RE.match(host) else ""


def clean_port(value: object) -> int:
    try:
        port = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return port if 1 <= port <= 65535 else 0


def clean_stored(raw: object) -> dict[str, Any]:
    """The saved settings, anything malformed dropped rather than refused so a hand edit cannot stop Relay."""
    base = default_settings()
    raw = raw if isinstance(raw, dict) else {}
    lyr = raw.get("lyrics") if isinstance(raw.get("lyrics"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "host": clean_host(raw.get("host")),
        "port": clean_port(raw.get("port")),
        "view_only": bool(raw.get("view_only", False)),
        "songs_library": " ".join(str(raw.get("songs_library") or base["songs_library"]).split())[:64]
        or base["songs_library"],
        "songs_only": bool(raw.get("songs_only", True)),
        "lyrics": {k: bool(lyr.get(k, v)) for k, v in base["lyrics"].items()},
    }


def settings() -> dict[str, Any]:
    return config.get().get("propresenter") or default_settings()


def configured(cfg: dict | None = None) -> bool:
    s = (cfg or config.get()).get("propresenter") or {}
    return bool(s.get("enabled") and s.get("host") and s.get("port"))


# ---------------------------------------------------------------------------------------------- the client
class ProError(Exception):
    """kind: network (no answer), timeout, http (an error status), view_only (a write refused), bad (an answer
    Relay cannot read)."""

    def __init__(self, kind: str, message: str, status: int = 0):
        super().__init__(message)
        self.kind, self.status = kind, status


class ViewOnly(ProError):
    def __init__(self, what: str):
        super().__init__("view_only", f"View-only mode is on, so Relay did not {what}.")


# Path segments that make a GET do something. ProPresenter triggers, clears and starts timers with GET, so
# "it is a GET" does not mean "it only reads": read() refuses these, and they go through action().
_ACTION_SEGMENTS = frozenset({
    "trigger", "clear", "start", "stop", "reset", "pause", "unpause", "focus", "next", "previous",
    "find_my_mouse", "go_to_time", "increment", "play", "toggle", "activate"})


def is_action_path(path: str) -> bool:
    segs = [s for s in path.split("?", 1)[0].strip("/").split("/") if s]
    return any(s in _ACTION_SEGMENTS for s in segs)


def resolve(host: str, port: int) -> str:
    """The address to connect to, looked up once per connect. A Bonjour name can cost ~200 ms per request, so
    the answer is pinned and the Host header keeps the name."""
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host.strip("[]")
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProError("network", f"Could not find {host}: {exc.strerror or exc}")
    infos.sort(key=lambda i: i[0] != socket.AF_INET)   # IPv4 first: ProPresenter listens on it
    return infos[0][4][0]


class Client:
    """The only way Relay talks to ProPresenter.

        read(path)                  GET; refused for a path that acts (trigger, clear, start...)
        action(path)                GET that acts: trigger/next, clear/layer/slide ...      a WRITE
        put(path, body)             PUT   (stage/message takes a bare JSON string)          a WRITE
        post(path, body)            POST                                                    a WRITE
        delete(path)                DELETE                                                  a WRITE

    In view-only mode every write raises ViewOnly here, before any socket is opened, so no caller (stage 2 adds
    many) can slip past it. The mode is read on every call, so switching it takes effect at once.

    Calls block: run them with `await follower.call(fn, ...)` from async code.
    """

    def __init__(self, host: str, port: int, view_only: Callable[[], bool] | bool = False):
        self.host, self.port = host, port
        self._view_only = view_only if callable(view_only) else (lambda: bool(view_only))
        self.ip: str | None = None

    @property
    def view_only(self) -> bool:
        return bool(self._view_only())

    def pin(self) -> str:
        """Look the host up again and keep the answer; called once per (re)connect."""
        self.ip = resolve(self.host, self.port)
        return self.ip

    def _send(self, method: str, path: str, body: Any = None, *, timeout: float = REQUEST_TIMEOUT_S) -> Any:
        """The single place a request leaves Relay."""
        url = "/" + path.lstrip("/")
        conn = None
        try:
            conn = http.client.HTTPConnection(self.ip or self.pin(), self.port, timeout=timeout)
            conn.putrequest(method, url, skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"{self.host}:{self.port}")
            conn.putheader("Accept", "application/json")
            data = b""
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            resp = conn.getresponse()
            raw = resp.read(MAX_BODY_BYTES + 1)
        except socket.timeout as exc:
            self.ip = None
            raise ProError("timeout", "ProPresenter did not answer in time.") from exc
        except (OSError, http.client.HTTPException) as exc:
            self.ip = None   # look the name up again next time
            raise ProError("network", f"Could not reach ProPresenter at {self.host}:{self.port} "
                                      f"({getattr(exc, 'strerror', None) or exc}).") from exc
        finally:
            if conn is not None:
                conn.close()
        if len(raw) > MAX_BODY_BYTES:
            raise ProError("bad", "ProPresenter sent a reply that is too large.")
        if resp.status == 404:
            raise ProError("http", "ProPresenter said not found: is Network switched on in its preferences?", 404)
        if resp.status >= 400:
            text = raw.decode("utf-8", "replace").strip()[:200]
            raise ProError("http", f"ProPresenter refused the request ({resp.status}) {text}".strip(), resp.status)
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _write(self, what: str, method: str, path: str, body: Any = None) -> Any:
        if self.view_only:
            raise ViewOnly(what)
        return self._send(method, path, body)

    # -- reads ------------------------------------------------------------------------------------------
    def read(self, path: str, timeout: float = REQUEST_TIMEOUT_S) -> Any:
        if is_action_path(path):
            raise ValueError(f"{path} acts on ProPresenter; use action()")
        return self._send("GET", path, timeout=timeout)

    def version(self) -> dict:
        """`GET /version`: unversioned, answers whenever Network is on."""
        data = self._send("GET", "/version")
        if not isinstance(data, dict):
            raise ProError("bad", "That address answered, but not like ProPresenter.")
        return data

    # -- writes (all refused in view-only mode) ---------------------------------------------------------
    def action(self, path: str) -> Any:
        return self._write(f"send {path}", "GET", path)

    def put(self, path: str, body: Any) -> Any:
        return self._write(f"change {path}", "PUT", path, body)

    def post(self, path: str, body: Any = None) -> Any:
        return self._write(f"send {path}", "POST", path, body)

    def delete(self, path: str) -> Any:
        return self._write(f"delete {path}", "DELETE", path)

    # -- the live stream (a read: it asks ProPresenter to tell, and changes nothing) -------------------
    def open_stream(self, paths: list[str]) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        conn = None
        try:
            conn = http.client.HTTPConnection(self.pin(), self.port, timeout=STALL_S + 5)
            data = json.dumps(paths).encode()
            conn.putrequest("POST", "/v1/status/updates", skip_host=True, skip_accept_encoding=True)
            conn.putheader("Host", f"{self.host}:{self.port}")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data)
            resp = conn.getresponse()
            if resp.status != 200:
                text = resp.read(300).decode("utf-8", "replace")
                raise ProError("http", f"ProPresenter refused the live connection ({resp.status}) {text}",
                               resp.status)
            return conn, resp
        except ProError:
            if conn is not None:
                conn.close()
            raise
        except socket.timeout as exc:
            if conn is not None:
                conn.close()
            raise ProError("timeout", "ProPresenter did not answer in time.") from exc
        except (OSError, http.client.HTTPException) as exc:
            if conn is not None:
                conn.close()
            raise ProError("network", f"Could not reach ProPresenter at {self.host}:{self.port} "
                                      f"({getattr(exc, 'strerror', None) or exc}).") from exc


class Splitter:
    """Cuts the byte stream into messages at the blank line. HTTP chunk boundaries mean nothing: one message can
    span several chunks and one chunk can hold several messages."""

    SEP = b"\r\n\r\n"

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> list[dict]:
        self.buf += data
        out = []
        while True:
            at = self.buf.find(self.SEP)
            if at < 0:
                break
            piece, self.buf = self.buf[:at], self.buf[at + len(self.SEP):]
            if not piece.strip():
                continue
            try:
                msg = json.loads(piece)
            except ValueError:
                log.debug("ProPresenter sent a message that is not JSON; skipped")
                continue
            if isinstance(msg, dict) and isinstance(msg.get("url"), str):
                out.append(msg)
        if len(self.buf) > MAX_MESSAGE_BYTES:
            raise ProError("bad", "ProPresenter sent a message that never ended.")
        return out


def _read_stream(resp: http.client.HTTPResponse, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                 stop: threading.Event) -> None:
    """Thread body: read the live connection and hand each message to the loop. Ends with None (EOF), or with
    the exception that ended it."""
    split = Splitter()
    end: Any = None
    try:
        while not stop.is_set():
            chunk = resp.read1(65536)
            if not chunk:
                break
            for msg in split.feed(chunk):
                loop.call_soon_threadsafe(queue.put_nowait, msg)
    except Exception as exc:   # a timeout, a reset, a close from the other side
        end = exc
    if not stop.is_set():
        try:
            loop.call_soon_threadsafe(queue.put_nowait, end)
        except RuntimeError:
            pass   # the loop is gone


# ------------------------------------------------------------------------------------------------ the follower
def _slide_side(side: Any) -> dict:
    side = side if isinstance(side, dict) else {}
    return {"text": str(side.get("text") or "").strip("\r\n"), "notes": str(side.get("notes") or ""),
            "uuid": str(side.get("uuid") or "")}


class Follower:
    def __init__(self) -> None:
        self.rev = 0
        self.listeners: list[Callable[[], None]] = []
        self.client: Client | None = None
        self.up = False                 # announced state: two misses flip it to False, one answer to True
        self.connecting = False
        self.misses = 0
        self.error = ""
        self.version: dict | None = None
        self.presentation: dict | None = None   # {uuid, name, path, library, groups: [(name, count)]}
        self.slide_index: int | None = None
        self.current = _slide_side(None)
        self.next = _slide_side(None)
        self.data: dict[str, Any] = {}          # the last body of the other stream paths
        self.stage_message = ""
        self.stage_owner: str | None = None     # the alert whose text is on the stage display
        self._announced_down = False
        self._sig = ""
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._stop: threading.Event | None = None
        self._conn_key: tuple | None = None
        self._tasks: set[asyncio.Task] = set()
        self._warned_view_only = False
        self._pending_slide = False
        self._pending_presentation = False
        self._presentation_hint: Any = None
        self.make_client: Callable[..., Client] = Client   # a test swaps this

    # -- lifecycle ---------------------------------------------------------------------------------------
    def start(self) -> None:
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
        self._hang_up()

    async def poke(self) -> None:
        """The settings changed: reconnect now (a new address) or go quiet (switched off)."""
        if self._wake is not None:
            self._wake.set()
        await asyncio.sleep(0)

    def _hang_up(self) -> None:
        stop, self._stop = self._stop, None
        if stop is not None:
            stop.set()

    @staticmethod
    async def call(fn, *args):
        """Run one blocking client call off the event loop."""
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    @staticmethod
    def _key(s: dict) -> tuple:
        return (bool(s["enabled"]), s["host"], s["port"])

    async def _run(self) -> None:
        attempt = 0
        while True:
            try:
                if not configured():
                    self._reset_state()
                    attempt = 0
                    await self._sleep(1.0)
                    continue
                s = settings()
                self._conn_key = self._key(s)
                self.client = self.make_client(s["host"], s["port"], lambda: bool(settings().get("view_only")))
                self.connecting = True
                self._note_change()
                try:
                    await self._session(self.client)
                    attempt = 0
                except ProError as exc:
                    self._miss(str(exc))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("ProPresenter follow failed")
                    self._miss(f"Unexpected error: {exc}")
                self._hang_up()
                if configured() and self._key(settings()) == self._conn_key:
                    await self._sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
                    attempt += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ProPresenter follow loop failed")
                await asyncio.sleep(2.0)

    async def _sleep(self, seconds: float) -> None:
        wake = self._wake
        if wake is None:
            await asyncio.sleep(seconds)
            return
        wake.clear()
        try:
            await asyncio.wait_for(wake.wait(), seconds)
        except asyncio.TimeoutError:
            pass

    # -- one connection ------------------------------------------------------------------------------------
    async def _session(self, client: Client) -> None:
        """Hold the live connection until it drops, stalls, or the settings change."""
        loop = asyncio.get_running_loop()
        conn, resp = await self.call(client.open_stream, STREAM_PATHS)
        stop = self._stop = threading.Event()
        queue: asyncio.Queue = asyncio.Queue()
        threading.Thread(target=_read_stream, args=(resp, loop, queue, stop), daemon=True,
                         name="propresenter-stream").start()
        last_beat = last_sync = time.monotonic()
        first = True
        try:
            if self.version is None:
                try:
                    self.version = await self.call(client.version)
                except ProError:
                    pass
            while True:
                if not configured() or self._key(settings()) != self._conn_key:
                    return
                try:
                    msg = await asyncio.wait_for(queue.get(), 1.0)
                except asyncio.TimeoutError:
                    msg = "tick"
                if msg is None:
                    raise ProError("network", "ProPresenter closed the live connection.")
                if isinstance(msg, BaseException):
                    if isinstance(msg, socket.timeout):
                        raise ProError("timeout", "ProPresenter stopped sending updates.")
                    if isinstance(msg, ProError):
                        raise msg
                    raise ProError("network", f"The live connection to ProPresenter ended ({msg}).")
                if msg != "tick":
                    if first:
                        first = False
                        self._answered()
                    batch = [msg]
                    # Several messages arrive together on connect: look at the slide once they are all in.
                    while not queue.empty():
                        batch.append(queue.get_nowait())
                    for item in batch:
                        if not isinstance(item, dict):
                            queue.put_nowait(item)   # the end of the stream: handled on the next turn
                            continue
                        if item["url"].strip("/") == HEARTBEAT_PATH:
                            last_beat = time.monotonic()
                        self._handle(item)
                    await self._flush_signals(client)
                if time.monotonic() - last_beat > STALL_S:
                    raise ProError("timeout", "ProPresenter stopped sending its heartbeat.")
                if time.monotonic() - last_sync >= RESYNC_S and not first:
                    last_sync = time.monotonic()
                    await self._resync(client)
        finally:
            stop.set()
            if self._stop is stop:
                self._stop = None
            for closer in (lambda: conn.sock.shutdown(socket.SHUT_RDWR), conn.close):
                try:
                    closer()
                except Exception:
                    pass

    def _handle(self, msg: dict) -> None:
        url = msg["url"].strip("/")
        if url.startswith("v1/"):
            url = url[3:]
        data = msg.get("data")
        if url not in STREAM_PATHS:
            return   # unrequested paths (the heartbeat is one) are ignored
        if url == "stage/message":
            self.stage_message = data if isinstance(data, str) else ""
            if not self.stage_message:
                self.stage_owner = None
            self._note_change()
        elif url == "presentation/current":
            self._pending_presentation, self._pending_slide = True, True
            self._presentation_hint = data
        elif url == "presentation/slide_index":
            idx = data.get("presentation_index") if isinstance(data, dict) else None
            idx = idx.get("index") if isinstance(idx, dict) else None
            self.slide_index = idx if isinstance(idx, int) else None
            self._pending_slide = True
        elif url == "status/slide":
            self._pending_slide = True
        else:
            self.data[url] = data
            self._note_change()

    async def _flush_signals(self, client: Client) -> None:
        """React to the signals gathered: read the presentation if it changed, then the slide. The stream misses
        slide changes, so the slide is always read from `GET /v1/status/slide`, never taken from the push."""
        if self._pending_presentation:
            self._pending_presentation = False
            await self._read_presentation(client, self._presentation_hint)
        if self._pending_slide:
            self._pending_slide = False
            await self._read_slide(client)

    async def _resync(self, client: Client) -> None:
        """A live ProPresenter does not push every slide change (sometimes none at all), so every second the
        slide position is read as well: two small GETs, a few milliseconds. The presentation is read again only
        when the position says a different one is up (or none)."""
        try:
            body = await self.call(client.read, "/v1/presentation/slide_index")
        except ProError as exc:
            log.debug("Resync failed: %s", exc)
            return
        idx = body.get("presentation_index") if isinstance(body, dict) else None
        uuid = ((idx or {}).get("presentation_id") or {}).get("uuid") if isinstance(idx, dict) else None
        known = self.presentation["uuid"] if self.presentation else None
        self.slide_index = idx.get("index") if isinstance(idx, dict) and isinstance(idx.get("index"), int) else None
        if (uuid or None) != known:
            try:
                data = await self.call(client.read, "/v1/presentation/current")
            except ProError as exc:
                log.debug("Resync failed: %s", exc)
                return
            await self._read_presentation(client, data)
        await self._read_slide(client)
        self._note_change()

    async def _read_presentation(self, client: Client, data: Any) -> None:
        pres = data.get("presentation") if isinstance(data, dict) else None
        pid = pres.get("id") if isinstance(pres, dict) else None
        uuid = pid.get("uuid") if isinstance(pid, dict) else None
        if not uuid:
            if self.presentation is not None:
                self.presentation = None
                self.slide_index = None
                self.current, self.next = _slide_side(None), _slide_side(None)
            self._note_change()
            return
        if self.presentation and self.presentation["uuid"] == uuid:
            return
        name = str(pid.get("name") or "").strip()
        self.presentation = self._describe(uuid, name, None)
        try:
            detail = await self.call(client.read, f"/v1/presentation/{urllib.parse.quote(uuid)}")
            self.presentation = self._describe(uuid, name, (detail or {}).get("presentation"))
        except ProError as exc:
            # The slide text still works without the library and group names; the song test says "not a song"
            # until they are known.
            log.info("Could not read the presentation's details: %s", exc)
        self._note_change()

    @staticmethod
    def _describe(uuid: str, name: str, detail: dict | None) -> dict:
        detail = detail if isinstance(detail, dict) else {}
        path = str(detail.get("presentation_path") or "")
        m = re.search(r"/Libraries/([^/]+)/", path.replace("\\", "/"))
        groups = [(str(g.get("name") or "").strip(), len(g.get("slides") or []))
                  for g in (detail.get("groups") or []) if isinstance(g, dict)]
        pid = detail.get("id") if isinstance(detail.get("id"), dict) else {}
        # Names can end in a space ("All The Way To Calvary ").
        return {"uuid": uuid, "name": str(pid.get("name") or name).strip(), "path": path,
                "library": m.group(1).strip() if m else "", "groups": groups}

    def is_song(self) -> bool:
        pres = self.presentation
        if not pres:
            return False
        lib = settings().get("songs_library") or ""
        if lib and pres["library"].lower() == lib.lower():
            return True
        return any(SONG_GROUPS.match(n) for n, _ in pres["groups"])

    def group_name(self) -> str:
        """The part (Verse 1, Chorus) the current slide is in, when the groups are known."""
        pres, idx = self.presentation, self.slide_index
        if not pres or idx is None:
            return ""
        seen = 0
        for name, count in pres["groups"]:
            if idx < seen + count:
                return name
            seen += count
        return ""

    async def _read_slide(self, client: Client) -> None:
        try:
            body = await self.call(client.read, "/v1/status/slide")
        except ProError as exc:
            log.info("Could not read the slide: %s", exc)
            return
        body = body if isinstance(body, dict) else {}
        cur, nxt = _slide_side(body.get("current")), _slide_side(body.get("next"))
        if (cur, nxt) == (self.current, self.next):
            return   # the same text pushed twice, or a push that changed nothing
        self.current, self.next = cur, nxt
        self._note_change()

    # -- health ---------------------------------------------------------------------------------------------
    def _answered(self) -> None:
        """First message on a connection: ProPresenter is there."""
        self.connecting = False
        self.misses = 0
        self.error = ""
        if not self.up:
            self.up = True
            if self._announced_down:
                log.info("ProPresenter is back")
            else:
                log.info("ProPresenter connected")
            self._announced_down = False
        self._note_change()

    def _miss(self, why: str) -> None:
        """A failed connect or a stall. One is noise; the second in a row marks ProPresenter down, announced
        once, until it answers again."""
        self.misses += 1
        self.error = why
        if self.misses >= MISSES_FOR_DOWN:
            if not self._announced_down:
                log.warning("ProPresenter is not answering: %s", why)
                self._announced_down = True
            self.up = False
            self.connecting = False
            self.presentation, self.slide_index = None, None
            self.current, self.next = _slide_side(None), _slide_side(None)
        self._note_change()

    def _reset_state(self) -> None:
        had = self.up or self.misses or self.client is not None
        self.up = self.connecting = False
        self.misses = 0
        self.error = ""
        self.client = None
        self.version = None
        self._announced_down = False
        self.presentation, self.slide_index = None, None
        self.current, self.next = _slide_side(None), _slide_side(None)
        self.stage_message, self.stage_owner = "", None
        self.data = {}
        if had:
            self._hang_up()
            self._note_change()

    # -- what is on screen -----------------------------------------------------------------------------------
    def lyrics(self) -> dict:
        """What the lyrics feed shows now. Empty with no presentation, whatever status/slide still says."""
        s = settings()
        song = self.is_song()
        showing = bool(self.up and self.presentation and (song or not s["songs_only"]))
        return {
            "live": showing,
            "song": song,
            "title": self.presentation["name"] if showing else "",
            "part": self.group_name() if showing else "",
            "index": self.slide_index if showing else None,
            "text": self.current["text"] if showing else "",
            "next": self.next["text"] if showing else "",
        }

    def state(self) -> dict:
        """For the admin page."""
        s = settings()
        pres = self.presentation
        look = self.data.get("look/current")
        return {
            "configured": configured(),
            "connected": self.up,
            "connecting": self.connecting and not self.up,
            "error": self.error if not self.up else "",
            "view_only": bool(s["view_only"]),
            "version": self.version,
            "presentation": {"name": pres["name"], "library": pres["library"], "song": self.is_song(),
                             "part": self.group_name()} if pres else None,
            "slide_index": self.slide_index,
            "current": {"text": self.current["text"], "notes": self.current["notes"]},
            "next": {"text": self.next["text"], "notes": self.next["notes"]},
            "stage_message": self.stage_message,
            "layers": self.data.get("status/layers"),
            "look": ((look.get("id") or {}).get("name") if isinstance(look, dict) else None),
            "timers": self.data.get("timers/current") or [],
        }

    def _note_change(self) -> None:
        sig = json.dumps([self.up, self.connecting, self.error, self.presentation, self.slide_index,
                          self.current, self.next, self.stage_message, self.data, self.lyrics()],
                         sort_keys=True, default=str)
        if sig != self._sig:
            self._sig = sig
            self.rev += 1
            for fn in list(self.listeners):
                try:
                    fn()
                except Exception:
                    log.exception("ProPresenter listener failed")

    # -- stage message -----------------------------------------------------------------------------------------
    @staticmethod
    def clean_message(text: object) -> str:
        return " ".join(str(text or "").split())[:STAGE_MESSAGE_MAX]

    def _need_client(self) -> Client:
        if self.client is None or not configured():
            raise ProError("network", "ProPresenter is not set up.")
        return self.client

    async def set_stage_message(self, text: str, owner: str | None = None) -> None:
        """`PUT /v1/stage/message` takes a bare JSON string. Raises ViewOnly in view-only mode."""
        text = self.clean_message(text)
        if not text:
            return await self.clear_stage_message()
        await self.call(self._need_client().put, "/v1/stage/message", text)
        self.stage_message, self.stage_owner = text, owner
        self._note_change()

    async def clear_stage_message(self) -> None:
        await self.call(self._need_client().delete, "/v1/stage/message")
        self.stage_message, self.stage_owner = "", None
        self._note_change()

    # -- Alerts (#145): an alert may also show on the stage display and goes away when it ends ----------------
    def alert_started(self, alert_id: str, text: str) -> None:
        self._spawn(self._alert_stage(alert_id, text))

    def alert_ended(self, alert_id: str) -> None:
        self._spawn(self._alert_stage(alert_id, None))

    def _spawn(self, coro) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:
            coro.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _alert_stage(self, alert_id: str, text: str | None) -> None:
        if not configured():
            return
        try:
            if text is not None:
                await self.set_stage_message(text, owner=alert_id)
            elif self.stage_owner == alert_id:
                # Only the alert that wrote the message takes it down: a newer alert, or the operator's own
                # message, is not wiped by an older alert ending.
                await self.clear_stage_message()
        except ViewOnly:
            if not self._warned_view_only:
                self._warned_view_only = True
                log.info("An alert asked for the ProPresenter stage display, but view-only mode is on")
        except ProError as exc:
            log.warning("Could not update the ProPresenter stage display for an alert: %s", exc)


follower = Follower()


# ----------------------------------------------------------------------------------------------- lyrics feed
class LyricsFeed:
    """`GET /stream?stream=lyrics&for=<viewer|screen|overlay|panel>`: Server-Sent Events, the whole state in each
    message, so a reader that reconnects needs nothing else. Sent when something changes, and by main every
    heartbeat. Whether an audience may read it is the operator's switch (`propresenter.lyrics`); the viewer
    port has no login, so the switch decides what Relay offers, not who may ask. `viewer` is off by default.

        {"type": "lyrics", "enabled": true, "live": true, "song": true, "title": "Great Is Thy Faithfulness",
         "part": "Verse 1", "index": 3, "text": "line one\\nline two", "next": "...", "rev": 12,
         "server_time": 1760000000.0}

    `live` is false (and text empty) when no presentation is up, when ProPresenter is unreachable, or when the
    presentation is not a song and "songs only" is on. `enabled` is false when the operator has switched this
    audience off: the message then carries nothing else. Lines in `text` and `next` are separated by "\\n".
    """

    def __init__(self, source: Follower):
        self.source = source
        self.subs: dict[str, set[asyncio.Queue]] = {a: set() for a in AUDIENCES}
        self._sig: dict[str, str] = {}
        source.listeners.append(self.refresh)

    @staticmethod
    def allowed(audience: str) -> bool:
        if not configured():
            return False
        key = "panels" if audience == "panel" else audience
        return bool(settings()["lyrics"].get(key))

    def payload(self, audience: str) -> dict:
        now = time.time()
        if not self.allowed(audience):
            return {"type": "lyrics", "enabled": False, "live": False, "server_time": now}
        return {"type": "lyrics", "enabled": True, **self.source.lyrics(), "rev": self.source.rev,
                "server_time": now}

    @staticmethod
    def _signature(msg: dict) -> str:
        return json.dumps({k: v for k, v in msg.items() if k not in ("server_time", "rev")}, sort_keys=True)

    def subscribe(self, audience: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        msg = self.payload(audience)
        self._sig[audience] = self._signature(msg)
        q.put_nowait(sse_frame(msg))
        self.subs[audience].add(q)
        return q

    def unsubscribe(self, audience: str, q: asyncio.Queue) -> None:
        self.subs[audience].discard(q)

    def refresh(self) -> None:
        """Send each audience with readers whatever changed for it. Called when the follower's state changes
        and when the lyrics switches move."""
        for audience, subs in self.subs.items():
            if not subs:
                continue
            msg = self.payload(audience)
            sig = self._signature(msg)
            if sig == self._sig.get(audience):
                continue
            self._sig[audience] = sig
            frame = sse_frame(msg)
            for q in list(subs):
                try:
                    q.put_nowait(frame)
                except asyncio.QueueFull:
                    subs.discard(q)   # stopped reading: it reconnects and is told the state

    def viewer_count(self) -> int:
        return sum(len(s) for s in self.subs.values())


lyrics_feed = LyricsFeed(follower)


# ------------------------------------------------------------------------------------------------ discovery
SERVICE = "_proapiv1ws._tcp.local"   # what ProPresenter 22 announces; its SRV port is the API port
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353


def _name(labels: list[str]) -> bytes:
    return b"".join(bytes([len(p.encode())]) + p.encode() for p in labels if p) + b"\0"


def _read_name(data: bytes, pos: int) -> tuple[str, int]:
    labels, end, jumped, hops = [], pos, False, 0
    while True:
        if pos >= len(data) or hops > 20:
            raise ValueError("bad name")
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            ptr = ((n & 0x3F) << 8) | data[pos + 1]
            if not jumped:
                end = pos + 2
            pos, jumped, hops = ptr, True, hops + 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if jumped else pos)


def parse_mdns(data: bytes) -> list[tuple[str, int, bytes, str]]:
    """Every resource record in a DNS message: (name, type, rdata, the name the rdata holds when it is a PTR or
    SRV). Raises ValueError on a message that does not parse."""
    try:
        _, _, qd, an, ns, ar = struct.unpack("!6H", data[:12])
        pos = 12
        for _ in range(qd):
            _, pos = _read_name(data, pos)
            pos += 4
        out = []
        for _ in range(an + ns + ar):
            name, pos = _read_name(data, pos)
            rtype, _cls, _ttl, rdlen = struct.unpack("!HHIH", data[pos:pos + 10])
            pos += 10
            rdata = data[pos:pos + rdlen]
            target = ""
            if rtype == 12:                               # PTR
                target, _ = _read_name(data, pos)
            elif rtype == 33 and rdlen >= 7:              # SRV: priority, weight, port, target
                target, _ = _read_name(data, pos + 6)
            out.append((name, rtype, rdata, target))
            pos += rdlen
        return out
    except (struct.error, IndexError) as exc:
        raise ValueError("bad message") from exc


def collect(records: list[tuple[str, int, bytes, str]]) -> list[dict]:
    """ProPresenters found in a set of records: name, host name, address, port."""
    inst = [t for n, ty, _, t in records if ty == 12 and n.lower() == SERVICE]
    srv = {n.lower(): (struct.unpack("!H", d[4:6])[0], t) for n, ty, d, t in records if ty == 33 and len(d) >= 7}
    addr = {n.lower(): socket.inet_ntoa(d) for n, ty, d, _ in records if ty == 1 and len(d) == 4}
    found = []
    for full in inst:
        hit = srv.get(full.lower())
        if hit is None:
            continue
        port, host = hit
        found.append({"name": full[: -len(SERVICE) - 1], "host": host.rstrip("."),
                      "address": addr.get(host.lower().rstrip("."), ""), "port": port})
    return found


def discover(seconds: float = 2.0) -> list[dict]:
    """Ask the LAN for ProPresenter over multicast DNS and return what answers within `seconds`. Standard library
    only (zeroconf is not in requirements.txt). The question is asked from an ordinary port, so the answer comes
    straight back to it. Multicast has to leave this machine's own network: from Docker Desktop's VM it never
    reaches the LAN, so there the address is typed in."""
    query = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _name(SERVICE.split(".")) + struct.pack("!HH", 12, 1)
    records: list[tuple[str, int, bytes, str]] = []
    asked: set[tuple[int, str]] = set()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        sock.settimeout(0.25)
        deadline = time.monotonic() + seconds
        sent = -1.0
        while time.monotonic() < deadline:
            if time.monotonic() - sent > 0.8:
                sock.sendto(query, (MDNS_GROUP, MDNS_PORT))
                sent = time.monotonic()
            try:
                data, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                records += parse_mdns(data)
            except ValueError:
                continue
            # A responder that answered with less than all of it: ask for the rest by name.
            for n, ty, _, t in list(records):
                want = (33, t) if ty == 12 and n.lower() == SERVICE else (1, t) if ty == 33 else None
                if want and want not in asked and t:
                    asked.add(want)
                    q = struct.pack("!6H", 0, 0, 1, 0, 0, 0) + _name(t.rstrip(".").split(".")) \
                        + struct.pack("!HH", want[0], 1)
                    sock.sendto(q, (MDNS_GROUP, MDNS_PORT))
    except OSError as exc:
        log.info("mDNS search failed: %s", exc)
    finally:
        sock.close()
    seen, out = set(), []
    for f in collect(records):
        key = (f["host"].lower(), f["port"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out
