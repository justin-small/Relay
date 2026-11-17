"""The NDI module's state (#294): settings, the sources found, the shared receiver pool and the output registry.

**Discovery** runs every `interval` seconds in a thread (mDNS, plus the extra addresses). A source seen in the last
`GONE_AFTER` scans is online; one not seen stays on the list, offline, so a camera that is switched off is still there
when it comes back. Its resolution and frame rate come from the pool: while any listener holds a source's receiver they
are updated from each frame. `version` goes up on every change; the page long-polls it for live updates.

**The pool** has one receiver per source however many listeners use it (viewer, multiview and router all go through
`subscribe`), and closes it when the last listener leaves. Frames reach listeners on the receiver's thread.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import threading
import time
from typing import Any, Callable

from ... import config
from . import discovery, runtime, viewer

log = logging.getLogger("relay.ndi")

KEY = "ndi"
MAX_EXTRA = 20
MAX_GROUPS = 20
MIN_INTERVAL, MAX_INTERVAL = 5, 300
GONE_AFTER = 2           # scans without an answer before a source shows as offline
_GROUP = re.compile(r"[A-Za-z0-9 _.\-]{1,40}")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
MAX_SERVERS = 4
MAX_TALLY = 64
AUDIO_FRESH_S = 2.0      # an audio level older than this is "no audio"
SERVER_PORT = 5959       # NDI Discovery Service's port
# The viewer's limits (#296): key -> (lowest, highest, default).
VIEW_LIMITS = {"view_fps": (1, 30, 15), "view_quality": (20, 90, 60), "view_width": (320, 1920, 1280),
               "view_max": (1, 8, 4)}


def clean_ip(value: Any) -> str:
    try:
        return str(ipaddress.ip_address(value.strip())) if isinstance(value, str) else ""
    except ValueError:
        return ""


def clean_server(value: Any) -> str:
    """`host` or `host:port` (an IPv4 address or a name), else ''. The default port is left off."""
    if not isinstance(value, str):
        return ""
    host, _, port = value.strip().partition(":")
    if not _HOST.fullmatch(host):
        return ""
    if port:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return ""
        return host if int(port) == SERVER_PORT else f"{host}:{int(port)}"
    return host


def split_server(server: str) -> tuple[str, int]:
    host, _, port = server.partition(":")
    return host, int(port) if port else SERVER_PORT


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    ips: list[str] = []
    for v in raw.get("extra_ips") if isinstance(raw.get("extra_ips"), list) else []:
        ip = clean_ip(v)
        if ip and ip not in ips:
            ips.append(ip)
    groups: list[str] = []
    for v in raw.get("groups") if isinstance(raw.get("groups"), list) else []:
        g = " ".join(v.split()) if isinstance(v, str) else ""
        if _GROUP.fullmatch(g) and g.lower() not in [x.lower() for x in groups]:
            groups.append(g)
    iv = raw.get("interval")
    iv = iv if isinstance(iv, int) and not isinstance(iv, bool) else 15
    servers: list[str] = []
    for v in raw.get("servers") if isinstance(raw.get("servers"), list) else []:
        sv = clean_server(v)
        if sv and sv not in servers:
            servers.append(sv)
    mdns = raw.get("mdns") if isinstance(raw.get("mdns"), bool) else True
    out = {"servers": servers[:MAX_SERVERS], "mdns": mdns, "extra_ips": ips[:MAX_EXTRA], "groups": groups[:MAX_GROUPS], "interval": max(MIN_INTERVAL, min(MAX_INTERVAL, iv))}
    for key, (lo, hi, default) in VIEW_LIMITS.items():
        v = raw.get(key)
        out[key] = max(lo, min(hi, v)) if isinstance(v, int) and not isinstance(v, bool) else default
    tally: dict[str, int] = {}
    if isinstance(raw.get("tally"), dict):
        for name, n in raw["tally"].items():
            if isinstance(name, str) and name and len(name) <= 160 and isinstance(n, int) and not isinstance(n, bool) \
                    and 1 <= n <= 999 and len(tally) < MAX_TALLY:
                tally[name] = n
    out["tally"] = tally
    return out


def check_servers(servers: list[str]) -> list[dict]:
    """Is each discovery server answering on its TCP port? (Blocking; run in a thread.)"""
    from ... import netsock

    out = []
    for sv in servers:
        host, port = split_server(sv)
        ok, err = False, ""
        try:
            netsock.connect_tcp(discovery.TRAFFIC, host, port, timeout=1.5).close()
            ok = True
        except netsock.RouteError as exc:
            err = str(exc)[:120]
        except OSError:
            err = "Nothing answered on that address."
        out.append({"server": sv, "reachable": ok, "error": err})
    return out


class Lease:
    """A listener's hold on a source's receiver. `release()` is safe to call twice."""

    def __init__(self, pool: "Pool", source: str, cb: Callable[[dict], None]) -> None:
        self.pool, self.source, self.cb, self.done = pool, source, cb, False

    def release(self) -> None:
        if not self.done:
            self.done = True
            self.pool._release(self)

    __call__ = release


class _Entry:
    def __init__(self, source: str) -> None:
        self.source = source
        self.leases: list[Lease] = []
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.error = ""


class Pool:
    def __init__(self, on_info: Callable[[str, dict], None], on_change: Callable[[], None],
                 on_audio: Callable[[str, float], None] | None = None) -> None:
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._on_info, self._on_change = on_info, on_change
        self._on_audio = on_audio
        self.address_of: Callable[[str], str] = lambda s: ""

    def acquire(self, source: str, cb: Callable[[dict], None]) -> Lease | None:
        backend = runtime.load()
        if backend is None or not isinstance(source, str) or not source:
            return None
        lease = Lease(self, source, cb)
        with self._lock:
            e = self._entries.get(source)
            if e is None:
                e = self._entries[source] = _Entry(source)
                e.thread = threading.Thread(target=self._run, args=(e, backend), name="ndi-recv", daemon=True)
                e.leases.append(lease)
                e.thread.start()
            else:
                e.leases.append(lease)
        self._on_change()
        return lease

    def _release(self, lease: Lease) -> None:
        stop: _Entry | None = None
        with self._lock:
            e = self._entries.get(lease.source)
            if e is None:
                return
            if lease in e.leases:
                e.leases.remove(lease)
            if not e.leases:
                del self._entries[lease.source]
                stop = e
        if stop is not None:
            stop.stop.set()
            if stop.thread is not None and stop.thread is not threading.current_thread():
                stop.thread.join(3)
        self._on_change()

    def _run(self, e: _Entry, backend: Any) -> None:
        conn = None
        try:
            conn = backend.connect(e.source, self.address_of(e.source))
            while not e.stop.is_set():
                frame = conn.receive(500)
                if not frame:
                    continue
                if "data" not in frame:      # an audio level, not a picture: nobody but the viewer's meter wants it
                    if self._on_audio is not None and isinstance(frame.get("audio_db"), (int, float)):
                        self._on_audio(e.source, float(frame["audio_db"]))
                    continue
                self._on_info(e.source, {"width": frame.get("width", 0), "height": frame.get("height", 0),
                                         "fps": frame.get("fps", 0)})
                with self._lock:
                    cbs = [x.cb for x in e.leases]
                for cb in cbs:
                    try:
                        cb(frame)
                    except Exception:
                        log.exception("an NDI frame listener failed")
        except Exception as exc:
            log.info("NDI receiver for %s stopped: %s", e.source, exc)
            e.error = "Could not receive that source."
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {s: len(e.leases) for s, e in self._entries.items()}

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for e in entries:
            e.stop.set()
        for e in entries:
            if e.thread is not None and e.thread is not threading.current_thread():
                e.thread.join(3)


class Service:
    def __init__(self) -> None:
        self.version = 0
        self._seen: dict[str, dict] = {}      # source name -> record (with "missed", "width", "height", "fps")
        self._task: asyncio.Task | None = None
        self._audio: dict[str, tuple[float, float]] = {}   # source -> (peak dBFS, when)
        self.pool = Pool(self._info, self._bump, self._level)
        self.viewers = viewer.Viewers(self.hook_subscribe, self.settings)
        self.pool.address_of = lambda s: self._seen.get(s, {}).get("address", "")
        self.outputs: dict[str, Any] = {}
        self.error = ""
        self.last_scan = 0.0
        self.scanning = False
        self.running = False
        self.servers: list[dict] = []     # per discovery server: {"server", "reachable", "error"}
        self.via_server = 0               # sources the SDK reports through its finder

    def settings(self) -> dict:
        return clean_settings(config.get().get(KEY))

    # --------------------------------------------------------------------------------------------- changes
    def _bump(self) -> None:
        self.version += 1
        try:
            asyncio.get_running_loop()
            self._notify()           # on the event loop already
            return
        except RuntimeError:
            pass
        loop = self._loop            # a receiver or scan thread: hand it to the loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._notify)
            except RuntimeError:
                pass

    _loop: asyncio.AbstractEventLoop | None = None

    def _notify(self) -> None:
        for fut in list(self._waiters):
            if not fut.done():
                fut.set_result(None)
        self._waiters.clear()

    _waiters: list = []

    async def wait_change(self, since: int, timeout: float = 20.0) -> None:
        """Return when the version differs from `since`, or after `timeout`."""
        if self.version != since:
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)

    def _level(self, source: str, db: float) -> None:
        self._audio[source] = (db, time.monotonic())

    def audio_level(self, source: str) -> float | None:
        """The source's latest audio peak in dBFS, or None when it has sent none lately."""
        got = self._audio.get(source)
        return got[0] if got and time.monotonic() - got[1] < AUDIO_FRESH_S else None

    def _info(self, source: str, info: dict) -> None:
        rec = self._seen.get(source)
        if rec is not None and any(rec.get(k) != info[k] for k in info):
            rec.update(info)
            self._bump()

    # ------------------------------------------------------------------------------------------ life cycle
    async def start(self) -> None:
        runtime.configure(self.settings(), config.STATE_DIR / "ndi")
        if runtime.load() is None:
            self.error = runtime.reason()
            self.running = False
            return
        self.error = ""
        self.running = True
        self._loop = asyncio.get_running_loop()
        self._waiters = []
        self._task = asyncio.create_task(self._loop_scan(), name="ndi-scan")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await asyncio.to_thread(self.viewers.close_all)
        await asyncio.to_thread(self.pool.close_all)
        self._audio.clear()
        for o in list(self.outputs.values()):
            o.close()
        self.outputs.clear()
        self._seen.clear()
        self.servers = []
        self._bump()

    async def _loop_scan(self) -> None:
        while True:
            try:
                await self.scan()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("NDI scan failed")
            await asyncio.sleep(self.settings()["interval"])

    async def scan(self) -> None:
        s = self.settings()
        self.scanning = True
        self._bump()
        try:
            found = await asyncio.to_thread(discovery.scan, 2.5, s["extra_ips"]) if s["mdns"] else []
            found = discovery.visible(found, s["groups"])
            self.servers = await asyncio.to_thread(check_servers, s["servers"])
            reported = await asyncio.to_thread(self._find_via_sdk) if (s["servers"] or not s["mdns"]) else []
        finally:
            self.scanning = False
        self.via_server = len(reported)
        have = {f["name"] for f in found}
        found += [r for r in reported if r["name"] not in have]
        self.merge(found)
        self.last_scan = time.time()
        self._bump()

    def _find_via_sdk(self) -> list[dict]:
        """Sources the NDI runtime reports (it talks to the discovery servers), in the same shape as mDNS finds."""
        backend = runtime.load()
        finder = getattr(backend, "find", None)
        if finder is None:
            return []
        try:
            got = finder(3.0)
        except Exception:
            log.info("NDI finder failed", exc_info=True)
            return []
        out = []
        for g in got[:discovery.MAX_FOUND]:
            machine, source = discovery.split_instance(str(g.get("name", "")))
            out.append({"name": str(g["name"])[:160], "machine": machine, "source": source, "host": "",
                        "address": str(g.get("address", ""))[:45], "port": 0, "groups": [], "converter": None})
        return out

    def merge(self, found: list[dict]) -> None:
        names = set()
        for f in found:
            names.add(f["name"])
            old = self._seen.get(f["name"], {})
            self._seen[f["name"]] = {**f, "missed": 0, "width": old.get("width", 0), "height": old.get("height", 0),
                                     "fps": old.get("fps", 0)}
        for n, rec in list(self._seen.items()):
            if n not in names:
                rec["missed"] += 1

    # ----------------------------------------------------------------------------------------------- reads
    def sources(self) -> list[dict]:
        users = self.pool.counts()
        out = []
        for rec in self._seen.values():
            r = {k: v for k, v in rec.items() if k != "missed"}
            r["online"] = rec["missed"] < GONE_AFTER
            r["listeners"] = users.get(rec["name"], 0)
            out.append(r)
        return sorted(out, key=lambda r: (r["machine"].lower(), r["source"].lower()))

    def snapshot(self) -> dict:
        return {"version": self.version, "available": runtime.load() is not None, "error": self.error or runtime.reason(),
                "running": self.running, "scanning": self.scanning, "last_scan": self.last_scan,
                "sources": self.sources(), "settings": self.settings(), "outputs": sorted(self.outputs),
                "servers": self.servers, "via_server": self.via_server,
                "restart_needed": runtime.restart_needed(self.settings())}

    # ------------------------------------------------------------------------------------------------ hooks
    def hook_sources(self) -> list[dict]:
        return self.sources() if self.running else []

    def hook_subscribe(self, source: str, on_frame: Callable[[dict], None]) -> Callable[[], None] | None:
        return self.pool.acquire(source, on_frame) if self.running else None

    def hook_output(self, name: str) -> Any:
        backend = runtime.load()
        name = " ".join(name.split())[:80] if isinstance(name, str) else ""
        if not self.running or backend is None or not name or name in self.outputs:
            return None
        try:
            out = backend.open_sender(name)
        except Exception:
            log.exception("NDI output %s could not open", name)
            return None
        svc = self

        class Output:
            def send(self, frame: dict) -> None:
                out.send(frame)

            def close(self) -> None:
                if svc.outputs.get(name) is self:
                    del svc.outputs[name]
                    svc._bump()
                out.close()

        o = Output()
        self.outputs[name] = o
        self._bump()
        return o


service = Service()
