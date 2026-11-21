"""The Dante steps of the "No sound?" walk (#263), from what monitoring (#258) already knows. Sends nothing.

Core asks "nosound.dante" (app/nosound.py) with the input's status. When the input is not a Dante sound card this
adds nothing. When it is, it walks upstream from this computer's Dante device: is it there, is the channel that feeds
Relay subscribed, is the device it listens to on the network, and is the clock locked. A reading Relay does not have
is "unknown", never a guess.
"""
from __future__ import annotations

from ... import netinfo, nosound
from . import inputs


def _local(devices: list[dict]) -> dict | None:
    here = {a for p in netinfo.interfaces() for a in p.addresses}
    return next((d for d in devices if here.intersection(d.get("ips") or [])), None)


def _source_device(source: str) -> str:
    """The device of a `<channel>@<device>` source."""
    return source.rpartition("@")[2].strip()


def steps(audio: dict, devices: list[dict]) -> list[dict]:
    if not inputs.kind_of(str(audio.get("device") or "")):
        return []
    S = nosound.step
    mine = _local(devices)
    if mine is None:
        return [S("dante_here", "Dante on this computer", "fail",
                  "Relay cannot see a Dante device on this computer.",
                  "Open Dante Virtual Soundcard (or Dante Via) on the computer running Relay and check it is started.")]
    out = [S("dante_here", "Dante on this computer", "ok", f"{mine['name']} is on the network.")]
    subs = mine.get("subscriptions")
    if subs is None:
        out.append(S("dante_sub", "What feeds Relay", "unknown", "Relay could not read this computer's Dante subscriptions.",
                     "Open Dante Controller and check that the channel Relay listens to is subscribed."))
        return out
    channel = audio.get("channel")
    by_channel = {s["channel"]: s for s in subs}
    wanted = [by_channel.get(channel + 1)] if isinstance(channel, int) else list(by_channel.values())
    wanted = [s for s in wanted if s is not None]
    subscribed = [s for s in wanted if s["status"] != "none"]
    label = f"receive channel {channel + 1}" if isinstance(channel, int) else "its receive channels"
    if not subscribed:
        out.append(S("dante_sub", "What feeds Relay", "fail", f"{mine['name']} {label} has nothing subscribed to it.",
                     "In Dante Controller, subscribe that channel to the microphone or desk channel it should hear."))
        return out
    broken = next((s for s in subscribed if s["status"] != "ok"), None)
    if broken is not None:
        out.append(S("dante_sub", "What feeds Relay", "fail",
                     f"{mine['name']} receive channel {broken['channel']} cannot find {broken['source'] or 'its source'}.",
                     "Check that device is switched on and connected, or subscribe the channel again."))
        return out
    out.append(S("dante_sub", "What feeds Relay", "ok",
                 "Subscribed to " + ", ".join(s["source"] for s in subscribed[:3]) + "."))
    by_name = {d["name"].lower(): d for d in devices}
    for s in subscribed[:3]:
        name = _source_device(s["source"])
        src = by_name.get(name.lower())
        if src is None:
            out.append(S("dante_source", f"{name} is on the network", "fail", f"Relay cannot see {name}.",
                         f"Check {name} is switched on and its network cable is in."))
            return out
        if not src.get("online"):
            out.append(S("dante_source", f"{name} is on the network", "fail", f"{name} does not answer.",
                         f"Check {name} is switched on and its network cable is in."))
            return out
        out.append(S("dante_source", f"{name} is on the network", "ok", f"{name} answers."))
    unlocked = next((d for d in devices if d.get("clock") and not d["clock"]["locked"]), None)
    if unlocked is not None:
        out.append(S("dante_clock", "Dante clock", "fail", f"{unlocked['name']}'s clock is not locked.",
                     "Open Dante Controller and check the clock leader is on and every device follows it."))
    elif any(d.get("clock") for d in devices):
        out.append(S("dante_clock", "Dante clock", "ok", "Every device that reports its clock is locked."))
    else:
        out.append(S("dante_clock", "Dante clock", "unknown", "Relay could not read any device's clock."))
    return out
