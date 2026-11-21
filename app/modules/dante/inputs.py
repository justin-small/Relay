"""Dante inputs in the audio input picker (#264).

Core asks "audio.input_labels" (app/audio.py, `label_inputs`) with the input devices; this answers for the ones that are
Dante sound cards (Dante Virtual Soundcard, Via, AVIO), and for the one running on this computer it says which
Dante channel feeds each of its inputs, read from that device's subscriptions (#258). Where Relay cannot read them
the feed is empty: "no reading", never a guess. Core never names this module.
"""
from __future__ import annotations

from ... import netinfo

DANTE_WORDS = ("dante", "dvs", "avio")
MAX_FEED_CHANNELS = 64


def kind_of(name: str) -> str:
    """"Dante" or "" from a sound card's name."""
    n = name.lower()
    if any(w in n for w in DANTE_WORDS):
        return "Dante"
    return ""


def local_feeds(devices: list[dict]) -> list[str]:
    """For each receive channel of the Dante device that runs on this computer, the source it is subscribed to
    (`<channel>@<device>`), or "" when it has none. [] when no device here is known or readable."""
    here = {a for p in netinfo.interfaces() for a in p.addresses}
    for d in devices:
        subs = d.get("subscriptions")
        if subs and here.intersection(d.get("ips") or []):
            top = min(max(s["channel"] for s in subs), MAX_FEED_CHANNELS)
            feeds = [""] * top
            for s in subs:
                if 1 <= s["channel"] <= top and s["status"] != "none":
                    feeds[s["channel"] - 1] = s["source"]
            return feeds
    return []


def labels(inputs: list[dict], known: list[dict] | None = None) -> dict[int, dict]:
    """{device index: {"kind", "feeds"}} for the input devices that are Dante. `known` is the Dante devices
    Relay has found (the module's service supplies them)."""
    feeds = local_feeds(known or [])
    out: dict[int, dict] = {}
    for dev in inputs:
        kind = kind_of(dev["name"])
        if kind:
            out[dev["index"]] = {"kind": kind, "feeds": feeds}
    return out
