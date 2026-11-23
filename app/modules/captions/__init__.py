"""Captions (#592): live transcription and translation, as one part of the stream.

Start opens core's stream (app/stream.py); while this module is on, captions run with it (unless the operator
turned that off). The module holds the pipeline: the capture, the transcription session and the translator. Core's
engine (app/engine.py) keeps what every page reads and answers with no captions while this module is off.
"""
from __future__ import annotations

from .. import Health, Manifest, Module
from ...users import Permission


class Captions(Module):
    manifest = Manifest(
        id="captions",
        name="Live captions",
        version="1.0.0",
        summary="Transcribes the room's audio and translates it, as one part of Start.",
        settings_key="captions",
        section="Show",
        guide="https://github.com/justin-small/relay#live-captions",
    )
    defaults: dict = {}
    permissions = (
        Permission("captions.view", "See whether live captions are running", "Live captions", "viewer"),
        Permission("captions.manage", "Switch live captions on or off", "Live captions", "admin"),
    )
    _pipeline = None          # made in attach()

    def configured(self, settings: dict) -> bool:
        return True       # Captions were always part of Start: every install keeps them on until switched off.

    def attach(self) -> None:
        from ...engine import engine
        from ...stream import stream
        from .pipeline import Pipeline

        self._pipeline = Pipeline()
        engine.pipeline = self._pipeline
        stream.add_part("captions", self._pipeline.start, self._pipeline.stop, lambda: self._pipeline.running,
                        wanted=stream.captions_wanted)

    def detach(self) -> None:
        from ...engine import engine
        from ...stream import stream

        stream.remove_part("captions")
        engine.pipeline = None

    async def start(self) -> None:
        import asyncio

        if self._pipeline is not None:
            self._pipeline.bind(asyncio.get_running_loop())

    async def stop(self) -> None:
        if self._pipeline is not None:
            await self._pipeline.stop()

    def health(self) -> Health:
        p = self._pipeline
        if p is None:
            return Health("idle", "")
        if p.running:
            return Health("ok", "Running")
        return Health("idle", p.last_error or "Waiting for Start")


MODULE = Captions()
