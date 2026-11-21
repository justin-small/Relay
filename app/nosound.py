"""The "No sound?" walk (#263): a check a volunteer can follow when captions stop.

It starts at Relay's own input and walks upstream, one step at a time, and says in plain words what Relay has already
checked and what the person should look at next. Core owns the first steps (is Relay capturing, is sound arriving);
every module that knows a link further upstream (Dante, a sound desk) adds steps by providing the capability
`nosound.<module id>`, a function `fn(audio: dict) -> list[step]` (see `Step`), and core never names a module.

The walk is read-only and cheap: it reads what Relay already knows and sends nothing. It reports:

* `verdict` "sound" (sound is arriving, so the problem is after Relay's input), "stopped" (a step failed: `stopped_at`
  is its id), "unknown" (input is silent and no step found a fault, so it is the microphone or the desk) or "off" (Relay
  is not capturing);
* `steps`, each `{id, title, state, checked, look}`: state is "ok", "fail", "unknown" (Relay cannot read it) or
  "skipped" (an earlier step already failed, so it was not looked at).
"""
from __future__ import annotations

import logging
from typing import Callable

from . import modules
from .streamstatus import input_state

log = logging.getLogger("relay.nosound")

STATES = ("ok", "fail", "unknown", "skipped")
MAX_STEPS = 12
TEXT_MAX = 200
TITLE_MAX = 60
CAPABILITY = "nosound."


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isprintable()).strip()[:limit]


def step(id: str, title: str, state: str, checked: str, look: str = "") -> dict:
    """One step of the walk. `checked` says what Relay looked at and what it found; `look` says what the person should
    do or look at next, and is only shown when the step failed (or could not be read)."""
    return {"id": _text(id, 40), "title": _text(title, TITLE_MAX), "state": state if state in STATES else "unknown",
            "checked": _text(checked, TEXT_MAX), "look": _text(look, TEXT_MAX)}


def core_steps(audio: dict, running: bool) -> tuple[list[dict], str]:
    """Relay's own steps and the input's coarse state (see streamstatus.input_state): ([steps], state)."""
    seen = input_state(audio, running)
    state, dbfs = seen["state"], seen["dbfs"]
    out: list[dict] = []
    if state == "off":
        out.append(step("capture", "Relay is listening", "fail", "Relay is not capturing sound right now.",
                        "Ask the operator to start Relay, then check again."))
        return out, state
    out.append(step("capture", "Relay is listening", "ok", "Relay is capturing."))
    device = _text(audio.get("device"), TITLE_MAX)
    if state == "lost":
        out.append(step("input", "Sound reaches Relay", "fail",
                        "Relay lost its input" + (f" ({device})" if device else "") + ".",
                        "Check that the input device is plugged in and powered, then check again."))
    elif state == "silent":
        out.append(step("input", "Sound reaches Relay", "fail",
                        f"Relay's input is silent ({dbfs} dB)" + (f" on {device}" if device else "") + ".",
                        "Look further back along the signal, below."))
    else:
        out.append(step("input", "Sound reaches Relay", "ok", f"Sound is arriving ({dbfs} dB)"
                        + (" but it is too loud and clipping." if state == "clipping" else "."),
                        "Turn the level down at the source." if state == "clipping" else ""))
    return out, state


def module_steps(audio: dict, providers: dict[str, Callable[[dict], list[dict]]] | None = None) -> list[dict]:
    """The steps the modules that are on add, in the order of their capability names. A module that fails or answers
    nonsense adds one "unknown" step, so the walk never breaks and never invents a result."""
    found = modules.using_all(CAPABILITY) if providers is None else providers
    out: list[dict] = []
    for name, fn in found.items():
        try:
            got = fn(audio)
        except Exception:  # noqa: BLE001
            log.warning("nosound: %s failed", name, exc_info=True)
            out.append(step(name, name.replace("_", " ").title(), "unknown", "Relay could not read this one.", ""))
            continue
        for s in got if isinstance(got, list) else []:
            if isinstance(s, dict) and len(out) < MAX_STEPS:
                out.append(step(s.get("id", name), s.get("title", name), s.get("state", "unknown"),
                                s.get("checked", ""), s.get("look", "")))
    return out


def walk(audio: dict, running: bool, providers: dict[str, Callable[[dict], list[dict]]] | None = None) -> dict:
    """The whole walk for one reading of the input. Everything after the first failing step is "skipped"."""
    steps, state = core_steps(audio, running)
    if state == "off":
        return {"verdict": "off", "stopped_at": "capture", "steps": steps}
    if state in ("ok", "clipping"):
        return {"verdict": "sound", "stopped_at": "", "steps": steps}
    steps = steps + module_steps(audio, providers)
    # The input step failing is the symptom, not the cause: the cause is the first failing step after it.
    later = steps[2:]
    cause = next((i for i, s in enumerate(later) if s["state"] == "fail"), None)
    if cause is not None:
        for s in later[cause + 1:]:
            s["state"], s["checked"], s["look"] = "skipped", "Not looked at: an earlier step already found the fault.", ""
    if cause is not None:
        return {"verdict": "stopped", "stopped_at": later[cause]["id"], "steps": steps}
    if state == "lost":
        return {"verdict": "stopped", "stopped_at": "input", "steps": steps}
    steps.append(step("source", "The microphone or desk", "unknown",
                      "Relay found nothing wrong between itself and the source.",
                      "Check the microphone is on and not muted, and the sound desk channel is up and not muted."))
    return {"verdict": "unknown", "stopped_at": "source", "steps": steps}


def current() -> dict:
    """The walk for the input as it is right now."""
    from .engine import engine

    audio = engine.capture.status() if engine.capture else {}
    return walk(audio, bool(engine.running))
