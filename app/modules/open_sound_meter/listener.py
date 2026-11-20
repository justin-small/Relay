"""Open Sound Meter listener (#149): receives its UDP packets and turns them into readings for core's metering seam.

The packet shape varies between Open Sound Meter releases, so the parser is tolerant: a packet is JSON (an object, or
an object of objects) or text of `key=value` / `key: value` pairs. A key names its own weighting and response when it
can (LAF, LAS, LCF, LCS, dBA, dBC, LAeq); a bare value (`spl`, `db`, `level`, `value`) takes the weighting and
response chosen in the module's settings. A packet with nothing recognisable is counted and the newest few are kept,
shortened, for the admin page, so a new release's shape can be seen and added.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time

from ... import metering, netsock

log = logging.getLogger("relay.open_sound_meter")

SOURCE = "open_sound_meter"
DEFAULT_PORT = 9999
MAX_PACKET = 4096
MAX_UNKNOWN = 5
SAMPLE_CHARS = 200
_GROUP = re.compile(r"(22[4-9]|23\d)\.\d{1,3}\.\d{1,3}\.\d{1,3}")
_PAIR = re.compile(r"([A-Za-z][A-Za-z0-9_.\-]{0,40})\s*[=:]\s*(-?\d+(?:\.\d+)?)")
_AMBIGUOUS = {"spl", "db", "level", "value", "leq", "dbspl", "sound", "noise"}


def _num(v, lo: float, hi: float, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return default
    return default if n != n else round(max(lo, min(hi, n)), 1)


def clean_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    group = raw.get("group").strip() if isinstance(raw.get("group"), str) else ""
    if group and not _GROUP.fullmatch(group):
        group = ""
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = DEFAULT_PORT
    warn = _num(raw.get("warn_db"), 40.0, 140.0, 85.0)
    return {"group": group, "port": port,
            "weighting": raw.get("weighting") if raw.get("weighting") in ("A", "C") else "A",
            "response": raw.get("response") if raw.get("response") in ("fast", "slow") else "slow",
            "warn_db": warn, "limit_db": max(warn, _num(raw.get("limit_db"), 40.0, 140.0, 95.0))}


def _flatten(obj, prefix: str = "", depth: int = 0, out: dict | None = None) -> dict:
    out = {} if out is None else out
    if depth > 3 or len(out) > 64:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                _flatten(v, k if not prefix else prefix + "." + k, depth + 1, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:64]):
            _flatten(v, f"{prefix}.{i}", depth + 1, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix] = float(obj)
    return out


def _pairs(data: bytes) -> dict[str, float]:
    text = data[:MAX_PACKET].decode("utf-8", "replace").strip()
    if text[:1] in "{[":
        try:
            return _flatten(json.loads(text))
        except ValueError:
            pass
    return {m.group(1): float(m.group(2)) for m in _PAIR.finditer(text)}


def _slot(key: str, weighting: str, response: str) -> str | None:
    """The reading a key stands for: a_fast, a_slow, c_fast, c_slow, leq_a, or None when it is not a level."""
    k = re.sub(r"[^a-z]", "", key.split(".")[-1].lower())
    if k in ("laeq", "aeq", "leqa", "dbaeq"):
        return "leq_a"
    m = re.fullmatch(r"(?:l|db)?(a|c)(f|s|fast|slow)", k)
    if m:
        return f"{m.group(1)}_{'fast' if m.group(2) in ('f', 'fast') else 'slow'}"
    if k in ("dba", "dbc", "la", "lc"):
        return f"{k[-1]}_{response}"
    if k in _AMBIGUOUS:
        return "leq_a" if k == "leq" and weighting == "A" else (None if k == "leq" else f"{weighting.lower()}_{response}")
    return None


def parse_packet(data: bytes, weighting: str = "A", response: str = "slow") -> dict | None:
    """A reading from one packet ({a_fast...}), or None when nothing in it is a level."""
    out: dict = {}
    for key, val in _pairs(data).items():
        slot = _slot(key, weighting, response)
        if slot and -20.0 <= val <= 200.0:
            out.setdefault(slot, val)
    return out or None


class Service:
    """Receives on a socket from core's routed helpers, in a thread: a blocking read with a timeout works the same on
    every platform, and a packet is parsed and published in a few microseconds."""

    def __init__(self) -> None:
        self.settings = clean_settings(None)
        self.packets = 0
        self.unknown = 0
        self.samples: list[str] = []
        self.last = 0.0
        self.error = ""
        self._sock = None
        self._thread: threading.Thread | None = None
        self._running = False

    async def start(self) -> None:
        from ... import config

        self._running = True
        await asyncio.to_thread(self._open, clean_settings(config.get().get("open_sound_meter")))

    def _open(self, settings: dict) -> None:
        self._close()
        self.settings, self.error = settings, ""
        self.packets = self.unknown = 0
        self.samples, self.last = [], 0.0
        try:
            if settings["group"]:
                sock = netsock.multicast_socket(SOURCE, settings["group"], settings["port"])
            else:
                sock = netsock.udp_socket(SOURCE, settings["port"], reuse=True)
        except netsock.RouteError as exc:
            self.error = str(exc)
            return
        except OSError:
            self.error = netsock.failure(SOURCE, f"Could not listen on UDP port {settings['port']}")
            return
        sock.settimeout(0.5)
        self._sock = sock
        self._thread = threading.Thread(target=self._read, args=(sock,), name="relay-osm", daemon=True)
        self._thread.start()

    def _read(self, sock) -> None:
        while self._sock is sock:
            try:
                data, _ = sock.recvfrom(MAX_PACKET)
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                self.packet(data)
            except Exception:
                log.exception("Open Sound Meter packet failed")

    def _close(self) -> None:
        sock, self._sock = self._sock, None
        thread, self._thread = self._thread, None
        if sock is not None:
            sock.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    async def stop(self) -> None:
        self._running = False
        await asyncio.to_thread(self._close)

    async def restart(self) -> None:
        from ... import config

        if self._running:
            await asyncio.to_thread(self._open, clean_settings(config.get().get("open_sound_meter")))

    def packet(self, data: bytes) -> None:
        self.packets += 1
        s = self.settings
        reading = parse_packet(data, s["weighting"], s["response"])
        if reading is None:
            self.unknown += 1
            if len(self.samples) < MAX_UNKNOWN:
                self.samples.append(data[:SAMPLE_CHARS].decode("utf-8", "replace"))
            return
        self.last = time.time()
        metering.publish(SOURCE, {**reading, "warn_db": s["warn_db"], "limit_db": s["limit_db"]})

    def snapshot(self) -> dict:
        s = self.settings
        return {**s, "listening": self._sock is not None, "error": self.error, "packets": self.packets,
                "unknown": self.unknown, "samples": list(self.samples), "heard": time.time() - self.last < 5.0
                if self.last else False}


service = Service()
