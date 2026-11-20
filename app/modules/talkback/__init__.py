"""Talkback (#93): the crew's talkback mic, transcribed on a session of its own.

A bus lane (app/audiobus.py) carries the mic; this module listens to it, sends the speech to a transcription backend
(app/transcribe.py) and keeps the text. It is separate from the main caption feed: it starts and stops on its own,
fails on its own, and its text never reaches the transcription or translation feeds, a recording or an export.
Billed as a second stream of audio seconds, shown next to the main feed's.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module
from ...users import Permission


class Talkback(Module):
    manifest = Manifest(
        id="talkback",
        name="Talkback",
        version="1.0.0",
        summary="Transcribes the crew's talkback mic on a session of its own, apart from the caption feed.",
        settings_key="talkback",
        section="Sound",
        guide="https://github.com/justin-small/relay#talkback",
    )
    defaults = {"lane": 0, "backend": "", "source_language": ""}
    admin_pages = (AdminPage(
        id="talkback", title="Talkback", template="talkback.html",
        icon='<path d="M3 3h10v7H8l-3 3v-3H3z"/>'),)
    admin_scripts = ("talkback.js",)
    route_prefixes = ("/api/admin/talkback",)
    permissions = (
        Permission("talkback.view", "See the talkback text and what it costs", "Talkback", "operator"),
        Permission("talkback.control", "Start, stop and restart talkback transcription", "Talkback", "operator"),
        Permission("talkback.manage", "Choose the talkback lane, backend and language", "Talkback", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import audio, modules
        from .service import TalkbackLaneSource, service

        audio.register_source(TalkbackLaneSource())
        # What a later screen needs to show the text: the newest lines, and a way to hear each new one.
        modules.provide("talkback.lines", {"recent": service.recent, "subscribe": service.subscribe})

    def detach(self) -> None:
        from ... import audio, modules
        from .service import SOURCE_ID

        audio.unregister_source(SOURCE_ID)
        modules.withdraw("talkback.lines")

    async def start(self) -> None:
        import asyncio

        from .service import service

        service.bind(asyncio.get_running_loop())

    async def stop(self) -> None:
        from .service import service

        await service.shutdown()

    def health(self) -> Health:
        from .service import service

        return Health(*service.health())

    async def test(self) -> dict:
        from .service import Refused, service

        try:
            _cfg, backend, warning, _language, lane = service._plan()
        except Refused as exc:
            return {"ok": False, "detail": str(exc)}
        return {"ok": True, "detail": f"Ready: lane {lane} through {backend.label}." + (f" {warning}" if warning else "")}


MODULE = Talkback()
