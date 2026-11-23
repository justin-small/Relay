"""The audio bus when the Rust server owns it (#657, part of #650).

`python run.py --upstream-port N` with `RELAY_RUST_AUDIO=1` (and the server started with `--rust-audio`) runs this app behind the Rust
server, which then owns the sound cards, the lanes, the crosspoint matrix and the meters (native/relay-audio). Python still runs the
modules that use the bus, so this file is their way in. The bus keeps the API every consumer already uses (`audiobus.bus.lane(n)
.subscribe`, `play_to`, `output(id).inject`, `status`, a module's network source and sender), and each call goes to the server's
loopback door (`native/relay-server/src/internal.rs`, `/internal/audio/*`) with the same `X-Relay-Front` secret `app/front.py` uses.

* A lane reader is a streaming GET: the server sends each block of the lane as a length and raw little-endian float32.
* A module's network source (NDI, Dante) is opened and read here, and each block is POSTed to the engine; a network sender is topped
  up here from `GET pull`. Which of them to run is whatever the bus setting says: this file waits on `GET config?since=` and follows it.
* How those sources and senders are doing is POSTed back, so the Audio bus page shows it beside the engine's own state.

The bus is always on in the server, so nothing here applies a setting: a page or a route that changes the bus does it there.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import numpy as np

from . import audiobus, front, serving

log = logging.getLogger("relay.audiobus.remote")

CALL_TIMEOUT_S = 5.0
WAIT_TIMEOUT_S = 35.0                      # the server holds a config call open for up to 25 s
TAP_QUEUE = 200                            # blocks a reader may fall behind before the oldest is dropped
REPORT_EVERY_S = 0.5


def active() -> bool:
    """True when the audio bus is the Rust server's: behind it, and asked for with RELAY_RUST_AUDIO."""
    return front.enabled() and os.environ.get("RELAY_RUST_AUDIO", "").strip().lower() in ("1", "true", "yes", "on")


def base_url() -> str:
    given = (os.environ.get("RELAY_RUST_URL") or "").strip().rstrip("/")
    return given or f"http://127.0.0.1:{serving.viewer_port(80)}"


def _open(path: str, query: dict | None = None, data: bytes | None = None, json_body: Any = None, timeout: float = CALL_TIMEOUT_S):
    headers = {front.FRONT_HEADER: front.token()}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["content-type"] = "application/json"
    elif data is not None:
        headers["content-type"] = "application/octet-stream"
    url = base_url() + path + ("?" + urllib.parse.urlencode(query) if query else "")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - the server's loopback door, http only


def get_json(path: str, query: dict | None = None, timeout: float = CALL_TIMEOUT_S) -> Any:
    with _open(path, query, timeout=timeout) as resp:
        return json.loads(resp.read())


def post_json(path: str, body: Any = None, query: dict | None = None) -> Any:
    with _open(path, query, json_body=body if body is not None else {}) as resp:
        return json.loads(resp.read())


def _pcm(block: np.ndarray) -> bytes:
    return np.ascontiguousarray(block, dtype="<f4").tobytes()


# -- the engine, as the lane readers, the network sources and the senders see it -----------------------------------------------
class _Tap:
    """One lane's stream from the server, read on a thread of its own into a queue."""

    def __init__(self, lane: int) -> None:
        self.lane = lane
        self.blocks: queue.Queue[np.ndarray] = queue.Queue(TAP_QUEUE)
        self.dropped = 0
        self.closed = threading.Event()
        self._resp = None
        self._thread = threading.Thread(target=self._run, name=f"lane-{lane}-stream", daemon=True)
        self._thread.start()

    def _put(self, block: np.ndarray) -> None:
        while True:
            try:
                self.blocks.put_nowait(block)
                return
            except queue.Full:
                try:
                    self.blocks.get_nowait()
                    self.dropped += len(block)
                except queue.Empty:
                    pass

    def _run(self) -> None:
        while not self.closed.is_set():
            try:
                self._resp = resp = _open(f"/internal/audio/tap/{self.lane}", timeout=10.0)
                while not self.closed.is_set():
                    head = resp.read(4)
                    if len(head) < 4:
                        break
                    (n,) = struct.unpack("<I", head)
                    body = resp.read(n)
                    if len(body) < n:
                        break
                    self._put(np.frombuffer(body, dtype="<f4").astype(np.float32))
            except Exception:
                if not self.closed.is_set():
                    log.debug("lane %d stream lost; reconnecting", self.lane)
            finally:
                resp = self._resp
                self._resp = None
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass
            if not self.closed.is_set():
                self.closed.wait(0.5)

    def close(self) -> None:
        self.closed.set()
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


class EngineProxy:
    """The few calls the bus makes on `relay_bus.Bus`, made on the server's engine instead. Failures are the engine's own kind
    (ValueError, RuntimeError), so the code that catches them for a local engine needs no change."""

    def __init__(self, bus: "RemoteBus") -> None:
        self.bus = bus
        self._taps: dict[int, _Tap] = {}
        self._next = 1
        self._lock = threading.Lock()

    def push(self, input_id: str, block: np.ndarray, rate: int) -> None:
        try:
            ok = post_json_raw("/internal/audio/push", {"input": input_id, "channels": block.shape[1], "rate": int(rate)}, _pcm(block))
        except (OSError, ValueError) as exc:
            raise RuntimeError("the server's audio bus is not reachable") from exc
        if not ok.get("ok"):
            raise ValueError("the engine is being reconfigured")

    def pull(self, output_id: str, frames: int) -> np.ndarray:
        channels = next((o["channels"] for o in self.bus.config["outputs"] if o["id"] == output_id), 0)
        if not channels:
            raise ValueError("no such output")
        try:
            with _open("/internal/audio/pull", {"output": output_id, "frames": int(frames)}) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise ValueError("no such output") from exc
        except OSError as exc:
            raise RuntimeError("the server's audio bus is not reachable") from exc
        return np.frombuffer(raw, dtype="<f4").astype(np.float32).reshape(-1, channels)

    def inject(self, output_id: str, channels: list[int], samples: np.ndarray, rate: int) -> None:
        got = post_json_raw("/internal/audio/inject", {"output": output_id, "channels": ",".join(str(c) for c in channels), "rate": int(rate)},
                            _pcm(samples))
        if not got.get("ok"):
            raise ValueError("no such output")

    def stop_injects(self, output_id: str) -> None:
        post_json_raw("/internal/audio/stop_injects", {"output": output_id}, b"")

    def open_tap(self, lane: int) -> int:
        with self._lock:
            tap_id, self._next = self._next, self._next + 1
            self._taps[tap_id] = _Tap(int(lane))
            return tap_id

    def read_tap(self, tap_id: int, frames: int, timeout: float) -> tuple[np.ndarray, int]:
        tap = self._taps.get(tap_id)
        if tap is None:
            raise ValueError("the tap is gone")
        out: list[np.ndarray] = []
        total = 0
        try:
            block = tap.blocks.get(timeout=timeout)
        except queue.Empty:
            if tap.closed.is_set():
                raise ValueError("the tap is gone") from None
            return np.zeros(0, dtype=np.float32), 0
        out.append(block)
        total += len(block)
        while total < frames:
            try:
                block = tap.blocks.get_nowait()
            except queue.Empty:
                break
            out.append(block)
            total += len(block)
        dropped, tap.dropped = tap.dropped, 0
        return np.concatenate(out), dropped

    def close_tap(self, tap_id: int) -> None:
        with self._lock:
            tap = self._taps.pop(tap_id, None)
        if tap is not None:
            tap.close()

    def list_devices(self) -> list[dict]:
        return []

    def status(self) -> dict:
        return get_json("/internal/audio/status")

    def apply(self, cfg: dict) -> None:           # the server applies its own setting
        return None

    def close(self) -> None:
        with self._lock:
            taps, self._taps = list(self._taps.values()), {}
        for tap in taps:
            tap.close()


def post_json_raw(path: str, query: dict, data: bytes) -> dict:
    """POST raw audio (or nothing) with a query; the answer is JSON."""
    try:
        with _open(path, query, data=data) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise ValueError(f"the server refused the call ({exc.code})") from exc


# -- the bus ------------------------------------------------------------------------------------------------------------------
class RemoteBus(audiobus.Bus):
    """`audiobus.Bus` with the engine and the setting on the server. The consumer API is unchanged; what runs here is only the
    module's network sources and senders, and the follow of the server's setting."""

    def __init__(self) -> None:
        super().__init__(backend="remote")
        self._proxy = EngineProxy(self)
        self._follow_stop = threading.Event()
        self._follower: threading.Thread | None = None
        self._reported: dict | None = None

    @property
    def engine(self):
        return self._proxy

    # -- following the server's setting ---------------------------------------------------------------------------------
    def start_remote(self) -> None:
        """Follow the server's bus. It is always on there; this starts the sources and senders Python drives."""
        if self._follower is None or not self._follower.is_alive():
            self._follow_stop = threading.Event()
            self._follower = threading.Thread(target=self._follow, args=(self._follow_stop,), name="remote-bus-follow", daemon=True)
            self._follower.start()

    def _follow(self, stop: threading.Event) -> None:
        gen = None
        while not stop.is_set():
            try:
                got = get_json("/internal/audio/config", {"since": gen} if gen is not None else None, timeout=WAIT_TIMEOUT_S)
                gen = int(got["gen"])
                self._adopt(got)
            except Exception:
                if not stop.is_set():
                    log.debug("the server's audio bus is not answering; trying again")
                    gen = None
                    stop.wait(1.0)

    def _adopt(self, got: dict) -> None:
        cfg = audiobus.normalize(got.get("config") or {})
        cfg["matrix"] = list((got.get("config") or {}).get("matrix") or [])
        with self._lock:
            self.config = cfg
            for lane in self.lanes:
                lane.name = ""
            for spec in cfg["lanes"]:
                self.lanes[spec["lane"] - 1].name = spec["name"]
            if got.get("running"):
                self._stop_outputs(cfg)
                self._apply_intakes(cfg)
                self._start_outputs(cfg)
                self.running = True
                if self._super is None or not self._super.is_alive() or self._stop.is_set():
                    self._stop = threading.Event()
                    self._super = threading.Thread(target=self._supervise, args=(self._stop,), name="lane-supervisor", daemon=True)
                    self._super.start()
            else:
                self._stop_all_local()
        self._report()

    def _stop_all_local(self) -> None:
        for out in self._outputs.values():
            out.stop()
        self._outputs.clear()
        for intake in self._intakes.values():
            intake.close()
        self._intakes.clear()
        self.running = False

    # -- how the network sources and senders are doing, back to the server -----------------------------------------------
    def external_state(self) -> dict:
        inputs = {iid: {"state": py.state, "error": py.error, "label": py.label(), "rate": py.rate or None,
                        "channels": int(py.info.device.get("channels", 0)) if py.info else None, "dropped": py.dropped}
                  for iid, py in self._intakes.items()}
        outputs = {oid: {"state": "open" if py.sink is not None else ("waiting" if py.error else "closed"), "error": py.error,
                         "underruns": py.underruns + (py.sink.underruns if py.sink else 0)}
                   for oid, py in self._outputs.items()}
        return {"inputs": inputs, "outputs": outputs}

    def _report(self) -> None:
        state = self.external_state()
        if state == self._reported:
            return
        try:
            post_json("/internal/audio/external", state)
            self._reported = state
        except OSError:
            log.debug("the server did not take the state of the network sources")

    def _supervise(self, stop: threading.Event) -> None:
        while not stop.wait(REPORT_EVERY_S):
            now = time.time()
            with self._lock:
                for intake in list(self._intakes.values()):
                    self._check_intake(intake, now)
                for out in list(self._outputs.values()):
                    self._check_output(out, now)
            self._report()

    # -- what a consumer calls ---------------------------------------------------------------------------------------------
    def apply(self, cfg: dict) -> None:
        """A route into an output channel changed from Python: the server runs the bus on the matrix its routes now say."""
        post_json("/internal/audio/reapply")

    def play_to(self, key: str, mono: np.ndarray, rate: int = audiobus.RATE) -> int:
        if not len(mono):
            return 0
        try:
            got = post_json_raw("/internal/audio/play", {"key": str(key), "rate": int(rate)}, _pcm(mono))
        except (OSError, ValueError):
            log.warning("The server's audio bus did not take %s", key)
            return 0
        return int(got.get("reached") or 0)

    def status(self) -> dict:
        try:
            return get_json("/internal/audio/status")
        except OSError:
            return {"running": False, "lane_rate": audiobus.RATE, "lanes": [], "inputs": [], "outputs": [], "matrix": []}

    def register_destination(self, key: str, label: str | None) -> None:
        body = {"register": {"key": str(key), "label": str(label)}} if label is not None else {"unregister": str(key)}
        try:
            post_json("/internal/audio/destinations", body)
        except OSError:
            log.debug("the server's audio bus did not take the destination %s", key)

    def stop(self) -> None:
        self._follow_stop.set()
        with self._lock:
            self._stop_all_local()
        self._stop.set()
        if self._super is not None and self._super is not threading.current_thread():
            self._super.join(timeout=2.0)
        self._super = None
        self._proxy.close()

    def close(self) -> None:
        self.stop()


# -- the sound meters ---------------------------------------------------------------------------------------------------------
class RemoteMetering:
    """`metering.Metering` read from the server, which keeps the probes. Reads are cached for half a second."""

    TTL_S = 0.5

    def __init__(self) -> None:
        self._cache: tuple[float, dict] = (0.0, {})
        self._lock = threading.Lock()

    def _fetch(self) -> dict:
        with self._lock:
            at, got = self._cache
            if got and time.time() - at < self.TTL_S:
                return got
        try:
            got = get_json("/internal/metering")
        except OSError:
            return self._cache[1] or {"public": None, "snapshots": [], "choices": []}
        with self._lock:
            self._cache = (time.time(), got)
        return got

    @property
    def config(self) -> dict:
        return {"meters": [{k: s[k] for k in ("id", "name", "enabled", "lane", "kind", "calibrated", "calibration_db", "warn_db", "limit_db")}
                           for s in self._fetch().get("snapshots") or []]}

    @property
    def running(self) -> bool:
        return any(s.get("state") != "off" for s in self._fetch().get("snapshots") or [])

    def snapshots(self) -> list[dict]:
        return list(self._fetch().get("snapshots") or [])

    def snapshot(self, mid: str) -> dict | None:
        return next((s for s in self.snapshots() if s["id"] == mid), None)

    def public(self) -> dict | None:
        return self._fetch().get("public")

    def choices(self) -> list[tuple[str, str]]:
        return [(k, v) for k, v in self._fetch().get("choices") or []]

    def apply(self, cfg: dict) -> None:           # the server applies its own setting
        return None

    def stop(self) -> None:
        return None


def forward_metering(body: dict) -> None:
    """A module registered, dropped or published a sound source: tell the server, which keeps the readings."""
    try:
        post_json("/internal/metering", body)
    except OSError:
        log.debug("the server did not take a sound source's reading")
