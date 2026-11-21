"""AES67 stream discovery (#266): listens for SAP announcements and keeps the table of streams the picker shows.

Streams are announced by SAP on 239.255.255.255 and 224.2.127.254, port 9875 (RFC 2974). Each announcement is parsed
(sdp.py) and kept until the sender deletes it or has been silent for STALE_S. A stream the operator chose is
remembered in the module's settings with its SDP, so the saved input still opens after a restart before the next
announcement arrives.
"""
from __future__ import annotations

import logging
import select
import threading
import time
from typing import Any

from ... import netroute, netsock
from .sdp import Stream, parse, parse_sap

log = logging.getLogger("relay.aes67")

CLASS_ID = "aes67"
GROUPS = ("239.255.255.255", "224.2.127.254")
SAP_PORT = 9875
STALE_S = 300.0
MAX_STREAMS = 256
MAX_REMEMBERED = 8
NO_PORT = "The AES67 network port is not available."
NO_LISTEN = "Could not listen for AES67 announcements on the network."


def clean_settings(raw: Any) -> dict:
    """`{"remembered": {stream id: sdp}, "output": {...}}`: the streams the operator chose, each kept only while its
    SDP still parses as the stream it is filed under, and the output's settings (output.py)."""
    out: dict[str, str] = {}
    src = raw.get("remembered") if isinstance(raw, dict) else None
    if isinstance(src, dict):
        for sid, sdp in src.items():
            s = parse(sdp) if isinstance(sdp, str) else None
            if s is not None and s.id == sid and len(out) < MAX_REMEMBERED:
                out[sid] = sdp
    from .output import clean as clean_output

    return {"remembered": out, "output": clean_output(raw.get("output") if isinstance(raw, dict) else None)}


class Service:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._streams: dict[str, dict] = {}      # id -> {"stream", "sdp", "seen", "sender"}
        self._remembered: dict[str, Stream] = {}
        self.error = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.heard = 0

    # ------------------------------------------------------------------------------------------------ table
    def configure(self, settings: dict) -> None:
        mem = {}
        for sid, sdp in clean_settings(settings)["remembered"].items():
            stream = parse(sdp)
            if stream is not None:
                mem[sid] = stream
        with self._lock:
            self._remembered = mem

    def observe(self, packet: bytes, now: float | None = None) -> None:
        """One SAP packet. Public so a test can feed announcements without a socket."""
        ann = parse_sap(packet)
        if ann is None:
            return
        stream = parse(ann.sdp)
        if stream is None:
            return
        now = time.monotonic() if now is None else now
        with self._lock:
            self.heard += 1
            if ann.withdraw:
                self._streams.pop(stream.id, None)
                return
            if stream.id not in self._streams and len(self._streams) >= MAX_STREAMS:
                return
            self._streams[stream.id] = {"stream": stream, "sdp": ann.sdp, "seen": now, "sender": ann.origin}

    def expire(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            for sid in [k for k, v in self._streams.items() if now - v["seen"] > STALE_S]:
                del self._streams[sid]

    def find(self, stream_id: str) -> Stream | None:
        """The stream by id: as last announced, else as remembered."""
        with self._lock:
            live = self._streams.get(stream_id)
            return live["stream"] if live else self._remembered.get(stream_id)

    def sdp_of(self, stream_id: str) -> str | None:
        with self._lock:
            live = self._streams.get(stream_id)
            return live["sdp"] if live else None

    def snapshot(self, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self._lock:
            rows = [{**v["stream"].as_dict(), "sender": v["sender"], "age": round(now - v["seen"]), "live": True}
                    for v in self._streams.values()]
            rows += [{**s.as_dict(), "sender": "", "age": None, "live": False}
                     for sid, s in self._remembered.items() if sid not in self._streams]
            heard, error = self.heard, self.error
        rows.sort(key=lambda r: (not r["dante"], r["name"].lower(), r["id"]))
        return {"streams": rows, "error": error, "heard": heard, "running": self.running}

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # ------------------------------------------------------------------------------------------------ listening
    def start(self) -> None:
        self.stop()
        r = netroute.resolve(CLASS_ID)
        if not r.available:
            self.error = NO_PORT
            return
        socks = []
        try:
            for group in GROUPS:
                socks.append(netsock.multicast_socket(r, group, SAP_PORT))
        except (OSError, ValueError):
            for s in socks:
                s.close()
            self.error = NO_LISTEN
            return
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(socks, self._stop), name="aes67-sap", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)
        with self._lock:
            self._streams.clear()

    def _run(self, socks: list, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                ready, _, _ = select.select(socks, [], [], 1.0)
                for s in ready:
                    try:
                        data, _addr = s.recvfrom(4096)
                    except OSError:
                        continue
                    self.observe(data)
                self.expire()
        finally:
            for s in socks:
                try:
                    s.close()
                except OSError:
                    pass


service = Service()
