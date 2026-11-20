"""ProdMesh RTA client (#149): follows a remote analyser over a WebSocket and publishes what it measures.

The analyser sends JSON messages. Which fields it has varies, so the parser is tolerant: it looks (case-insensitively,
at any depth up to three) for SPL as `spl` / `dba` / `laeq`, loudness as `lufs` / `loudness`, a signal state as `signal` /
`state` and the analyser as a list of 31 numbers under `bands` / `rta` / `analyser` / `spectrum` (a list of numbers
only: 31 bands, 20 Hz to 20 kHz). A message with none of these is counted and the newest few are kept, shortened, for
the admin page. Not verified against a real ProdMesh install: the shape is the one the issue describes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from urllib.parse import urlsplit

import websockets

from ... import metering, netsock

log = logging.getLogger("relay.prodmesh_rta")

SOURCE = "prodmesh_rta"
BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_FRAME = 200_000
QUIET_S = 30.0
MAX_UNKNOWN = 5
SAMPLE_CHARS = 200
_SPL = ("spl", "dba", "laeq", "la", "level")
_BANDS = ("bands", "rta", "analyser", "analyzer", "spectrum")


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return default if n != n else round(max(lo, min(hi, n)), 1)


def clean_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    url = raw.get("url").strip() if isinstance(raw.get("url"), str) else ""
    try:
        parts = urlsplit(url)
        ok = parts.scheme in ("ws", "wss") and bool(parts.hostname) and len(url) <= 300 and not parts.username
    except ValueError:
        ok = False
    warn = _num(raw.get("warn_db"), 40.0, 140.0, 85.0)
    return {"url": url if ok else "", "warn_db": warn, "limit_db": max(warn, _num(raw.get("limit_db"), 40.0, 140.0, 95.0))}


def _find(obj, names: tuple[str, ...], depth: int = 0):
    if depth > 3 or not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() in names:
            return v
    for v in obj.values():
        got = _find(v, names, depth + 1)
        if got is not None:
            return got
    return None


def _number(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse_message(text: str) -> dict | None:
    """{"reading": {...}, "lufs": float|None, "signal": str} from one message, or None when nothing is recognised."""
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    reading: dict = {}
    spl = _number(_find(obj, _SPL))
    if spl is not None:
        reading["a_slow"] = reading["a_fast"] = spl
    bands = _find(obj, _BANDS)
    if isinstance(bands, list) and len(bands) == metering.BAND_N and all(_number(b) is not None for b in bands):
        reading["bands"] = [float(b) for b in bands]
    lufs = _number(_find(obj, ("lufs", "loudness")))
    signal = _find(obj, ("signal", "state", "signalstate"))
    signal = re.sub(r"[^A-Za-z0-9 _-]", "", signal)[:20] if isinstance(signal, str) else ""
    if not reading and lufs is None and not signal:
        return None
    return {"reading": reading, "lufs": lufs, "signal": signal}


class Client:
    def __init__(self, url: str, warn_db: float, limit_db: float) -> None:
        self.url, self.warn_db, self.limit_db = url, warn_db, limit_db
        self.connected = False
        self.error = ""
        self.signal = ""
        self.lufs: float | None = None
        self.messages = 0
        self.unknown = 0
        self.samples: list[str] = []
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="prodmesh-rta")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self.connected = False

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._once()
                attempt = 0
            except asyncio.CancelledError:
                raise
            except (OSError, websockets.WebSocketException, asyncio.TimeoutError, ValueError):
                self.error = netsock.failure(SOURCE, "Could not reach the analyser")
            except Exception:
                log.exception("ProdMesh RTA client failed")
                self.error = "Lost the connection"
            self.connected = False
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(self._stop.wait(), wait)
            except asyncio.TimeoutError:
                pass

    async def _once(self) -> None:
        async with netsock.websocket_session(SOURCE, self.url, max_size=MAX_FRAME, open_timeout=5) as ws:
            self.connected, self.error = True, ""
            heard = time.monotonic()
            while not self._stop.is_set():
                if time.monotonic() - heard > QUIET_S:
                    raise asyncio.TimeoutError()
                try:
                    frame = await asyncio.wait_for(ws.recv(), 1.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(frame, bytes):
                    continue
                heard = time.monotonic()
                self.message(frame)

    def message(self, frame: str) -> None:
        self.messages += 1
        got = parse_message(frame)
        if got is None:
            self.unknown += 1
            if len(self.samples) < MAX_UNKNOWN:
                self.samples.append(frame[:SAMPLE_CHARS])
            return
        self.signal = got["signal"] or self.signal
        if got["lufs"] is not None:
            self.lufs = got["lufs"]
        if got["reading"]:
            metering.publish(SOURCE, {**got["reading"], "warn_db": self.warn_db, "limit_db": self.limit_db})


class Service:
    def __init__(self) -> None:
        self.client: Client | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._open()

    def _open(self) -> None:
        from ... import config

        s = clean_settings(config.get().get("prodmesh_rta"))
        if self._running and s["url"]:
            self.client = Client(s["url"], s["warn_db"], s["limit_db"])
            self.client.start()

    async def _close(self) -> None:
        if self.client is not None:
            c, self.client = self.client, None
            await c.close()

    async def stop(self) -> None:
        self._running = False
        await self._close()

    async def restart(self) -> None:
        await self._close()
        self._open()

    def snapshot(self) -> dict:
        from ... import config

        s = clean_settings(config.get().get("prodmesh_rta"))
        c = self.client
        return {**s, "configured": bool(s["url"]), "connected": bool(c and c.connected), "error": c.error if c else "",
                "signal": c.signal if c else "", "lufs": c.lufs if c else None,
                "messages": c.messages if c else 0, "unknown": c.unknown if c else 0,
                "samples": list(c.samples) if c else []}


service = Service()
