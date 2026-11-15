"""Runs a local livekit-server for the intercom (#238) and keeps it running while the module is on.

LiveKit publishes Linux and Windows builds only, no macOS one. On Linux and Windows Relay downloads the pinned
release once, on first switch-on, checks its SHA-256 and keeps it in the state folder; after that nothing is
fetched, so intercom works with no internet. On macOS the operator installs livekit-server (for instance
`brew install livekit`) and gives its path in the module's settings.

Everything stays on the LAN: no TURN, no STUN, no cloud. LiveKit's signalling port is bound to 127.0.0.1; media
goes to one UDP port and one TCP port. The API key and secret are made here on first start and kept in the state
folder (0600); they never reach the admin page or a phone.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile
from collections import deque
from pathlib import Path

from ... import netinfo, paths
from . import winjob

log = logging.getLogger("relay.intercom")

VERSION = "1.13.11"
BASE_URL = f"https://github.com/livekit/livekit/releases/download/v{VERSION}/"
# (system, machine) -> (archive, sha256 from the release's checksums.txt). macOS has no entry: LiveKit does not
# publish one, so the operator supplies a path.
ASSETS = {
    ("linux", "x86_64"): ("livekit_%s_linux_amd64.tar.gz" % VERSION,
                          "1e9ee90ca301e1842b2887fc77cb122759756a9c8aeb4334ee91de3325ec43fb"),
    ("linux", "aarch64"): ("livekit_%s_linux_arm64.tar.gz" % VERSION,
                           "2af6ce091d6b3c79d1677f8ff1bbb91a8007f40b2325c059b52d38383c0830b6"),
    ("windows", "amd64"): ("livekit_%s_windows_amd64.zip" % VERSION,
                           "5d071ea43339f0140d24a66f0b2ff8a7bf6c4d0dceb19b30abca835670213667"),
    ("windows", "arm64"): ("livekit_%s_windows_arm64.zip" % VERSION,
                           "47ebfaa1ef6de546bf17a6699d4d9be59693bd5c3ffbec14d0749bd396ea3875"),
}
EXE = "livekit-server.exe" if sys.platform == "win32" else "livekit-server"
SIGNAL_PORT = 7880          # bound to 127.0.0.1 only
TCP_PORT = 7881
UDP_PORT = 7882
KEY_NAME = "relay"
HEALTH_EVERY_S = 5
FAILS_TO_RESTART = 3
STARTUP_GRACE_S = 15        # a server still starting is not counted as failing
GIVE_UP_AFTER = 5           # restarts ...
GIVE_UP_WINDOW_S = 600      # ... in this many seconds
STOP_WAIT_S = 5
LOG_LINES = 50
BACKOFF_S = (1, 2, 5, 10)
MAX_DOWNLOAD = 60 * 1024 * 1024


class LiveKitError(Exception):
    """A failure with a fixed sentence the admin page may show (nothing from the network or the process)."""

    def __init__(self, text: str):
        super().__init__(text)
        self.message = text


def state_folder() -> Path:
    return paths.state_dir() / "livekit"


def platform_key() -> tuple[str, str]:
    return sys.platform.replace("win32", "windows"), platform.machine().lower()


def asset() -> tuple[str, str] | None:
    return ASSETS.get(platform_key())


def find_binary(configured: str = "") -> str | None:
    """RELAY_LIVEKIT, else the operator's path, else the copy Relay downloaded."""
    for cand in (os.environ.get("RELAY_LIVEKIT", ""), configured):
        if cand:
            return cand if Path(cand).is_file() else None
    mine = state_folder() / EXE
    return str(mine) if mine.is_file() else None


def download(base_url: str = BASE_URL, wanted: tuple[str, str] | None = None) -> str:
    """Fetch the pinned release, check its SHA-256, and unpack livekit-server into the state folder.

    Blocking: call it from a thread. Raises LiveKitError. The archive is held in memory and checked before any of
    it is read, and only the one member named livekit-server is written, so nothing else in it can land on disk."""
    wanted = wanted or asset()
    if wanted is None:
        raise LiveKitError("LiveKit publishes no build for this computer. Install livekit-server and give its path.")
    name, want = wanted
    try:
        with urllib.request.urlopen(base_url + name, timeout=60) as r:    # noqa: S310  (a fixed https URL)
            blob = r.read(MAX_DOWNLOAD + 1)
    except OSError:
        raise LiveKitError("Could not download LiveKit. Check the internet connection, then switch intercom on "
                           "again.") from None
    if len(blob) > MAX_DOWNLOAD or hashlib.sha256(blob).hexdigest() != want:
        raise LiveKitError("The LiveKit download did not match its checksum, so it was thrown away.")
    data = extract(name, blob)
    folder = state_folder()
    paths.make_private_dir(folder)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".livekit-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o755)
        os.replace(tmp, folder / EXE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    log.info("intercom: livekit-server %s installed", VERSION)
    return str(folder / EXE)


def extract(name: str, blob: bytes) -> bytes:
    """The livekit-server file from a .tar.gz or .zip, by exact base name; nothing else is read."""
    try:
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                for info in z.infolist():
                    if os.path.basename(info.filename) == EXE and info.file_size <= MAX_DOWNLOAD:
                        return z.read(info)
        else:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
                for m in t.getmembers():
                    if m.isfile() and os.path.basename(m.name) == EXE and m.size <= MAX_DOWNLOAD:
                        f = t.extractfile(m)
                        if f is not None:
                            return f.read()
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        pass
    raise LiveKitError("The LiveKit download did not hold livekit-server.")


# ------------------------------------------------------------------ keys and config

def write_private(file: Path, text: str) -> None:
    """Write `file` owner-only (0600), replacing it whole."""
    tmp = file.with_name(file.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, file)


def keys() -> tuple[str, str]:
    """The API key and secret, made on first use and kept in the state folder (0600). Never shown or sent out."""
    folder = state_folder()
    file = folder / "keys.json"
    try:
        got = json.loads(file.read_text(encoding="utf-8"))
        if isinstance(got.get("key"), str) and isinstance(got.get("secret"), str) and got["secret"]:
            return got["key"], got["secret"]
    except (OSError, ValueError, AttributeError):
        pass
    paths.make_private_dir(folder)
    pair = {"key": KEY_NAME, "secret": secrets.token_urlsafe(32)}
    write_private(file, json.dumps(pair))
    return pair["key"], pair["secret"]


def lan_address(configured: str = "") -> str:
    if configured:
        return configured
    ips = netinfo.lan_ips()
    return ips[0] if ips else "127.0.0.1"


def config_text(node_ip: str, key: str, secret: str, webhook_url: str = "") -> str:
    """livekit.yaml. JSON strings are valid YAML scalars, so nothing needs escaping by hand."""
    q = json.dumps
    hook = ["webhook:", f"  api_key: {q(key)}", "  urls:", f"    - {q(webhook_url)}"] if webhook_url else []
    return "\n".join([
        f"port: {SIGNAL_PORT}",
        "bind_addresses:",
        '  - "127.0.0.1"',
        "rtc:",
        f"  tcp_port: {TCP_PORT}",
        f"  udp_port: {UDP_PORT}",
        "  use_external_ip: false",
        f"  node_ip: {q(node_ip)}",
        "room:",
        "  auto_create: false",
        "  empty_timeout: 10",
        "keys:",
        f"  {q(key)}: {q(secret)}",
        *hook,
        "logging:",
        "  level: info",
        "",
    ])


def webhook_url() -> str:
    """Where LiveKit posts its events: Relay's own plain-HTTP viewer port on this computer, never off it."""
    from ... import config, serving

    return f"http://127.0.0.1:{serving.viewer_port(config.get()['port'])}/intercom/livekit-webhook"


def write_config(node_ip: str) -> Path:
    key, secret = keys()
    folder = state_folder()
    paths.make_private_dir(folder)
    file = folder / "livekit.yaml"
    write_private(file, config_text(node_ip, key, secret, webhook_url()))
    return file


# ------------------------------------------------------------------ ports

def busy_port(host: str = "127.0.0.1") -> str:
    """A sentence naming the first port LiveKit needs that something else holds, else ''.

    Probed on `host` (the address LiveKit advertises), never on every interface."""
    for kind, port in (("tcp", SIGNAL_PORT), ("tcp", TCP_PORT), ("udp", UDP_PORT)):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
        try:
            if kind == "tcp" and sys.platform != "win32":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # connections just closed do not count
            s.bind(("127.0.0.1" if port == SIGNAL_PORT else host, port))
        except OSError:
            return f"Port {port}/{kind} is already in use by another program. Close it, then restart LiveKit."
        finally:
            s.close()
    return ""


def ping(port: int, timeout: float = 2.0) -> bool:
    """LiveKit answers GET / with `OK` when it is up. Blocking: call it from a thread."""
    try:
        # No proxy: an http_proxy in the environment must never carry a check of this computer's own port.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/", timeout=timeout) as r:   # noqa: S310
            return r.read(16).strip() == b"OK"
    except OSError:
        return False


# ------------------------------------------------------------------ the supervisor

class Supervisor:
    """One livekit-server process: started, health-checked every 5 s, restarted with back-off, never orphaned."""

    def __init__(self) -> None:
        self.state = "off"       # off | downloading | starting | running | restarting | missing | error
        self.detail = ""
        self.since = 0.0
        self.started_at = 0.0
        self.restarts: deque[float] = deque()
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self.settings: dict = {}
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._secret = ""

    def _set(self, state: str, detail: str = "") -> None:
        if state != self.state:
            self.since = time.time()
            log.info("intercom: livekit %s", state)
        self.state, self.detail = state, detail

    def begin(self, settings: dict) -> None:
        self.settings = settings
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="intercom-livekit")

    async def end(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._kill()
        self.restarts.clear()
        self._set("off")

    async def restart(self) -> None:
        """The admin's Restart: stop the process, clear the give-up count, and let the loop start it again."""
        self.restarts.clear()
        await self._kill()
        if self._task is None or self._task.done():
            self.begin(self.settings)

    # -------------------------------------------------------------- the loop
    async def _run(self) -> None:
        try:
            binary = find_binary(self.settings.get("binary_path", ""))
            if binary is None:
                binary = await self._fetch()
                if binary is None:
                    return
            fails = 0
            while True:
                if self._proc is None or self._proc.returncode is not None:
                    if self._gave_up():
                        self._set("error", "LiveKit stopped five times in ten minutes, so Relay gave up. "
                                           "Look at the log below, then press Restart LiveKit.")
                        return
                    if self.restarts:        # a start after a stop: wait a little longer each time
                        await asyncio.sleep(BACKOFF_S[min(len(self.restarts) - 1, len(BACKOFF_S) - 1)])
                    if not await self._launch(binary):
                        return
                    self.restarts.append(time.monotonic())
                    fails = 0
                await asyncio.sleep(HEALTH_EVERY_S)
                ok = self._proc is not None and self._proc.returncode is None and await asyncio.to_thread(
                    ping, SIGNAL_PORT)
                if ok:
                    fails = 0
                    self._set("running", "")
                    continue
                if self._proc is not None and self._proc.returncode is None and \
                        time.time() - self.started_at < STARTUP_GRACE_S:
                    continue
                fails += 1
                if self._proc is None or self._proc.returncode is not None or fails >= FAILS_TO_RESTART:
                    log.warning("intercom: livekit_restarted")
                    self._set("restarting", "LiveKit stopped answering. Starting it again.")
                    await self._kill()
        except asyncio.CancelledError:
            await self._kill()
            raise
        except Exception:
            log.exception("intercom: livekit supervisor failed")
            self._set("error", "LiveKit could not be started. See Relay's own log.")
            await self._kill()

    def _gave_up(self) -> bool:
        now = time.monotonic()
        while self.restarts and now - self.restarts[0] > GIVE_UP_WINDOW_S:
            self.restarts.popleft()
        return len(self.restarts) > GIVE_UP_AFTER

    async def _fetch(self) -> str | None:
        if os.environ.get("RELAY_LIVEKIT_NO_DOWNLOAD") or asset() is None:     # the variable is for tests
            self._set("missing", "LiveKit publishes no build for this computer. Install livekit-server (on a Mac: "
                                 "brew install livekit) and enter its path under Settings.")
            return None
        self._set("downloading", "Downloading LiveKit. This happens once.")
        try:
            return await asyncio.to_thread(download)
        except LiveKitError as exc:
            self._set("missing", exc.message)
            return None

    async def _launch(self, binary: str) -> bool:
        node_ip = lan_address(self.settings.get("node_ip", ""))
        busy = await asyncio.to_thread(busy_port, node_ip)
        if busy:
            self._set("error", busy)
            return False
        self._set("starting", "Starting LiveKit.")
        self._secret = keys()[1]
        cfg = write_config(node_ip)
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x0800_0200   # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True      # its own process group, so stop reaches any children
        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary, "--config", str(cfg), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, **kwargs)
        except OSError:
            self._set("error", "LiveKit would not start. Check that the file is livekit-server for this computer.")
            return False
        self.started_at = time.time()
        if sys.platform == "win32" and not winjob.attach(self._proc.pid):
            log.warning("intercom: livekit is not tied to Relay's job object; a hard crash of Relay may leave it")
        _live.add(self)
        asyncio.create_task(self._read(self._proc), name="intercom-livekit-log")
        return True

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if self._secret:
                line = line.replace(self._secret, "***")
            self.log.append(line[:300])

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        _signal(proc.pid, False)
        try:
            await asyncio.wait_for(proc.wait(), STOP_WAIT_S)
        except asyncio.TimeoutError:
            _signal(proc.pid, True)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), STOP_WAIT_S)
        self.started_at = 0.0

    # -------------------------------------------------------------- what the page and health read
    def view(self) -> dict:
        return {"state": self.state, "detail": self.detail,
                "uptime_s": int(time.time() - self.started_at) if self.state == "running" and self.started_at else 0,
                "node_ip": lan_address(self.settings.get("node_ip", "")),
                "ports": {"signal": SIGNAL_PORT, "tcp": TCP_PORT, "udp": UDP_PORT},
                "version": VERSION, "binary": bool(find_binary(self.settings.get("binary_path", ""))),
                "can_download": asset() is not None, "log": list(self.log)}


def _signal(pid: int, hard: bool) -> None:
    """End the process and anything it started: its whole group on Unix, the process tree on Windows."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL if hard else signal.SIGTERM)


# A process that outlives Relay is an orphan holding Relay's ports, so a hard exit kills what is still running.
_live: "set[Supervisor]" = set()


def _atexit() -> None:
    for sup in list(_live):
        proc = sup._proc
        if proc is not None and proc.returncode is None:
            _signal(proc.pid, True)


import atexit  # noqa: E402

atexit.register(_atexit)

supervisor = Supervisor()
