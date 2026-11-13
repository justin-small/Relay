"""Remote access as a whole: enrolling with Relay's cloud, checking in at start, running the tunnel, releasing.

Settings live under "remote_access" in config.json. The secret and the tunnel token never reach the browser.
Switching the module off stops cloudflared but keeps the address: switching it on again brings the same link back.
Only "Release this address" gives it up.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets

from ... import config, serving
from . import cloud
from .tunnel import tunnel

log = logging.getLogger("relay.remote_access")

KEY = "remote_access"
# Relay's cloud releases an address that hasn't checked in for 90 days, so a box left running checks in this often.
CHECK_IN_EVERY_S = 12 * 3600


def settings() -> dict:
    return config.get()[KEY]


def enrolled(s: dict | None = None) -> bool:
    s = settings() if s is None else s
    return bool(s.get("secret") and s.get("hostname") and s.get("tunnel_token"))


def url(s: dict | None = None) -> str:
    s = settings() if s is None else s
    return f"https://{s['hostname']}" if enrolled(s) else ""


def _version() -> str:
    return os.environ.get("RELAY_VERSION") or "dev"


def _viewer_port() -> int:
    return serving.viewer_port(config.get()["port"])


def _save(**changes) -> None:
    config.save({KEY: {**settings(), **changes}}, replace=(KEY,))


async def _blocking(fn, *args):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


class Service:
    def __init__(self) -> None:
        self.cloud_error = ""        # the kind of the last failed call to the cloud, "" when the last one worked
        self._lock = asyncio.Lock()
        self._beat: asyncio.Task | None = None

    async def begin(self) -> None:
        """At start (and after enrolling): check in, then run the tunnel with the token the cloud holds."""
        async with self._lock:
            s = settings()
            if not enrolled(s):
                await tunnel.stop()
                return
            token = s["tunnel_token"]
            try:
                got = await _blocking(cloud.check_in, s["cloud_url"], s["secret"], _viewer_port(), _version())
            except cloud.CloudError as exc:
                self.cloud_error = exc.kind
                log.warning("Remote access: check-in failed (%s)", exc.kind)
                if exc.kind == "unauthorised":
                    # Released from the cloud side: the token no longer works, so don't keep trying it.
                    await tunnel.stop()
                    return
                # The cloud being unreachable doesn't stop the tunnel: cloudflared talks to Cloudflare, not to us.
            else:
                self.cloud_error = ""
                if got["tunnel_token"] != token or got["hostname"] != s["hostname"]:
                    _save(tunnel_token=got["tunnel_token"], hostname=got["hostname"])
                    token = got["tunnel_token"]
            tunnel.start(token)
        if self._beat is None or self._beat.done():
            self._beat = asyncio.create_task(self._heartbeat(), name="remote-access-check-in")

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(CHECK_IN_EVERY_S)
            try:
                await self.begin()
            except Exception:  # noqa: BLE001  (a failed check-in is logged by begin(); keep the loop alive)
                log.exception("Remote access: check-in loop")

    async def enroll(self, code: str) -> None:
        """Spend an enrollment code. Raises cloud.CloudError."""
        async with self._lock:
            s = settings()
            # The secret is saved before the call, so a retry after a lost reply sends the same one and the
            # cloud answers with the same install instead of refusing a code it already spent.
            secret = s["pending_secret"] or secrets.token_urlsafe(32)
            if secret != s["pending_secret"]:
                _save(pending_secret=secret)
            got = await _blocking(cloud.enroll, s["cloud_url"], code, secret, _viewer_port(), _version())
            _save(secret=secret, pending_secret="", install_id=got["install_id"], hostname=got["hostname"],
                  tunnel_token=got["tunnel_token"])
            self.cloud_error = ""
            log.info("Remote access: enrolled as %s", got["hostname"])
        await self.begin()

    async def release(self) -> None:
        """Give the address up for good. Raises cloud.CloudError unless the cloud has already let it go."""
        async with self._lock:
            s = settings()
            if s["secret"]:
                try:
                    await _blocking(cloud.release, s["cloud_url"], s["secret"])
                except cloud.CloudError as exc:
                    if exc.kind != "unauthorised":
                        raise
            await tunnel.stop()
            _save(secret="", pending_secret="", install_id="", hostname="", tunnel_token="")
            self.cloud_error = ""
            log.info("Remote access: address released")

    async def end(self) -> None:
        beat, self._beat = self._beat, None
        if beat is not None and not beat.done() and beat is not asyncio.current_task():
            beat.cancel()
        await tunnel.stop()


service = Service()
