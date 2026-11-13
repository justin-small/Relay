"""Runs cloudflared for this install's tunnel and keeps it running while the module is on.

cloudflared ships beside the frozen backend (tools/fetch_cloudflared.py puts it there at build time); nothing is
downloaded at run time. The tunnel token goes to cloudflared through its environment (TUNNEL_TOKEN), never on the
command line, where any local user could read it from the process list.

The tunnel's ingress is set in Relay's cloud and points at this machine's viewer port only, so the operator panel is
never reachable through it. Relay refuses admin paths on the viewer port as well.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
import time
from pathlib import Path

log = logging.getLogger("relay.remote_access")

EXE = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"
BACKOFF_S = (2, 5, 10, 30, 60)
STOP_WAIT_S = 5


def find_binary() -> str | None:
    """RELAY_CLOUDFLARED, else the copy shipped beside the frozen backend, else one on PATH (a source checkout)."""
    override = os.environ.get("RELAY_CLOUDFLARED")
    if override:
        return override if Path(override).is_file() else None
    if getattr(sys, "frozen", False):
        beside = Path(sys.executable).resolve().parent / EXE
        if beside.is_file():
            return str(beside)
    return shutil.which("cloudflared")


class Tunnel:
    """One cloudflared process, restarted with back-off when it exits."""

    def __init__(self) -> None:
        self.state = "off"           # off | missing | connecting | connected | retrying
        self.connections = 0         # edge connections cloudflared reports as registered
        self.since = 0.0             # when the state last changed
        self._token = ""
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None

    def _set(self, state: str) -> None:
        if state != self.state:
            self.state, self.since = state, time.time()
            log.info("Remote access tunnel: %s", state)

    def start(self, token: str) -> None:
        if self._task and not self._task.done() and token == self._token:
            return
        self._token = token
        self.cancel_sync()
        self._task = asyncio.create_task(self._run(), name="remote-access-tunnel")

    def cancel_sync(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._kill()
        self.connections = 0
        self._set("off")

    async def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), STOP_WAIT_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    async def _run(self) -> None:
        attempt = 0
        try:
            while True:
                binary = find_binary()
                if not binary:
                    self._set("missing")
                    log.warning("Remote access: cloudflared is not installed beside Relay")
                    return
                self._set("connecting" if attempt == 0 else "retrying")
                started = time.monotonic()
                await self._once(binary)
                # A run that stayed up a while was healthy: start the back-off again from the beginning.
                attempt = 0 if time.monotonic() - started > 120 else attempt + 1
                self.connections = 0
                self._set("retrying")
                await asyncio.sleep(BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)])
        except asyncio.CancelledError:
            await self._kill()
            raise

    async def _once(self, binary: str) -> None:
        env = {**os.environ, "TUNNEL_TOKEN": self._token}
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x0800_0000  # CREATE_NO_WINDOW
        try:
            self._proc = await asyncio.create_subprocess_exec(
                binary, "tunnel", "--no-autoupdate", "--loglevel", "info", "run",
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, env=env, **kwargs)
        except OSError as exc:
            log.warning("Remote access: cloudflared would not start (%s)", exc.__class__.__name__)
            return
        assert self._proc.stderr is not None
        async for raw in self._proc.stderr:
            self.observe(raw.decode("utf-8", "replace"))
        await self._proc.wait()
        log.info("Remote access: cloudflared exited with %s", self._proc.returncode)
        self._proc = None

    def observe(self, line: str) -> None:
        """Follow cloudflared's log: it says when each edge connection registers and drops."""
        if "Registered tunnel connection" in line:
            self.connections += 1
            self._set("connected")
        elif "Unregistered tunnel connection" in line or "Connection terminated" in line:
            self.connections = max(0, self.connections - 1)
            if self.connections == 0:
                self._set("retrying")
        if " ERR " in line:
            # The level and the start of the message only: cloudflared's lines can carry addresses and ids.
            log.warning("cloudflared: %s", line.strip()[:200])


tunnel = Tunnel()
