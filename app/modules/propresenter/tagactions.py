"""What ProPresenter's slide-note tags (slidetags.py) do in Relay: start and stop captioning, switch panels to the
lyrics and back, change the panels' language and raise an alert. Set as the tag actions by the module's attach."""
from __future__ import annotations

from ... import alerts as alertsmod
from ...alerts import alerts
from ...engine import engine
from ...panels import registry as panel_registry
from ...scheduler import scheduler


# Slide-note tags (#138): what each action does in Relay. These are Relay's own doing, so view-only (which is about
# writes to ProPresenter) does not stop them. None of them calls ProPresenter: capture means Relay's own audio and
# captions, never ProPresenter's capture/start.
async def _tag_capture_start(value: str | None) -> str:
    if engine.running:
        return "Captioning was already running."
    scheduler.manual_start()
    try:
        await engine.start()
    except RuntimeError as exc:
        raise RuntimeError(f"Captioning did not start: {exc}")
    return "Captioning started."


async def _tag_capture_stop(value: str | None) -> str:
    if not engine.running:
        return "Captioning was already stopped."
    scheduler.manual_stop()
    await engine.stop()
    return "Captioning stopped."


async def _tag_lyrics(value: str | None) -> str:
    return panel_registry.show_lyrics()


async def _tag_captions(value: str | None) -> str:
    return panel_registry.show_captions()


async def _tag_lang(value: str | None) -> str:
    return panel_registry.show_language(value or "")


async def _tag_alert(value: str | None) -> str:
    try:
        return alerts.raise_named(value or "", "ProPresenter")
    except alertsmod.Refused as exc:
        raise RuntimeError(str(exc))


ACTIONS = {"capture-start": _tag_capture_start, "capture-stop": _tag_capture_stop,
           "lyrics": _tag_lyrics, "captions": _tag_captions, "lang": _tag_lang, "alert": _tag_alert}
