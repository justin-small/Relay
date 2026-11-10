"""A stand-in for Relay's caption stream, for the emulator suite.

It serves `GET /stream?stream=...&lang=...&finals=1[&last_id=N]` the way `app/main.py` and `app/hub.py`
do: chunked, as uvicorn sends it, with `id:` on each finished line, a snapshot on a fresh connection or a
resume older than its history, only the missed lines on a resume it can cover, a snapshot with no lines
on a reset, and `: ping` when nothing else is said. It keeps one history per feed and one counter for
all of them, as Relay does, so seqs have gaps.

It also serves the panel registry (`app/panels.py`): `POST /api/panels/checkin` and `GET /api/panels/<id>/events`, with
the rule that decides when a `set_feed` is sent (the panel's `feed_rev` against the operator's), and `404` on the stream
for a panel that has not checked in. `set_feed` is the operator's setting; `forget_panels` is a Relay restart.

The test drives it: `commit` a line, `reset`, `drop` every open connection, `hold` new connections at the
door while lines are committed, `mute` the pings so a connection goes silent. Standard library only.

**What it is not.** It is Relay's wire format and resume rule copied by hand, not Relay. A change to
`app/hub.py` that this file does not follow is not caught here; the server's own tests own that side.
"""

from __future__ import annotations

import contextlib
import hashlib
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

# What Relay says about each language it can have live: `app/languages.py`'s label and script. JAPANESE and RUSSIAN are
# here so a test can have a live language the panel cannot draw.
SCRIPTS = {
    "SPANISH": ("Spanish", "es", "latin"),
    "FRENCH": ("French", "fr", "latin"),
    "PORTUGUESE": ("Portuguese", "pt", "latin"),
    "JAPANESE": ("Japanese", "ja", "kana"),
    "RUSSIAN": ("Russian", "ru", "cyrillic"),
}


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
        self.api_requests = 0
        # Which languages are live, and the source language's script: what `targets_payload` says.
        self.live: list[str] = ["SPANISH", "FRENCH"]
        self.source_script = "latin"
        self.muted = False
        # The panel registry. `checkins` is every check-in body, in order; `known` the panel IDs that have checked in.
        self.checkins: list[dict] = []
        self.known: set[str] = set()
        self.panel_streams: list[tuple[str, queue.Queue]] = []
        self.panel_stream_opens = 0
        self.operator_feed: dict | None = None  # {"rev": N, "feeds": [..]}
        self.acked_rev = 0
        self.checkin_status = 200
        # Firmware updates (`app/panelfw.py`, `app/panels.py`). `image` is what is served at `/api/panels/firmware/<v>.bin`;
        # `update_offer` is what Relay is asking the panel to fetch, and ends (as Relay's does) on a failed, rolled_back
        # or installed report. `update_reports` is every report in order. A check-in from a version in `refuse_versions`
        # is answered 503, which is a new image that cannot reach Relay.
        self.image: bytes | None = None
        self.image_version = ""
        self.update_offer: dict | None = None
        self.update_reports: list[dict] = []
        self.image_requests = 0
        self.update_report_status = 200
        self.cut_image_at: int | None = None
        self.refuse_versions: set[str] = set()
        # The Planning Center service (#79, #81): the panel's mode (in check-in replies and pushed on its stream), the
        # service types offered in `targets`, and the whole state per service type, sent on connect and on each change.
        self.service_mode: dict = {"mode": "off", "service_type": None}
        self.service_offers: list[dict] = []
        self.service_states: dict[str, dict] = {}
        self._service_subs: dict[str, list[queue.Queue]] = {}
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

            def do_POST(self) -> None:  # noqa: N802
                relay._serve_post(self)

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

    def _targets_payload(self) -> dict:
        """`Engine.targets_payload`: the source, and the live languages sorted by label."""
        live = sorted(self.live, key=lambda t: SCRIPTS[t][0])
        return {
            "source": {"lang": "ENGLISH", "label": "English", "iso": "en", "script": self.source_script},
            "live": [{"target": t, "label": SCRIPTS[t][0], "iso": SCRIPTS[t][1], "script": SCRIPTS[t][2],
                      "state": "open"} for t in live],
            "running": True,
            "font_px": 48,
            "service": list(self.service_offers),
        }

    def set_live(self, languages: list[str]) -> None:
        """A change to the live languages, to every viewer of every feed (`Hub.broadcast_viewers`)."""
        self.live = list(languages)
        self.targets()

    def targets(self) -> None:
        """The live set as it is, to every viewer of every feed. Relay does this when the set changes, and not when
        a viewer connects: a viewer asks `/api/targets` for the first picture."""
        frame = _frame({"type": "targets", **self._targets_payload()})
        with self._lock:
            for key in self._subscribers:
                self._send(*key, frame)

    def set_feed(self, feeds: list[dict], *, push: bool = True) -> int:
        """The operator sets the panels' feed. Pushed on every open event stream unless `push` is false, and
        otherwise left for the next check-in or stream to find. Returns the revision."""
        with self._lock:
            rev = (self.operator_feed or {"rev": 0})["rev"] + 1
            self.operator_feed = {"rev": rev, "feeds": feeds}
            if push:
                frame = _frame({"type": "set_feed", **self.operator_feed})
                for _, q in self.panel_streams:
                    q.put(frame)
            return rev

    def set_service_mode(self, mode: str, service_type: str | None = None, key: str | None = None) -> None:
        """The operator sets the panel's service mode (`Registry.update`): pushed at once, and in later replies."""
        msg: dict = {"mode": mode, "service_type": service_type}
        if key is not None and mode in ("control", "both"):
            msg["key"] = key
        with self._lock:
            self.service_mode = msg
            frame = _frame({"type": "service_mode", **msg})
            for _, q in self.panel_streams:
                q.put(frame)

    def offer_service(self, offers: list[dict]) -> None:
        """The followed service types, as `targets` lists them; sent to every viewer as Relay does on a change."""
        self.service_offers = list(offers)
        self.targets()

    def set_service(self, service_type: str, state: dict) -> None:
        """Planning Center's state changed: the whole `service` message, to every panel on that feed."""
        msg = {"type": "service", "service_type": service_type, "followed": True, "server_time": time.time(), **state}
        with self._lock:
            self.service_states[service_type] = msg
            frame = _frame(msg)
            for q in self._service_subs.get(service_type, []):
                q.put(frame)

    def push(self, message: dict) -> None:
        """Any message, on every open panel stream (an `update_available`, say)."""
        with self._lock:
            for _, q in self.panel_streams:
                q.put(_frame(message))

    def serve_image(self, image: bytes, version: str) -> None:
        """The image Relay holds, at `/api/panels/firmware/<version>.bin`."""
        self.image, self.image_version = image, version

    def offer_update(self, *, now: bool = True, version: str | None = None, image: bytes | None = None) -> dict:
        """The operator asks the panel to update: carried in its check-in replies and pushed on its open streams, as
        `Registry.request_update` does. The size and checksum are the image's unless `image` says what Relay claims."""
        version = version or self.image_version
        claimed = image if image is not None else self.image
        assert claimed is not None
        offer = {"version": version, "size": len(claimed), "sha256": hashlib.sha256(claimed).hexdigest(),
                 "path": f"/api/panels/firmware/{version}.bin", "now": now}
        with self._lock:
            self.update_offer = offer
            frame = _frame({"type": "update_available", **offer})
            for _, q in self.panel_streams:
                q.put(frame)
        return offer

    def wait_update_report(self, state: str, *, timeout: float = 120.0, after: int = 0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for report in self.update_reports[after:]:
                if report.get("state") == state:
                    return report
            if time.monotonic() >= deadline:
                raise AssertionError(f"waited {timeout}s for an update report {state!r}; got {self.update_reports}")
            time.sleep(0.1)

    def forget_panels(self) -> None:
        """A Relay restart as the registry sees it: nobody has checked in, and every stream is cut."""
        with self._lock:
            self.known.clear()
            self.acked_rev = 0
        self.drop()

    def wait_checkins(self, count: int, *, timeout: float = 60.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.checkins) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(f"waited {timeout}s for {count} check-ins; got {len(self.checkins)}")
            time.sleep(0.05)
        return list(self.checkins)

    def wait_panel_streams(self, count: int, *, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while len(self.panel_streams) < count:
            if time.monotonic() >= deadline:
                raise AssertionError(f"waited {timeout}s for {count} panel streams; got {len(self.panel_streams)}")
            time.sleep(0.05)

    def drop(self) -> None:
        """Every open stream is cut, as a Wi-Fi drop or a restart of Caddy would."""
        with self._lock:
            handlers = list(self._handlers)
            for subs in self._subscribers.values():
                for q in subs:
                    q.put(None)
            for _, q in self.panel_streams:
                q.put(None)
            for subs in self._service_subs.values():
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
        if url.path.startswith("/api/panels/") and url.path.endswith("/events"):
            self._serve_panel_events(handler, url.path[len("/api/panels/"):-len("/events")])
            return
        if url.path.startswith("/api/panels/firmware/"):
            self._serve_image(handler, url.path[len("/api/panels/firmware/"):])
            return
        if url.path == "/api/targets":
            self.api_requests += 1
            body = json.dumps(self._targets_payload()).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Content-Length", str(len(body)))
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(body)
            handler.close_connection = True
            return
        if url.path != "/stream":
            handler.send_error(404)
            return
        self.requests.append(query)
        if query.get("stream") == "service":
            self._serve_service(handler, query.get("type", ""))
            return
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

    def _serve_service(self, handler: BaseHTTPRequestHandler, service_type: str) -> None:
        """`/stream?stream=service&type=..` (app/servicefeed.py): the whole state on connect, then each change, and
        a `service_tick` in the quiet. A type Relay does not follow is a 404."""
        if not any(o["service_type"] == service_type for o in self.service_offers):
            self._json(handler, 404, {"detail": "not a followed service type"})
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler.close_connection = True
        q: queue.Queue = queue.Queue()
        with self._lock:
            state = self.service_states.get(service_type) or {
                "type": "service", "service_type": service_type, "followed": True, "plan": None, "items": [],
                "live": False, "reachable": True, "server_time": time.time()}
            q.put(_frame(state))
            self._service_subs.setdefault(service_type, []).append(q)
            self._handlers.append(handler)
        try:
            while True:
                try:
                    frame = q.get(timeout=self.ping_s)
                except queue.Empty:
                    frame = _frame({"type": "service_tick", "service_type": service_type, "server_time": time.time(),
                                    "updated_at": time.time()})
                if frame is None:
                    break
                handler.wfile.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
                handler.wfile.flush()
        except OSError:
            pass
        finally:
            with self._lock:
                with contextlib.suppress(ValueError):
                    self._service_subs[service_type].remove(q)
                with contextlib.suppress(ValueError):
                    self._handlers.remove(handler)

    # -- the panel registry ---------------------------------------------------

    def _pending(self) -> dict | None:
        """`Registry._pending_feed`: the operator's feed, until the panel has said it applied it."""
        feed = self.operator_feed
        if feed is not None and self.acked_rev < feed["rev"]:
            return {"type": "set_feed", **feed}
        return None

    def _json(self, handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(raw)
        handler.close_connection = True

    def _serve_image(self, handler: BaseHTTPRequestHandler, name: str) -> None:
        with self._lock:
            image, version = self.image, self.image_version
            self.image_requests += 1
            cut = self.cut_image_at
        if image is None or name != f"{version}.bin":
            self._json(handler, 404, {"detail": "no such firmware"})
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(image)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        try:
            # A download the network cuts part way through: the headers promised the whole image.
            handler.wfile.write(image if cut is None else image[:cut])
            handler.wfile.flush()
            if cut is not None:
                handler.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _serve_post(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlparse(handler.path).path
        if path.startswith("/api/panels/") and path.endswith("/update"):
            size = int(handler.headers.get("Content-Length") or 0)
            try:
                report = json.loads(handler.rfile.read(size))
            except ValueError:
                self._json(handler, 400, {"detail": "body must be JSON"})
                return
            with self._lock:
                self.update_reports.append(report)
                # A report Relay does not take (a 503) does not end the ask: the panel has to hold off on its own.
                if self.update_report_status == 200 and report.get("state") in ("failed", "rolled_back", "installed"):
                    self.update_offer = None
            self._json(handler, self.update_report_status, {"ok": True})
            return
        if path != "/api/panels/checkin":
            handler.send_error(404)
            return
        size = int(handler.headers.get("Content-Length") or 0)
        try:
            body = json.loads(handler.rfile.read(size))
        except ValueError:
            self._json(handler, 400, {"detail": "body must be JSON"})
            return
        if self.checkin_status != 200 or body.get("version") in self.refuse_versions:
            self._json(handler, self.checkin_status if self.checkin_status != 200 else 503, {"detail": "refused"})
            return
        with self._lock:
            self.checkins.append(body)
            self.known.add(body.get("id", ""))
            self.acked_rev = max(self.acked_rev, int(body.get("feed_rev") or 0))
            reply = {"ok": True, "name": body.get("name", ""), "interval_s": 30, "service": dict(self.service_mode)}
            pending = self._pending()
            if pending:
                reply["set_feed"] = pending
            if self.update_offer and self.update_offer["version"] == body.get("version"):
                self.update_offer = None  # `Registry._settle_update`: the version asked for has checked in
            if self.update_offer:
                reply["update"] = self.update_offer
        self._json(handler, 200, reply)

    def _serve_panel_events(self, handler: BaseHTTPRequestHandler, panel_id: str) -> None:
        with self._lock:
            known = panel_id in self.known
        if not known:
            self._json(handler, 404, {"detail": "unknown panel"})
            return
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-cache, no-transform")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        handler.close_connection = True
        q: queue.Queue = queue.Queue()
        with self._lock:
            pending = self._pending()
            if pending:
                q.put(_frame(pending))
            if self.update_offer:
                q.put(_frame({"type": "update_available", **self.update_offer}))
            entry = (panel_id, q)
            self.panel_streams.append(entry)
            self.panel_stream_opens += 1
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
                    self.panel_streams.remove(entry)
