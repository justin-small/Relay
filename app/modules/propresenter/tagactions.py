"""What ProPresenter's slide-note tags (slidetags.py) do in Relay: start and stop captioning, switch panels to the
lyrics and back, change the panels' language and raise an alert. Set as the tag actions by the module's attach."""
from __future__ import annotations

from ... import alerts as alertsmod
from ...alerts import alerts
from ...panels import registry as panel_registry
from ...scheduler import scheduler
from ...stream import stream


# Slide-note tags (#138): what each action does in Relay. These are Relay's own doing, so view-only (which is about
# writes to ProPresenter) does not stop them. None of them calls ProPresenter: capture means Relay's own audio and
# captions, never ProPresenter's capture/start.
async def _tag_capture_start(value: str | None) -> str:
    if stream.running:
        return "The stream was already running."
    scheduler.manual_start()
    try:
        await stream.start()
    except RuntimeError as exc:
        raise RuntimeError(f"The stream did not start: {exc}")
    return "The stream started."


async def _tag_capture_stop(value: str | None) -> str:
    if not stream.running:
        return "The stream was already stopped."
    scheduler.manual_stop()
    await stream.stop()
    return "The stream stopped."


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


async def _tag_light(value: str | None) -> str:
    """Fire the lighting cue called `value` on a lighting page, through core's hook: this module never imports
    lighting, and the tag does nothing (and says so) while lighting is off."""
    from .. import fire_cue

    name = (value or "").strip()
    if not name:
        raise RuntimeError("Name the cue after light=, like [relay:light=Walk-in].")
    r = await fire_cue(name, by_name=True, source="a ProPresenter slide tag")
    if not r.get("ok"):
        raise RuntimeError(str(r.get("error") or "The cue was not fired."))
    return f"Lighting cue {name} fired."


async def _tag_obs(value: str | None) -> str:
    """Switch OBS to the scene called `value`, through core's hook: this module never imports obs, and the tag does
    nothing (and says so) while the OBS module is off."""
    from .. import switch_scene

    name = (value or "").strip()
    if not name:
        raise RuntimeError("Name the scene after obs=, like [relay:obs=Message].")
    r = await switch_scene(name, source="a ProPresenter slide tag")
    if not r.get("ok"):
        raise RuntimeError(str(r.get("error") or "OBS did not switch."))
    return f"OBS switched to {name}."


async def _tag_reaper(value: str | None) -> str:
    """Tell REAPER to record, stop, drop a marker or run an action, through core's hook: this module never imports
    reaper. `record`, `stop`, `marker`, `marker=Sermon`, `action=40157`."""
    from .. import control_recording

    command, _, rest = (value or "").strip().partition("=")
    command = command.strip().lower()
    if command not in ("record", "stop", "marker", "action"):
        raise RuntimeError("Say what REAPER should do, like [relay:reaper=record], [relay:reaper=stop] or "
                           "[relay:reaper=marker=Sermon].")
    r = await control_recording(command, rest.strip(), source="a ProPresenter slide tag")
    if not r.get("ok"):
        raise RuntimeError(str(r.get("error") or "REAPER did not answer."))
    return {"record": "REAPER is recording.", "stop": "REAPER stopped recording.",
            "marker": "REAPER marker added.", "action": "REAPER action sent."}[command]


ACTIONS = {"capture-start": _tag_capture_start, "capture-stop": _tag_capture_stop,
           "lyrics": _tag_lyrics, "captions": _tag_captions, "lang": _tag_lang, "alert": _tag_alert,
           "light": _tag_light, "obs": _tag_obs, "reaper": _tag_reaper}
