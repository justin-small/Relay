"""Parent paging (#220): a kids worker taps a child on their room's page, and the child's Planning Center Check-Ins
security code goes to ProPresenter for the audience screen.

A module of its own because it joins two other products: the rooms and children come from Planning Center
Check-Ins (with the token saved in the Planning Center module), and the code is shown through a ProPresenter
message (with the connection saved in the ProPresenter module). Both modules must be on.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission


class ParentPaging(Module):
    manifest = Manifest(
        id="parent_paging",
        name="Parent paging",
        version="1.0.0",
        summary="Kids workers page a parent from their room's page: the child's Check-Ins security code goes to a "
                "ProPresenter message, never the child's name.",
        settings_key="parent_paging",
        section="Crew",
        guide="https://github.com/justin-small/relay#parent-paging",
        uses=("planning_center", "propresenter"),
    )
    admin_pages = (AdminPage(
        id="parentpaging", title="Parent paging", template="admin.html",
        icon='<path d="M8 2a3 3 0 0 1 3 3v2l1.5 3h-9L5 7V5a3 3 0 0 1 3-3zM6.5 12a1.5 1.5 0 0 0 3 0"/>'),)
    admin_scripts = ("parentpaging.js",)
    admin_styles = ("parentpaging.css",)
    route_prefixes = ("/api/admin/parent-paging", "/paging")
    permissions = (
        Permission("parent_paging.view", "See the parent pages that are up", "Parent paging", "viewer"),
        Permission("parent_paging.control", "Show and clear parent pages", "Parent paging", "operator"),
        Permission("parent_paging.manage", "Change the parent paging setup and the rooms' links", "Parent paging",
                   "admin"),
    )

    defaults = {"mode": "queue", "message": "Parent Page", "token": "code", "timeout_min": 10, "rooms": []}

    def clean(self, raw: Any) -> dict:
        from .paging import clean_stored

        return clean_stored(raw)

    def public(self, settings: dict) -> dict:
        from .paging import public

        return public(settings)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules
        from .paging import pager

        # ProPresenter provides "propresenter.follower" and "propresenter.api" (#523); asked at each call, so the
        # order the two came on in does not matter, and nothing is paged while it is off.
        def client():
            follower, pp = modules.using("propresenter.follower"), modules.using("propresenter.api")
            return follower.client if follower is not None and pp.configured() else None

        async def call(*args, **kwargs):
            follower = modules.using("propresenter.follower")
            if follower is None:
                raise RuntimeError("ProPresenter is off.")
            return await follower.call(*args, **kwargs)

        pager.client_for = client
        pager.call = call

    def detach(self) -> None:
        from .paging import pager

        pager.client_for = None
        pager.call = None

    async def start(self) -> None:
        from .paging import pager

        await pager.start()

    async def stop(self) -> None:
        from .checkins import cache
        from .paging import pager

        await pager.stop()
        pager.reset()
        cache.clear()

    def health(self) -> Health:
        from .. import modules
        from .paging import pager, settings

        off = [n for mid, n in (("planning_center", "Planning Center"), ("propresenter", "ProPresenter"))
               if not modules.enabled(mid)]
        if off:
            return Health("warn", " and ".join(off) + (" is" if len(off) == 1 else " are") + " off, so nothing can "
                          "be paged")
        if pager.error:
            return Health("warn", pager.error)
        rooms = len(settings()["rooms"])
        if not rooms:
            return Health("idle", "No rooms set up yet")
        up = len(pager.pages)
        return Health("ok", f"{rooms} room{'s' if rooms != 1 else ''}"
                      + (f", {up} page{'s' if up != 1 else ''} up" if up else ""))

    async def test(self) -> dict:
        import asyncio
        import urllib.parse

        from ... import config
        from ... import modules
        from . import checkins, paging

        pc = modules.using("plan.api")
        client = pc.client_from(config.get()) if pc is not None else None
        if client is None:
            return {"ok": False, "detail": "Planning Center is not set up."}
        try:
            rooms = await asyncio.to_thread(checkins.locations, client)
        except checkins.Unavailable as exc:
            return {"ok": False, "detail": exc.message}
        pager = paging.pager
        pp = pager.client_for() if pager.client_for else None
        if pp is None:
            return {"ok": False, "detail": "ProPresenter is not set up."}
        s = paging.settings()
        try:
            msg = await pager.call(pp.read, "/v1/message/" + urllib.parse.quote(s["message"], safe=""))
            paging.with_token(msg, s["token"], "")
        except Exception as exc:  # noqa: BLE001 - shown as a fixed sentence
            return {"ok": False, "detail": paging._pp_refusal(exc)}
        return {"ok": True, "detail": f"Check-Ins has {len(rooms)} room{'s' if len(rooms) != 1 else ''}, and the "
                                      f"ProPresenter message is ready."}


MODULE = ParentPaging()
