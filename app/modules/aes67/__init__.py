"""AES67 (#266): receives a network audio stream as Relay's input.

AES67 is an open standard (Dante devices in AES67 mode, Ravenna, Livewire). The module finds streams by SAP/SDP
announcements, lists them in a picker, and, once one is chosen, receives its multicast RTP (L16 or L24, 44.1 to 96 kHz)
in Python and hands it to core's audio source interface (app/audio.py). No kernel driver. Dante Virtual Soundcard
cannot send AES67; a Dante device must have AES67 mode on. When the Dante module is on, a stream a Dante device sends
is marked as Dante's.
Venue switches need IGMP snooping set up, or the multicast floods or drops.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission


class AES67(Module):
    manifest = Manifest(
        id="aes67",
        name="AES67",
        version="1.0.0",
        summary="Finds AES67 network audio streams (Dante devices in AES67 mode, Ravenna, Livewire) and receives "
                "one as Relay's input, and sends Relay's alert tones and spoken languages out as one, with no driver "
                "to install.",
        settings_key="aes67",
        section="Sound",
        guide="https://github.com/justin-small/relay#aes67",
        uses=("dante",),
        traffic=(Traffic("aes67", "AES67 stream announcements (SAP) and audio (multicast RTP), in and out", media=True),),
    )
    defaults: dict = {"remembered": {}, "output": {}}
    admin_pages = (AdminPage(
        id="aes67", title="AES67", template="aes67.html",
        icon='<path d="M2 8h2l2-5 3 10 2-5h3"/>'),)
    admin_scripts = ("aes67.js",)
    route_prefixes = ("/api/admin/aes67",)
    permissions = (
        Permission("aes67.view", "See the AES67 streams Relay hears announced and how the chosen one is arriving", "AES67", "viewer"),
        Permission("aes67.manage", "Choose which AES67 stream Relay keeps for its input, and set up and test the stream Relay sends", "AES67", "admin"),
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
        from ... import audio
        from .service import service
        from .source import AES67Source

        from ... import modules as registry
        from ...alerts import alerts
        from .output import AES67Output, output

        audio.register_source(AES67Source(service.find))
        audio.register_output(AES67Output())
        if output.on_alert not in alerts.listeners:
            alerts.listeners.append(output.on_alert)
        registry.provide("aes67.speak", output.speak)

    def detach(self) -> None:
        from ... import audio
        from .source import SOURCE_ID

        from ... import modules as registry
        from ...alerts import alerts
        from .output import OUTPUT_ID, output

        audio.unregister_source(SOURCE_ID)
        audio.unregister_output(OUTPUT_ID)
        if output.on_alert in alerts.listeners:
            alerts.listeners.remove(output.on_alert)
        registry.withdraw("aes67.speak")

    async def start(self) -> None:
        from ... import config
        from .service import service

        from .output import output

        service.configure(config.get().get("aes67") or {})
        service.start()
        output.configure((config.get().get("aes67") or {}).get("output"))

    async def stop(self) -> None:
        from .service import service

        from .output import output

        service.stop()
        output.stop()

    def network_changed(self, resolved) -> None:
        from .output import output
        from .service import service

        service.start()
        if output.settings["enabled"]:
            output.configure(output.settings)

    def health(self) -> Health:
        from .service import service
        s = service.snapshot()
        if s["error"]:
            return Health("error", s["error"])
        usable = [x for x in s["streams"] if x["live"] and not x["problem"]]
        if not usable:
            return Health("warn", "Looking: no AES67 streams announced yet")
        return Health("ok", f"{len(usable)} AES67 streams announced")

    async def test(self) -> dict:
        from .service import service

        s = service.snapshot()
        if s["error"]:
            return {"ok": False, "detail": s["error"]}
        live = [x for x in s["streams"] if x["live"]]
        if not live:
            return {"ok": False, "detail": "No AES67 streams announced yet. Check that the device's AES67 mode is on "
                                           "and that the switch passes multicast (IGMP snooping)."}
        return {"ok": True, "detail": f"{len(live)} streams announced."}


MODULE = AES67()
