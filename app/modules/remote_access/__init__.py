"""Remote access (#50): viewers off the venue's network open the caption and translation links over the internet.

Switched on, this install enrolls once with an enrollment code and gets its own address,
https://<random>.relaychurchsystems.com, made by Relay's cloud (relay-cloud). cloudflared then keeps an outbound-only
tunnel from this machine to Cloudflare: no port forwarding, no router changes. The tunnel reaches the viewer port
only; the operator panel is never on it. Switched off, cloudflared stops and the address is kept for next time.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission


def _text(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


class RemoteAccess(Module):
    manifest = Manifest(
        id="remote_access",
        name="Remote access",
        version="1.0.0",
        summary="Gives the viewer links an internet address, so people off the venue's Wi-Fi can follow along.",
        settings_key="remote_access",
        guide="https://github.com/justin-small/relay#remote-access",
    )
    defaults = {"cloud_url": "", "install_id": "", "hostname": "", "secret": "", "tunnel_token": "",
                "pending_secret": ""}
    admin_pages = (AdminPage(
        id="remoteaccess", title="Remote access", template="admin.html", group="Output",
        icon='<circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c2 2 2.5 4 2.5 6S10 12 8 14M8 2C6 4 5.5 6 5.5 8S6 12 8 14"/>'),)
    admin_scripts = ("remoteaccess.js",)
    route_prefixes = ("/api/admin/remote-access",)
    permissions = (
        Permission("remote_access.view", "See the remote viewer link and whether it is connected", "Remote access",
                   "viewer"),
        Permission("remote_access.manage", "Enroll this install or release its internet address", "Remote access",
                   "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .cloud import DEFAULT_URL

        raw = raw if isinstance(raw, dict) else {}
        out = {k: _text(raw.get(k)) for k in self.defaults}
        out["cloud_url"] = out["cloud_url"] or DEFAULT_URL
        return out

    def public(self, settings: dict) -> dict:
        # The secret, the pending secret and the tunnel token stay in config.json.
        from .service import enrolled, url

        return {"enrolled": enrolled(settings), "url": url(settings)}

    def configured(self, settings: dict) -> bool:
        return False  # New in this version: nobody had it set up before modules, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    async def start(self) -> None:
        from .service import service

        await service.begin()

    async def stop(self) -> None:
        from .service import service

        await service.end()

    def health(self) -> Health:
        from .service import enrolled, service, url
        from .tunnel import tunnel

        if not enrolled():
            return Health("warn", "Not set up: enter an enrollment code on the Remote access page")
        if tunnel.state == "missing":
            return Health("error", "cloudflared is missing from this install")
        if service.cloud_error == "unauthorised":
            return Health("error", "The address was released")
        if tunnel.state == "connected":
            return Health("ok", f"Live at {url()}")
        if tunnel.state == "retrying":
            return Health("error", "Can't reach Cloudflare")
        return Health("warn", "Connecting")

    async def test(self) -> dict:
        import asyncio

        from . import cloud
        from .service import settings
        from .tunnel import find_binary

        if not find_binary():
            return {"ok": False, "detail": "cloudflared is missing from this install of Relay. Reinstall Relay."}
        try:
            await asyncio.get_running_loop().run_in_executor(None, cloud.health, settings()["cloud_url"])
        except cloud.CloudError as exc:
            return {"ok": False, "detail": exc.message}
        return {"ok": True, "detail": "Relay's cloud answered, and cloudflared is in place."}

    def status(self) -> dict:
        from .service import url
        from .tunnel import tunnel

        # The public link, once the tunnel is up, for anything that lists the viewer links.
        return {"remote_url": url() if tunnel.state == "connected" else ""}


MODULE = RemoteAccess()
