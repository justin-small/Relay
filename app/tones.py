"""The sounds Relay makes itself: the alert tone for each level and a test tone (#599).

They are played to the bus's named destinations ("Alert tones", "Test tone"), which the operator patches to outputs in the
route editor, so no feature opens a sound device or picks its own speaker. Core listens for an alert sent and plays its
tone; the same tone shapes serve any module that wants one.
"""
from __future__ import annotations

import logging
import math

import numpy as np

from . import audiobus

log = logging.getLogger("relay.tones")

RATE = audiobus.RATE

# One alert level -> beeps (hz, count, seconds each, gap seconds): more and higher as it gets more urgent.
ALERT_TONES = {"info": (880.0, 1, 0.20, 0.0), "attention": (988.0, 2, 0.15, 0.10), "urgent": (1320.0, 3, 0.15, 0.08)}

ALERTS = "alerts"            # the destination keys (audiobus.destinations)
TEST = "test"


def tone(hz: float, seconds: float, level_db: float = -12.0) -> np.ndarray:
    """A sine with 5 ms fades, so it starts and ends without a click. Mono float32 at the bus rate."""
    n = max(1, int(seconds * RATE))
    t = np.arange(n, dtype=np.float32) / RATE
    wave = np.sin(2 * math.pi * hz * t).astype(np.float32) * (10.0 ** (level_db / 20.0))
    fade = min(n // 2, int(0.005 * RATE))
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave


def alert_tone(level: str) -> np.ndarray:
    hz, count, length, gap = ALERT_TONES.get(level, ALERT_TONES["info"])
    beep, rest = tone(hz, length), np.zeros(int(gap * RATE), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i in range(count):
        parts.append(beep)
        if i < count - 1 and rest.size:
            parts.append(rest)
    return np.concatenate(parts)


def play_alert(level: str) -> int:
    """The tone for `level`, to wherever Alert tones is patched. Returns how many output channels it reached."""
    return audiobus.bus.play_to(ALERTS, alert_tone(level), RATE)


def play_test(seconds: float = 2.0, hz: float = 1000.0) -> int:
    """A test tone to wherever Test tone is patched, so an operator can hear a patch. Returns the channels it reached."""
    return audiobus.bus.play_to(TEST, tone(hz, seconds), RATE)


def on_alert(event: dict) -> None:
    """Core's alert listener (alerts.listeners): a stage alert sent plays its tone. Never raises, never blocks."""
    if isinstance(event, dict) and event.get("event") == "alert.sent":
        try:
            play_alert(str(event.get("level") or "info"))
        except Exception:
            log.exception("The alert tone could not be played")
