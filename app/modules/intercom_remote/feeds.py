"""Main-bus lanes heard in a channel's remote mix (#616): what the operator saved, and how it is doing.

The saved list is `feeds` in this module's settings: `{channel, lane, gain_db}`, one per channel and lane. A feed is
carried only while a remote phone is listening to its channel (the mixer for a channel exists only then), so it is
applied when that channel's mix starts, again when the bridge reconnects, and live when the operator changes the list.
The audio itself runs Rust to Rust (mixbus.py); this file only says which lane goes where.
"""
from __future__ import annotations

import logging
from typing import Any

from ... import config

log = logging.getLogger("relay.intercom_remote")

MAX_FEEDS = 32
GAIN_MIN, GAIN_MAX = -60.0, 12.0
LANES = 32                                  # the main bus's lanes (app.audiobus.LANES) and the mixer's own limit


def clean_list(raw: Any) -> list[dict]:
    """A saved list made safe: whole numbers in range, one entry per channel and lane, the first one kept."""
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            channel, lane = int(item.get("channel")), int(item.get("lane"))
            gain = float(item.get("gain_db", 0.0))
        except (TypeError, ValueError):
            continue
        if channel < 1 or not 1 <= lane <= LANES or gain != gain:
            continue
        if (channel, lane) in seen or len(out) >= MAX_FEEDS:
            continue
        seen.add((channel, lane))
        out.append({"channel": channel, "lane": lane, "gain_db": round(max(GAIN_MIN, min(GAIN_MAX, gain)), 1)})
    return out


def saved() -> list[dict]:
    return clean_list((config.get().get("intercom_remote") or {}).get("feeds"))


def save(feeds: list[dict]) -> None:
    config.save({"intercom_remote": {"feeds": feeds}}, replace=("intercom_remote",))


def _engine():
    from ... import audiobus

    return audiobus.bus.engine


def apply_channel(media: Any, channel_id: int) -> None:
    """Start every saved feed of a channel whose mix has just started."""
    for f in saved():
        if f["channel"] == channel_id:
            _start(media, f)


def _start(media: Any, f: dict) -> None:
    try:
        if not media.feed(f["channel"], _engine(), f["lane"], f["gain_db"]):
            log.warning("intercom_remote: main lane %s was not added to channel %s", f["lane"], f["channel"])
    except Exception:
        log.exception("intercom_remote: could not feed main lane %s into channel %s", f["lane"], f["channel"])


def change(media: Any, listening, old: list[dict], new: list[dict]) -> None:
    """Move the running mixers from the old list to the new one."""
    keep = {(f["channel"], f["lane"]) for f in new}
    for f in old:
        if (f["channel"], f["lane"]) not in keep and media is not None:
            media.unfeed(f["channel"], f["lane"])
    for f in new:
        if media is not None and f["channel"] in listening:
            _start(media, f)


def status(media: Any, listening) -> dict:
    """The mixer's health and each saved feed's counters, for the admin page."""
    mix = getattr(media, "mix", None)
    mixer: dict = {"lanes_total": LANES, "lanes_used": 0, "outputs": []}
    if mix is not None:
        try:
            st = mix.status()
            mixer["lanes_used"] = sum(1 for lane in st.get("lanes", []) if lane.get("patched"))
            mixer["outputs"] = [{"channel": int(str(o["id"])[2:]), "underruns": o.get("underruns", 0),
                                 "resyncs": o.get("resyncs", 0), "queued_ms": round(o.get("queued_ms", 0.0), 1)}
                                for o in st.get("outputs", []) if str(o.get("id", "")).startswith("ch")]
        except Exception:
            log.exception("intercom_remote: could not read the mixer")
    rows = []
    for f in saved():
        row = {**f, "state": "waiting", "carried": 0, "refused": 0, "dropped": 0}
        if f["channel"] in listening and mix is not None:
            counters = mix.feed_status(f["channel"], f["lane"])
            if counters is None:
                row["state"] = "refused"
            else:
                row.update(state="carried", **{k: counters.get(k, 0) for k in ("carried", "refused", "dropped")})
        rows.append(row)
    return {"mixer": mixer, "feeds": rows, "limit": MAX_FEEDS}
