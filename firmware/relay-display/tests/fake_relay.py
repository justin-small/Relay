"""A stand-in for Relay's caption stream, for the emulator suite.

It serves `GET /stream?stream=...&lang=...&finals=1[&last_id=N]` the way `app/main.py` and `app/hub.py`
do: chunked, as uvicorn sends it, with `id:` on each finished line, a snapshot on a fresh connection or a
resume older than its history, only the missed lines on a resume it can cover, a snapshot with no lines
on a reset, and `: ping` when nothing else is said. It keeps one history per feed and one counter for
all of them, as Relay does, so seqs have gaps.

The test drives it: `commit` a line, `reset`, `drop` every open connection, `hold` new connections at the
door while lines are committed, `mute` the pings so a connection goes silent. Standard library only.

**What it is not.** It is Relay's wire format and resume rule copied by hand, not Relay. A change to
`app/hub.py` that this file does not follow is not caught here; the server's own tests own that side.
"""

from __future__ import annotations

import contextlib
import json
import queue
import socket
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from qemu import HOST_FROM_GUEST

LANGUAGES = ("SPANISH", "FRENCH", "PORTUGUESE")


def _frame(message: dict, seq: int | None = None) -> bytes:
    prefix = b"id: %d\n" % seq if seq else b""
    return prefix + b"data: " + json.dumps(message, ensure_ascii=False).encode() + b"\n\n"


class FakeRelay:
    def __init__(self, *, port: int = 0, history_lines: int = 500, ping_s: float = 1.0) -> None:
        self.ping_s = ping_s
        self.history_lines = history_lines
        self._lock = threading.Lock()
        self._seq = 0
        self._history: dict[tuple[str, str], deque[dict]] = {}
        self._reset_seq: dict[tuple[str, str], int] = {}
        self._subscribers: dict[tuple[str, str], list[queue.Queue]] = {}
        self._handlers: list[BaseHTTPRequestHandler] = []
        self.requests: list[dict[str, str]] = []
        self.muted = False
        # Set: connections are served. Cleared: a new connection waits here before it is primed, so the
        # test can commit lines that only a resume can deliver.
        self.door = threading.Event()
        self.door.set()

        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
                relay._serve(self)

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    # -- what the test sees ---------------------------------------------------

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        """The address the panel uses: the host, as the emulator's network shows it."""
        return f"http://{HOST_FROM_GUEST}:{self.port}"

    def lines(self, stream: str = "translation", lang: str = "SPANISH") -> list[tuple[int, str]]:
        with self._lock:
            return [(line["seq"], line["text"]) for line in self._channel(stream, lang)]

    def wait_requests(self, count: int, *, timeout: float = 30.0) -> list[dict[str, str]]:
        deadline = time.monotonic() + timeout
        while len(self.requests) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(f"waited {timeout}s for {count} requests; got {self.requests}")
            time.sleep(0.02)
        return list(self.requests)

    # -- what the test does ---------------------------------------------------

    def commit(self, text: str, stream: str = "translation", lang: str = "SPANISH") -> int:
        """A finished line, as `Hub.publish_final` makes one. Returns its seq."""
        with self._lock:
            # Relay's counter is shared by every feed and by the partial lines a panel never sees.
            self._seq += 3
            line = {"seq": self._seq, "text": text, "ts": time.time()}
            self._channel(stream, lang).append(line)
            frame = _frame(
                {"stream": stream, "lang": lang, "seq": line["seq"], "delta": "", "text": text,
                 "final": True, "ts": line["ts"]},
                line["seq"],
            )
            self._send(stream, lang, frame)
            return line["seq"]

    def reset(self, stream: str = "translation", lang: str = "SPANISH") -> None:
        """Start: the history is cleared and every viewer gets an empty snapshot."""
        with self._lock:
            self._channel(stream, lang).clear()
            self._reset_seq[(stream, lang)] = self._seq
            self._send(stream, lang, _frame(self._snapshot(stream, lang)))

    def targets(self) -> None:
        """A change to the live languages, to every viewer of every feed."""
        frame = _frame({"type": "targets", "source": {"lang": "ENGLISH", "label": "English", "iso": "en",
                                                      "script": "latin"},
                        "live": [{"lang": "SPANISH", "label": "Spanish", "script": "latin"}],
                        "running": True, "font_px": 32})
        with self._lock:
            for key in self._subscribers:
                self._send(*key, frame)

    def drop(self) -> None:
        """Every open stream is cut, as a Wi-Fi drop or a restart of Caddy would."""
        with self._lock:
            handlers = list(self._handlers)
            for subs in self._subscribers.values():
                for q in subs:
                    q.put(None)
        for handler in handlers:
            with contextlib.suppress(OSError):
                handler.connection.shutdown(socket.SHUT_RDWR)

    def close(self) -> None:
        self.door.set()
        self.drop()
        self._server.shutdown()
        self._server.server_close()

    # -- the server -----------------------------------------------------------

    def _channel(self, stream: str, lang: str) -> deque[dict]:
        return self._history.setdefault((stream, lang), deque(maxlen=self.history_lines))

    def _snapshot(self, stream: str, lang: str) -> dict:
        return {"type": "snapshot", "stream": stream, "lang": lang,
                "lines": list(self._channel(stream, lang)), "open": "", "ts": time.time()}

    def _send(self, stream: str, lang: str, frame: bytes) -> None:
        for q in self._subscribers.get((stream, lang), []):
            q.put(frame)

    def _resume(self, stream: str, lang: str, last_id: int) -> list[bytes]:
        """`Hub._resume_frames`, finished lines only."""
        history = self._channel(stream, lang)
        if last_id and self._reset_seq.get((stream, lang), 0) < last_id <= self._seq:
            oldest = history[0]["seq"] if history else None
            if oldest is None or oldest <= last_id + 1:
                return [
                    _frame({"stream": stream, "lang": lang, "seq": line["seq"], "delta": "",
                            "text": line["text"], "final": True, "ts": line["ts"]}, line["seq"])
                    for line in history if line["seq"] > last_id
                ]
        return [_frame(self._snapshot(stream, lang))]

    def _serve(self, handler: BaseHTTPRequestHandler) -> None:
        url = urlparse(handler.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path != "/stream":
            handler.send_error(404)
            return
        self.requests.append(query)
        stream = query.get("stream", "")
        lang = "ENGLISH" if stream == "transcription" else query.get("lang", "")
        if stream not in ("transcription", "translation") or (stream == "translation" and lang not in LANGUAGES):
            body = b'{"detail":"unknown language"}'
            handler.send_response(400)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
            return
        self.door.wait()

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler.close_connection = True

        q: queue.Queue = queue.Queue()
        with self._lock:
            last_id = int(query.get("last_id", "0") or 0)
            for frame in self._resume(stream, lang, last_id):
                q.put(frame)
            self._subscribers.setdefault((stream, lang), []).append(q)
            self._handlers.append(handler)
        try:
            while True:
                try:
                    frame = q.get(timeout=self.ping_s)
                except queue.Empty:
                    if self.muted:
                        continue
                    frame = b": ping\n\n"
                if frame is None:
                    break
                # Each frame in two chunks, so a frame split across chunks is read too.
                cut = min(7, len(frame))
                for part in (frame[:cut], frame[cut:]):
                    if part:
                        handler.wfile.write(b"%x\r\n" % len(part) + part + b"\r\n")
                handler.wfile.flush()
        except OSError:
            pass
        finally:
            with self._lock:
                with contextlib.suppress(ValueError):
                    self._subscribers[(stream, lang)].remove(q)
                with contextlib.suppress(ValueError):
                    self._handlers.remove(handler)
