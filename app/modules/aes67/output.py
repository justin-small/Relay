"""AES67 output (#267): Relay's alert tones and spoken languages sent onto the network as one multicast stream.

One stream of up to 8 channels, announced by SAP. Each channel has a gain; one channel carries the alert tone, and each
spoken language can have its own. It is Relay's audio output `aes67_out` (`audio.get_output("aes67_out").play(pcm, rate,
"<channel>")`, channels counted from 1) and a lane-bus sink, so the bus can feed it too. A Dante receiver subscribes to
the announced stream in AES67 mode (Dante Controller, or Relay's Dante module).
"""
from __future__ import annotations

import ipaddress
import logging
from typing import Any

import numpy as np

from ...audio import AudioOutput, LaneSink, SourceError
from .sender import (GAIN_MAX_DB, GAIN_MIN_DB, MAX_CHANNELS, RATE, Mixer, Sender, alert_tone, pcm_to_float, resample,
                     tone)

log = logging.getLogger("relay.aes67")

OUTPUT_ID = "aes67_out"
NAME_MAX = 60
LABEL_MAX = 30
MAX_LANGUAGES = 16
ENCODINGS = ("L24", "L16")
DEFAULTS: dict[str, Any] = {"enabled": False, "name": "Relay", "group": "239.69.67.1", "port": 5004, "channels": 2,
                            "encoding": "L24", "gain_db": [], "labels": [], "alert_channel": 1, "languages": {}}


class Invalid(Exception):
    """Settings refused. `message` is a fixed sentence, safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and lo <= value <= hi else default


def _group_ok(group: Any) -> bool:
    try:
        return ipaddress.IPv4Address(group).is_multicast
    except ValueError:
        return False


def _gain(value: Any) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and GAIN_MIN_DB <= value <= GAIN_MAX_DB
    return round(float(value), 1) if ok else 0.0


def clean(raw: Any) -> dict:
    """The saved output settings, with anything malformed put back to its default so a hand edit cannot stop Relay."""
    raw = raw if isinstance(raw, dict) else {}
    channels = _int(raw.get("channels"), 1, MAX_CHANNELS, DEFAULTS["channels"])
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw["name"].strip() else DEFAULTS["name"]
    group = raw.get("group") if _group_ok(raw.get("group")) else DEFAULTS["group"]
    gains, labels = raw.get("gain_db"), raw.get("labels")
    gains = [_gain(g) for g in gains[:channels]] if isinstance(gains, list) else []
    labels = [str(x).strip()[:LABEL_MAX] if isinstance(x, str) else "" for x in labels[:channels]] \
        if isinstance(labels, list) else []
    langs = {}
    if isinstance(raw.get("languages"), dict):
        for code, ch in raw["languages"].items():
            if isinstance(code, str) and 0 < len(code) <= 12 and isinstance(ch, int) and not isinstance(ch, bool) \
                    and 1 <= ch <= channels and len(langs) < MAX_LANGUAGES:
                langs[code] = ch
    return {"enabled": raw.get("enabled") is True, "name": name.strip()[:NAME_MAX], "group": group,
            "port": _int(raw.get("port"), 1024, 65535, DEFAULTS["port"]), "channels": channels,
            "encoding": raw.get("encoding") if raw.get("encoding") in ENCODINGS else DEFAULTS["encoding"],
            "gain_db": gains + [0.0] * (channels - len(gains)), "labels": labels + [""] * (channels - len(labels)),
            "alert_channel": _int(raw.get("alert_channel"), 0, channels, 1 if channels else 0), "languages": langs}


def validate(raw: Any) -> dict:
    """Settings from admin: refused with a sentence when something is wrong, else cleaned."""
    if not isinstance(raw, dict):
        raise Invalid("The settings must be an object.")
    if not isinstance(raw.get("name", ""), str) or len(str(raw.get("name", ""))) > NAME_MAX:
        raise Invalid(f"The stream name must be text of at most {NAME_MAX} characters.")
    if "group" in raw and not _group_ok(raw["group"]):
        raise Invalid("The address must be an IPv4 multicast address such as 239.69.67.1.")
    if "port" in raw and _int(raw["port"], 1024, 65535, 0) == 0:
        raise Invalid("The port must be 1024 to 65535.")
    if "channels" in raw and _int(raw["channels"], 1, MAX_CHANNELS, 0) == 0:
        raise Invalid(f"The stream carries 1 to {MAX_CHANNELS} channels.")
    if "encoding" in raw and raw["encoding"] not in ENCODINGS:
        raise Invalid("The format must be L24 or L16.")
    for g in raw.get("gain_db") or []:
        if not isinstance(g, (int, float)) or isinstance(g, bool) or not GAIN_MIN_DB <= g <= GAIN_MAX_DB:
            raise Invalid(f"A channel's gain must be {GAIN_MIN_DB:.0f} to +{GAIN_MAX_DB:.0f} dB.")
    out = clean({**DEFAULTS, **raw})
    langs = raw.get("languages")
    if isinstance(langs, dict) and len(out["languages"]) != len(langs):
        raise Invalid("Each language needs a channel inside the stream.")
    if _int(raw.get("alert_channel", 1), 0, out["channels"], -1) == -1:
        raise Invalid("The alert channel must be 0 (none) or a channel inside the stream.")
    return out


class Output:
    """The running output: settings, mixer and sender. One instance (`output`) serves the module."""

    def __init__(self) -> None:
        self.settings = clean({})
        self.mixer: Mixer | None = None
        self.sender: Sender | None = None
        self.error = ""

    # ------------------------------------------------------------------------------------------------ lifecycle
    def configure(self, settings: Any) -> None:
        """Take the settings and (re)start or stop the stream to match them."""
        self.settings = clean(settings)
        self.stop()
        if self.settings["enabled"]:
            self.start()

    def start(self) -> None:
        s = self.settings
        self.mixer = Mixer(s["channels"], s["gain_db"])
        self.sender = Sender(self.mixer, name=s["name"], group=s["group"], port=s["port"], encoding=s["encoding"],
                             labels=s["labels"])
        try:
            self.sender.start()
            self.error = ""
        except OSError as exc:
            self.error = str(exc)
            self.sender = None

    def stop(self) -> None:
        sender, self.sender = self.sender, None
        if sender is not None:
            sender.stop()
        self.mixer = None
        self.error = ""

    @property
    def running(self) -> bool:
        return self.sender is not None and self.sender.running

    # ------------------------------------------------------------------------------------------------ audio in
    def channel_of(self, selector: Any, default: int = 1) -> int:
        """A selector ("2", 2, "" or None) as a 0-based channel, clamped into the stream."""
        try:
            n = int(str(selector).strip()) if selector not in (None, "") else default
        except ValueError:
            n = default
        return min(max(1, n), max(1, self.settings["channels"])) - 1

    def play(self, pcm: bytes, rate: int, selector: Any = None) -> bool:
        """A clip mixed in at once on one channel. False when the stream is not running."""
        mixer = self.mixer
        if mixer is None or not self.running:
            return False
        mixer.overlay(self.channel_of(selector), resample(pcm_to_float(pcm), int(rate)))
        return True

    def speak(self, language: str, pcm: bytes, rate: int) -> bool:
        """Spoken audio for `language`, queued behind what that language's channel is still saying. False when the
        stream is off or the language has no channel."""
        mixer, ch = self.mixer, self.settings["languages"].get(language)
        if mixer is None or ch is None or not self.running:
            return False
        mixer.append(ch - 1, resample(pcm_to_float(pcm), int(rate)))
        return True

    def play_alert(self, level: str) -> bool:
        mixer, ch = self.mixer, self.settings["alert_channel"]
        if mixer is None or not ch or not self.running:
            return False
        mixer.overlay(ch - 1, alert_tone(level))
        return True

    def test_tone(self, channel: int, seconds: float = 2.0, hz: float = 1000.0) -> bool:
        mixer = self.mixer
        if mixer is None or not self.running or not 1 <= channel <= self.settings["channels"]:
            return False
        mixer.overlay(channel - 1, tone(hz, seconds))
        return True

    def on_alert(self, event: dict) -> None:
        """Core's alert listener: a stage alert raised plays its tone. Never raises, never blocks."""
        if isinstance(event, dict) and event.get("event") == "alert.sent":
            try:
                self.play_alert(str(event.get("level") or "info"))
            except Exception:
                log.exception("AES67 alert tone failed")

    def snapshot(self) -> dict:
        s, sender = self.settings, self.sender
        return {"settings": s, "running": self.running, "error": self.error,
                "sdp": sender.sdp if sender else "", "address": sender.address if sender else "",
                "packets": sender.packets if sender else 0, "late": sender.late if sender else 0,
                "levels": self.mixer.peaks_db() if self.mixer else [None] * s["channels"]}


output = Output()


class AES67Output(AudioOutput):
    id = OUTPUT_ID
    label = "AES67 network stream"

    def play(self, pcm: bytes, rate: int = RATE, selector=None) -> None:
        try:
            if not output.play(pcm, rate, selector):
                log.warning("AES67 output is off: clip dropped")
        except Exception as exc:
            log.warning("Could not play audio on AES67: %s", exc)

    def stop(self) -> None:
        if output.mixer is not None:
            output.mixer.clear()

    def lane_sink(self, selector, channels: int) -> LaneSink:
        if not output.running or output.mixer is None:
            raise SourceError("The AES67 output is not running.")
        return AES67Sink(output.mixer, output.channel_of(selector), channels)


class AES67Sink(LaneSink):
    """Lane-bus channels written to consecutive channels of the stream from `first`."""
    rate = RATE

    def __init__(self, mixer: Mixer, first: int, channels: int) -> None:
        self.mixer, self.first, self.channels = mixer, first, min(channels, mixer.channels - first)
        self._open = True

    def free(self) -> int:
        return min(self.mixer.free(self.first + i) for i in range(self.channels)) if self.channels else 0

    def queued(self) -> int:
        return max((self.mixer.queued(self.first + i) for i in range(self.channels)), default=0)

    def write(self, block: np.ndarray) -> int:
        taken = 0
        for i in range(self.channels):
            taken = self.mixer.append(self.first + i, np.ascontiguousarray(block[:, i]))
        return taken

    def close(self) -> None:
        self._open = False

    def alive(self) -> bool:
        return self._open and output.running and output.mixer is self.mixer
