"""The sound desk step of the "No sound?" walk (#263): is the desk channel that feeds Relay muted. Sends nothing.

Core asks "nosound.audio_mixers" (app/nosound.py) with the input's status. The desk is found through the Dante
module's read-only routing ("dante.routing", provided only while that module is on): the receive channel that feeds
Relay is subscribed to `<channel>@<device>`; when that device is one of the desks configured here (its address is the
desk's host, or its name is the desk's name) and the channel's name is a strip on the desk, the step says whether that
strip is muted. A desk Relay cannot hear, a mute it has not heard (Allen & Heath desks say it only when it changes) or
a channel it cannot tie to a strip is "unknown", never a guess. When no configured desk feeds Relay this adds nothing.
"""
from __future__ import annotations

from typing import Any, Callable

from ... import nosound, text


def _feeds(audio: dict, devices: list[dict]) -> list[tuple[dict, str]]:
    """[(Dante device, channel label)] that feed the input's channel, from this computer's own Dante device."""
    from ... import netinfo

    here = {a for p in netinfo.interfaces() for a in p.addresses}
    mine = next((d for d in devices if here.intersection(d.get("ips") or [])), None)
    if mine is None or not isinstance(mine.get("subscriptions"), list):
        return []
    channel = audio.get("channel")
    by_name = {text.fold(d.get("name")): d for d in devices}
    out = []
    for s in mine["subscriptions"]:
        if s.get("status") == "none" or (isinstance(channel, int) and s.get("channel") != channel + 1):
            continue
        label, _, device = str(s.get("source") or "").rpartition("@")
        src = by_name.get(text.fold(device))
        if src is not None and label.strip():
            out.append((src, label.strip()))
    return out


def steps(audio: dict, devices: list[dict], desks: list[Any]) -> list[dict]:
    """`desks` are the service's Desk objects (cfg, mirror, driver)."""
    S = nosound.step
    out: list[dict] = []
    for src, label in _feeds(audio, devices):
        desk = next((d for d in desks if (d.cfg.get("host") and d.cfg["host"] in (src.get("ips") or []))
                     or text.same_name(d.cfg.get("name"), src.get("name"))), None)
        if desk is None:
            continue
        name = desk.cfg["name"]
        title = f"{name} channel is not muted"
        if not desk.driver.connected():
            out.append(S("desk_mute", title, "unknown", f"Relay cannot hear {name} right now.",
                         f"Look at {name}: is the channel \"{label}\" muted?"))
            continue
        strip = next((s for s in desk.mirror.strips.values() if text.same_name(s.shown(), label)), None)
        if strip is None:
            out.append(S("desk_mute", title, "unknown", f"Relay cannot tell which {name} channel is \"{label}\".",
                         f"Look at {name}: is the channel \"{label}\" muted?"))
        elif strip.muted is None:
            out.append(S("desk_mute", title, "unknown", f"{name} has not told Relay whether {strip.shown()} is muted.",
                         f"Look at {name}: is the channel \"{label}\" muted?"))
        elif strip.muted:
            out.append(S("desk_mute", title, "fail", f"{strip.shown()} is muted on {name}.",
                         f"Unmute {strip.shown()} on {name}, or ask the operator to."))
        else:
            out.append(S("desk_mute", title, "ok", f"{strip.shown()} is not muted on {name}."))
        break
    return out


def provider(desks: Callable[[], list[Any]]) -> Callable[[dict], list[dict]]:
    from ... import modules

    def run(audio: dict) -> list[dict]:
        routing = modules.using("dante.routing")
        return steps(audio, routing(), desks()) if routing else []

    return nosound.upstream(20, run)
