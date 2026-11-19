"""Native capture: PortAudio in, 24 kHz / 16-bit / mono PCM out.

PortAudio gives us ASIO + WASAPI on Windows and Core Audio on macOS from one
codebase. We capture at the interface's native rate, downsample with soxr, and
push every chunk -- silence included -- to each registered consumer.

Per the PRD: no silence gating on the path to the API. The RMS level computed
here drives the admin meter and the cosmetic "speaking" indicator only.
"""
from __future__ import annotations

import asyncio
import logging
import collections
import math
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd
import soxr

log = logging.getLogger("relay.audio")

TARGET_RATE = 24000
# A stream that delivers nothing for this long has lost its device (an unplugged interface). The pump then
# tries to re-open the saved input every RETRY_EVERY_S until it is back.
LOST_AFTER_S = 2.0
RETRY_EVERY_S = 2.0
# Fixed text for the admin page while the input is gone; it names no device.
INPUT_LOST_SENTENCE = ("The audio input stopped. Check the interface is plugged in; Relay reconnects "
                       "on its own when it comes back.")
CHUNK_MS = 40  # ~1920 bytes per send at 24 kHz mono PCM16
# Frames of speech to keep buffered ahead of a detected onset, so gating never
# clips the start of a word (the risk the continuous-send PRD note called out).
PREROLL_FRAMES = 8  # 8 x 40 ms = 320 ms
SPEAKING_RMS = 0.012
SPEAKING_HOLD_S = 0.6

# -- meter ------------------------------------------------------------------
# A sample at or above this is treated as clipped. Float32 input from PortAudio
# is nominally [-1, 1]; some drivers overshoot, so this catches both the rail
# and anything past it.
CLIP_LEVEL = 0.999
# How long a clip stays latched on the panel. Clipping is transient by nature --
# a single hot consonant is a handful of samples -- and an indicator that only
# lives for one 1 Hz poll would be missed.
CLIP_HOLD_S = 3.0
# Peak marker fallback, dB per second. Fast enough to track a speaker, slow
# enough to read.
PEAK_DECAY_DB_PER_S = 20.0
# Floor of the meter scale. Below this, the reading is "-inf" for the operator.
DBFS_FLOOR = -60.0


def dbfs(linear: float) -> float:
    """Linear amplitude (0..1) as dBFS, floored rather than -inf.

    Every audio operator reads dBFS; the 0-1 bar this replaced is the least
    informative view of an input.
    """
    if not linear or linear <= 0.0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20.0 * math.log10(min(1.0, float(linear))))


# PortAudio's final matching Pa_Terminate() closes every stream still open, so
# the terminate/initialise pair below is destructive while a capture is live.
# The list is therefore cached, and the re-init is skipped whenever a stream is
# open -- see list_input_devices().
_device_cache: list[dict] | None = None
# How many PortAudio input streams are open: the main capture, and every device the lane bus (audiobus.py)
# has open. The device list is re-probed only when this is zero.
_capture_count = 0
_capture_lock = threading.Lock()


def _capture_opened() -> None:
    global _capture_count
    with _capture_lock:
        _capture_count += 1


def _capture_closed() -> None:
    global _capture_count
    with _capture_lock:
        _capture_count = max(0, _capture_count - 1)


def _set_capture_live(flag: bool) -> None:
    """Test helper: pretend one stream is open (True) or none are (False)."""
    global _capture_count
    with _capture_lock:
        _capture_count = 1 if flag else 0


def capture_is_live() -> bool:
    return _capture_count > 0


def _number_repeats(devices: list[dict]) -> list[dict]:
    """Two identical interfaces report the same name, and the label is both
    what the dropdown shows and what the config saves. Number the repeats so
    each one can be told apart and picked; the first keeps the plain label so
    configs saved before this still resolve to it."""
    seen: dict[str, int] = {}
    for d in devices:
        seen[d["label"]] = n = seen.get(d["label"], 0) + 1
        if n > 1:
            d["label"] = f"{d['label']} #{n}"
    return devices


def _probe_input_devices(reinit: bool) -> list[dict]:
    """Ask PortAudio for the device table. `reinit` restarts the backend so
    newly-connected hardware shows up -- and closes any open stream with it."""
    if reinit:
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # pragma: no cover - PortAudio re-init is best effort
            pass
    apis = sd.query_hostapis()
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        api = apis[dev["hostapi"]]["name"] if dev["hostapi"] < len(apis) else "?"
        out.append(
            {
                "index": idx,
                "name": dev["name"],
                "hostapi": api,
                "label": f"{api}: {dev['name']}",
                "channels": dev["max_input_channels"],
                "default_samplerate": int(dev["default_samplerate"] or 0),
            }
        )
    return _number_repeats(out)


def list_input_devices(refresh: bool = False) -> list[dict]:
    """Every input-capable device, with its host API (ASIO / Core Audio / ...).

    Served from cache unless `refresh` is asked for, so merely opening the admin
    panel never touches PortAudio. A refresh while a capture is running re-reads
    the device table but leaves the backend alone, which keeps the live stream
    open at the cost of not seeing hardware plugged in since startup.
    """
    global _device_cache
    if _device_cache is not None and not refresh:
        return _device_cache
    _device_cache = _probe_input_devices(reinit=not capture_is_live())
    _report_input_channels(_device_cache)
    return _device_cache


MAX_CHANNELS_PER_DEVICE = 32


def _report_input_channels(devices: list[dict]) -> None:
    """Each input channel of each device PortAudio lists is an audio source channel in core (#542). A device that is gone
    turns missing there, with any name an operator gave it."""
    from . import channels

    found = [{"key": f"{d['label']}#{n}", "kind": "audio", "direction": "source", "number": n,
              "gear_name": f"{d['name']} {n}"}
             for d in devices for n in range(1, min(d["channels"], MAX_CHANNELS_PER_DEVICE) + 1)]
    channels.report("portaudio", found)


def _default_device(devices: list[dict]) -> dict:
    try:
        default_idx = sd.default.device[0]
    except Exception:
        default_idx = None
    for d in devices:
        if d["index"] == default_idx:
            return d
    return devices[0]


# What the admin page says when a saved device was named for the old PulseAudio bridge. A fixed
# sentence: it names no device, so nothing from PortAudio or the config reaches the page.
LEGACY_PULSE_NOTICE = ("The saved audio input was set up for the old PulseAudio bridge, so Relay is using "
                       "this computer's default input instead. Pick an input from the list and save to "
                       "keep your choice.")
_LEGACY_PULSE_NAMES = ("alsa: pulse", "alsa: default", "pulse", "default")


def is_legacy_pulse(selector) -> bool:
    """True for a saved device named for the PulseAudio bridge ("PulseAudio: <source>", or the
    two ALSA devices that stood for the server's default input)."""
    sel = str(selector or "").strip().lower()
    return sel.startswith("pulseaudio:") or sel in _LEGACY_PULSE_NAMES


def selection_notice(selector, refresh: bool = False) -> str | None:
    """LEGACY_PULSE_NOTICE when `selector` is a PulseAudio-era name that no real device carries,
    so capture would fall back to the default input; None otherwise."""
    if not is_legacy_pulse(selector):
        return None
    sel = str(selector).strip().lower()
    for d in list_input_devices(refresh=refresh):
        if d["label"].lower() == sel or d["name"].lower() == sel:
            return None
    return LEGACY_PULSE_NOTICE


# Added after a failed open, by operating system: a blocked microphone and a busy device look the
# same to PortAudio, so the sentence names the OS's switch without claiming which it was. Fixed text.
MIC_BLOCKED_HINTS = {
    "darwin": "If macOS blocked microphone access, open System Settings > Privacy & Security > Microphone "
              "and switch on the app that started Relay (Terminal, or the Relay app).",
    "win32": "If Windows blocked microphone access, open Settings > Privacy & security > Microphone and "
             "allow desktop apps to use the microphone.",
    "linux": "If the system blocked the input, check the sound server (PipeWire or PulseAudio) is running "
             "and that your user can use the sound card.",
}


def mic_blocked_hint() -> str:
    return MIC_BLOCKED_HINTS.get("darwin" if sys.platform == "darwin" else "win32" if sys.platform == "win32"
                                 else "linux", "")


def resolve_device(selector, refresh: bool = False) -> dict | None:
    """Match a saved device by 'API: Name' label, bare name, or index."""
    devices = list_input_devices(refresh=refresh)
    if not devices:
        return None
    if selector is None or selector == "":
        return _default_device(devices)
    if isinstance(selector, int) or (isinstance(selector, str) and selector.isdigit()):
        idx = int(selector)
        for d in devices:
            if d["index"] == idx:
                return d
        return None
    sel = str(selector).strip().lower()
    for d in devices:
        if d["label"].lower() == sel:
            return d
    for d in devices:
        if d["name"].lower() == sel:
            return d
    for d in devices:
        if sel in d["label"].lower():
            return d
    # Saved by a v2.0 install, which reached the sound card through a PulseAudio bridge (an
    # imported config): those names mean nothing to PortAudio's own host APIs, so use the default input.
    if is_legacy_pulse(selector):
        return _default_device(devices)
    return None


# -- sources and outputs ----------------------------------------------------
# Audio comes in through an AudioSource and goes out through an AudioOutput. Core registers PortAudio for
# both; a module adds its own (a network stream, a network sender) with register_source / register_output
# while it is on and removes it again when it is switched off, so core never imports a network client.
PORTAUDIO = "portaudio"


class SourceError(RuntimeError):
    """A source could not be opened. The message is a fixed sentence fit for the admin page: it must name
    no secret, and no text from the library or the network."""


@dataclass
class SourceInfo:
    """What an open source tells the pump: the rate of the blocks it delivers, and what to show."""
    rate: int                      # samples per second of the blocks passed to `deliver`
    device: dict                   # at least {"label": str}; PortAudio adds its channel count and index
    channel: int | str = 0         # the channel in use, or "mix"; shown in the status


class AudioSource:
    """Where capture audio comes from. The pump (AudioCapture) owns resampling, the meter, the silence
    gate, reconnecting and the consumers; a source only delivers frames.

    `open` starts delivery: from any thread, call `deliver(block)` with each block as a mono float32 numpy
    array in -1..1 at `SourceInfo.rate`. `deliver` never blocks and drops when the pump is behind, so a
    source need not buffer for it."""

    id: str = ""
    label: str = ""

    def open(self, selector, channel: int | str, deliver: Callable[[np.ndarray], None]) -> SourceInfo:
        """Start delivering. `selector` and `channel` are the saved `audio_device` and `input_channel`.
        Raises SourceError with a fixed sentence when it cannot start."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop delivering. Safe to call when it is not open, and to call again."""

    def alive(self) -> bool:
        """False when the source knows it has lost its input. A source that cannot tell returns True: the
        pump also treats LOST_AFTER_S without a block as lost."""
        return True

    def notice(self, selector) -> str | None:
        """A fixed sentence when the saved selection cannot be used as named, else None."""
        return None

    def lane_input(self) -> "LaneInput":
        """A new, separate input for the lane bus. The default wraps `open` as one mono lane; a source that can
        be open more than once, or has several channels, overrides this."""
        return MonoSourceInput(self)


# -- lanes (the 32x32 bus, audiobus.py) -----------------------------------------
# The bus runs every lane at LANE_RATE. An input delivers whole multichannel blocks, and an output accepts them,
# so one open device feeds every lane patched to it.
LANE_RATE = 48000
MAX_LANES = 32


class LaneInput:
    """One open multichannel input. `open` starts delivering; `deliver(block)` takes a float32 array shaped
    (frames, channels) at `SourceInfo.rate` and must never block (call it from any thread)."""

    def open(self, selector, deliver: Callable[[np.ndarray], None]) -> SourceInfo:
        """`SourceInfo.device["channels"]` is the channel count. Raises SourceError with a fixed sentence."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop delivering. Safe to call when not open, and again."""

    def alive(self) -> bool:
        return True

    @property
    def dropped(self) -> int:
        """Frames lost at the device hand-off since it opened (a ring that was not read in time)."""
        return 0


class MonoSourceInput(LaneInput):
    """Any registered AudioSource as one lane: the source's own mono channel, so a module that only
    implements `open` can feed the bus without more code."""

    def __init__(self, source: "AudioSource") -> None:
        self.source = source

    def open(self, selector, deliver) -> SourceInfo:
        info = self.source.open(selector, 0, lambda block: deliver(np.asarray(block, dtype=np.float32).reshape(-1, 1)))
        return SourceInfo(rate=info.rate, device={**info.device, "channels": 1}, channel=0)

    def close(self) -> None:
        self.source.close()

    def alive(self) -> bool:
        return self.source.alive()


class LaneSink:
    """One open multichannel output, paced by its own device clock. The bus writes `free()` frames at a time
    (never more) as float32 (frames, channels) at `rate`, so a sink never blocks the bus and a slow sink
    cannot back up a fast one."""

    rate: int = LANE_RATE
    channels: int = 0

    def free(self) -> int:
        """Frames that `write` takes now."""
        raise NotImplementedError

    def queued(self) -> int:
        """Frames written and not yet played."""
        raise NotImplementedError

    def write(self, block: np.ndarray) -> int:
        """Queue the frames; returns how many were taken."""
        raise NotImplementedError

    def close(self) -> None:
        """Stop and release the device. Safe to call again."""

    def alive(self) -> bool:
        return True

    @property
    def underruns(self) -> int:
        """Times the device wanted audio and the queue was empty."""
        return 0


class AudioOutput:
    """Where audio Relay plays goes (alert tones, spoken translation): a sound device, or a module's
    network sender. `play` queues one clip and returns at once."""

    id: str = ""
    label: str = ""

    def play(self, pcm: bytes, rate: int = TARGET_RATE, selector=None) -> None:
        """Play mono 16-bit little-endian PCM at `rate` Hz on the device `selector` names (None: the default).
        Never blocks the caller and raises nothing: a clip that cannot play is logged and dropped."""
        raise NotImplementedError

    def stop(self) -> None:
        """Cut off whatever is playing and release the device."""

    def lane_sink(self, selector, channels: int) -> "LaneSink":
        """A new multichannel sink for the lane bus. Raises SourceError when this output cannot take lanes."""
        raise SourceError("This output cannot be used for lanes.")


_sources: dict[str, AudioSource] = {}
_outputs: dict[str, AudioOutput] = {}


def _register(table: dict, item, kind: str) -> None:
    if not item.id or not re.fullmatch(r"[a-z][a-z0-9_]*", item.id):
        raise ValueError(f"an audio {kind} needs a lower_snake_case id")
    if item.id == PORTAUDIO and not isinstance(item, (PortAudioSource, PortAudioOutput)):
        raise ValueError(f"the id {PORTAUDIO!r} belongs to core")
    table[item.id] = item


def register_source(source: AudioSource) -> None:
    _register(_sources, source, "source")


def unregister_source(source_id: str) -> None:
    if source_id != PORTAUDIO:
        _sources.pop(source_id, None)


def get_source(source_id: str | None) -> AudioSource | None:
    return _sources.get(source_id or PORTAUDIO)


def sources() -> list[AudioSource]:
    """Every registered source, PortAudio first."""
    return sorted(_sources.values(), key=lambda s: (s.id != PORTAUDIO, s.id))


def register_output(output: AudioOutput) -> None:
    _register(_outputs, output, "output")


def unregister_output(output_id: str) -> None:
    if output_id != PORTAUDIO:
        _outputs.pop(output_id, None)


def get_output(output_id: str | None) -> AudioOutput | None:
    return _outputs.get(output_id or PORTAUDIO)


def outputs() -> list[AudioOutput]:
    return sorted(_outputs.values(), key=lambda o: (o.id != PORTAUDIO, o.id))


class PortAudioSource(AudioSource):
    """A PortAudio input device: the only source core registers."""

    id = PORTAUDIO
    label = "Sound device"

    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()

    def open(self, selector, channel, deliver) -> SourceInfo:
        self.close()
        # Nothing of ours is open here, so a full hardware re-probe is safe and picks up anything plugged in
        # since the last look.
        dev = resolve_device(selector, refresh=True)
        if dev is None:
            raise SourceError("No input device matched the saved selection.")
        rate = dev["default_samplerate"] or 48000
        channels = dev["channels"]
        # A stereo board feed is usually better summed than halved: taking one
        # channel discards whatever is only on the other. "mix" averages every
        # channel -- averaging, not adding, so a correlated pair cannot clip the
        # downmix that the source channels did not already clip.
        mix = isinstance(channel, str) and channel.strip().lower() == "mix"
        if mix and channels < 2:
            mix = False  # nothing to mix; fall through to channel 0
        if mix:
            pick = -1
        else:
            try:
                pick = int(channel)
            except (TypeError, ValueError):
                pick = 0
            pick = min(max(0, pick), channels - 1)
        blocksize = max(64, int(rate * 0.01))  # ~10 ms; PortAudio may round up

        def callback(indata, frames, time_info, status):
            if status:
                log.debug("PortAudio status: %s", status)
            deliver(indata.mean(axis=1, dtype=np.float32) if mix else indata[:, pick].copy())

        try:
            stream = sd.InputStream(
                device=dev["index"],
                channels=channels,
                samplerate=rate,
                blocksize=blocksize,
                dtype="float32",
                callback=callback,
            )
            stream.start()
        except Exception as exc:
            # PortAudio's own text goes to the log; the admin page gets a fixed sentence about the device.
            log.warning("Could not open %s: %s", dev["label"], exc)
            raise SourceError(f"Could not open {dev['label']}. Check it is plugged in and not in use by another "
                              "app. " + mic_blocked_hint()) from exc
        with self._lock:
            self._stream = stream
        _capture_opened()
        return SourceInfo(rate=int(rate), device=dev, channel="mix" if mix else pick)

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            _capture_closed()
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def alive(self) -> bool:
        s = self._stream
        return s is not None and bool(getattr(s, "active", True))

    def notice(self, selector) -> str | None:
        return selection_notice(selector)

    def lane_input(self) -> "LaneInput":
        return PortAudioLaneInput()


def list_output_devices() -> list[dict]:
    """Every output-capable PortAudio device. Never re-initialises the backend, so it is safe while a
    capture is live."""
    apis = sd.query_hostapis()
    out = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["max_output_channels"] < 1:
            continue
        api = apis[dev["hostapi"]]["name"] if dev["hostapi"] < len(apis) else "?"
        out.append({"index": idx, "name": dev["name"], "hostapi": api, "label": f"{api}: {dev['name']}",
                    "channels": dev["max_output_channels"]})
    return _number_repeats(out)


class PortAudioOutput(AudioOutput):
    """A PortAudio output device: plays each clip on its own short-lived stream, off the caller's thread."""

    id = PORTAUDIO
    label = "Sound device"

    def __init__(self) -> None:
        self._playing: set[threading.Thread] = set()
        self._cut = threading.Event()

    def _device(self, selector) -> int | None:
        if selector in (None, ""):
            return None
        sel = str(selector).strip().lower()
        devs = list_output_devices()
        for d in devs:
            if sel.isdigit() and d["index"] == int(sel):
                return d["index"]
        for key in ("label", "name"):
            for d in devs:
                if d[key].lower() == sel:
                    return d["index"]
        return None

    def play(self, pcm: bytes, rate: int = TARGET_RATE, selector=None) -> None:
        def run() -> None:
            try:
                data = np.frombuffer(pcm, dtype="<i2").reshape(-1, 1)
                with sd.OutputStream(device=self._device(selector), samplerate=int(rate), channels=1,
                                     dtype="int16") as stream:
                    step = max(1, int(rate * 0.05))
                    for i in range(0, len(data), step):
                        if self._cut.is_set():
                            break
                        stream.write(data[i:i + step])
            except Exception as exc:
                log.warning("Could not play audio: %s", exc)
            finally:
                self._playing.discard(threading.current_thread())

        self._cut.clear()
        t = threading.Thread(target=run, name="play", daemon=True)
        self._playing.add(t)
        t.start()

    def stop(self) -> None:
        self._cut.set()
        for t in list(self._playing):
            t.join(timeout=1.0)

    def lane_sink(self, selector, channels: int) -> "LaneSink":
        return PortAudioLaneSink(self._device(selector), selector, channels)


# -- PortAudio lanes: C-callback ring buffers (rtmixer) -------------------------------------------------------
# rtmixer's C callback and PortAudio's lock-free ring buffer keep Python off the audio thread. Where rtmixer is
# not installed the same classes fall back to a plain sounddevice callback, so Relay still runs, with the
# Python callback holding the GIL on every block.
try:  # pragma: no cover - depends on the install
    import rtmixer
except Exception:  # pragma: no cover
    rtmixer = None


def _ring_frames(rate: int) -> int:
    """About a second of frames, as the power of two PortAudio's ring buffer needs."""
    return 1 << max(10, int(rate).bit_length())


class PortAudioLaneInput(LaneInput):
    """Every channel of one PortAudio input device, opened once however many lanes use it."""

    def __init__(self) -> None:
        self._stream = None
        self._ring = None
        self._action = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._counted = False
        self._channels = 0
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def open(self, selector, deliver) -> SourceInfo:
        self.close()
        dev = resolve_device(selector, refresh=True)
        if dev is None:
            raise SourceError("No input device matched the saved selection.")
        rate = int(dev["default_samplerate"] or LANE_RATE)
        channels = int(dev["channels"])
        self._channels = channels
        self._stop.clear()
        try:
            if rtmixer is not None:
                self._open_ring(dev, rate, channels, deliver)
            else:
                self._open_callback(dev, rate, channels, deliver)
        except Exception as exc:
            log.warning("Could not open %s for lanes: %s", dev["label"], exc)
            self.close()
            raise SourceError(f"Could not open {dev['label']}. Check it is plugged in and not in use by another "
                              "app. " + mic_blocked_hint()) from exc
        _capture_opened()
        self._counted = True
        return SourceInfo(rate=rate, device=dev, channel="all")

    def _open_ring(self, dev, rate, channels, deliver) -> None:
        rec = rtmixer.Recorder(device=dev["index"], channels=channels, samplerate=rate, latency="low")
        ring = rtmixer.RingBuffer(4 * channels, _ring_frames(rate))
        self._stream, self._ring = rec, ring
        rec.start()
        self._action = rec.record_ringbuffer(ring)
        self._reader = threading.Thread(target=self._read, args=(deliver,), name="lane-in", daemon=True)
        self._reader.start()

    def _read(self, deliver) -> None:
        ring, channels = self._ring, self._channels
        while not self._stop.wait(0.005):
            n = ring.read_available
            if n:
                data = np.frombuffer(ring.read(n), dtype=np.float32).reshape(-1, channels).copy()
                deliver(data)

    def _open_callback(self, dev, rate, channels, deliver) -> None:
        def callback(indata, frames, time_info, status):
            if status:
                log.debug("PortAudio status: %s", status)
            deliver(indata.copy())

        self._stream = sd.InputStream(device=dev["index"], channels=channels, samplerate=rate,
                                      blocksize=max(64, int(rate * 0.01)), dtype="float32", callback=callback)
        self._stream.start()

    def alive(self) -> bool:
        s = self._stream
        return s is not None and bool(getattr(s, "active", True))

    def close(self) -> None:
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=1.0)
            self._reader = None
        stream, self._stream = self._stream, None
        if stream is not None:
            for call in ("abort", "close"):
                try:
                    getattr(stream, call)()
                except Exception:
                    pass
        self._ring = self._action = None
        if self._counted:
            self._counted = False
            _capture_closed()


class _PyRing:
    """A small frame ring for the fallback sink: one writer thread, one reader (the device callback)."""

    def __init__(self, frames: int, channels: int) -> None:
        self.buf = np.zeros((frames, channels), dtype=np.float32)
        self.size, self.channels = frames, channels
        self.r = self.w = 0
        self.lock = threading.Lock()

    @property
    def read_available(self) -> int:
        return self.w - self.r

    @property
    def write_available(self) -> int:
        return self.size - (self.w - self.r)

    def write(self, block: np.ndarray) -> int:
        with self.lock:
            n = min(len(block), self.write_available)
            self.buf[(self.w + np.arange(n)) % self.size] = block[:n]
            self.w += n
            return n

    def read(self, n: int) -> np.ndarray:
        with self.lock:
            n = min(n, self.read_available)
            idx = (self.r + np.arange(n)) % self.size
            out = self.buf[idx].copy()
            self.r += n
            return out


class PortAudioLaneSink(LaneSink):
    """A multichannel PortAudio output device, played from a ring buffer the bus fills."""

    def __init__(self, index: int | None, selector, channels: int) -> None:
        self.channels = int(channels)
        self._underruns = 0
        self._stream = None
        self._ring = None
        self._action = None
        try:
            info = sd.query_devices(index, "output") if index is not None else sd.query_devices(kind="output")
            index = info["index"] if "index" in info else index
            have = int(info["max_output_channels"])
            if have < self.channels:
                raise SourceError(f"{info['name']} has {have} output channels, fewer than the {self.channels} asked for.")
            self.rate = LANE_RATE
            frames = _ring_frames(self.rate)
            if rtmixer is not None:
                self._stream = rtmixer.Mixer(device=index, channels=self.channels, samplerate=self.rate,
                                             latency="low")
                self._ring = rtmixer.RingBuffer(4 * self.channels, frames)
                self._stream.start()
                self._action = self._stream.play_ringbuffer(self._ring)
            else:
                self._ring = _PyRing(frames, self.channels)

                def callback(outdata, frame_count, time_info, status):
                    block = self._ring.read(frame_count)
                    outdata[: len(block)] = block
                    if len(block) < frame_count:
                        outdata[len(block):] = 0
                        self._underruns += 1

                self._stream = sd.OutputStream(device=index, channels=self.channels, samplerate=self.rate,
                                               dtype="float32", callback=callback)
                self._stream.start()
        except SourceError:
            self.close()
            raise
        except Exception as exc:
            log.warning("Could not open the output device for lanes: %s", exc)
            self.close()
            raise SourceError("Could not open the output device. Check it is plugged in and not in use by "
                              "another app.") from exc

    def free(self) -> int:
        return self._ring.write_available if self._ring is not None else 0

    def queued(self) -> int:
        return self._ring.read_available if self._ring is not None else 0

    def write(self, block: np.ndarray) -> int:
        ring = self._ring
        if ring is None or not len(block):
            return 0
        n = min(len(block), ring.write_available)
        if isinstance(ring, _PyRing):
            return ring.write(block[:n])
        return int(ring.write(np.ascontiguousarray(block[:n], dtype=np.float32), n))

    def alive(self) -> bool:
        s = self._stream
        return s is not None and bool(getattr(s, "active", True))

    @property
    def underruns(self) -> int:
        return self._underruns

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            for call in ("abort", "close"):
                try:
                    getattr(stream, call)()
                except Exception:
                    pass
        self._ring = self._action = None


register_source(PortAudioSource())
register_output(PortAudioOutput())


class AudioCapture:
    """Owns the pump from an AudioSource and fans identical PCM to every consumer."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.source: AudioSource | None = None
        self._raw: queue.Queue = queue.Queue(maxsize=200)
        self._pump: threading.Thread | None = None
        self._stop = threading.Event()
        self._consumers: set[asyncio.Queue] = set()

        self.device: dict | None = None
        self.native_rate: int = 0
        self.running = False
        self.error: str | None = None
        self.notice: str | None = None   # a saved device that could not be used as named
        self.reconnecting = False        # the input vanished (unplugged?) and is being re-opened
        self._selector = None            # what start() was given, so a reconnect finds the same input
        self._channel_arg: int | str = 0
        self._last_block_ts = 0.0
        self._stream_lock = threading.Lock()
        self.level: float = 0.0
        self.speaking: bool = False
        self.channel: int | str = 0
        # Meter state. `peak` is the current block's peak; `_peak_hold` is the
        # decaying marker the panel draws. Clipping is counted (cumulative,
        # since the last start) and latched for CLIP_HOLD_S so a transient is
        # visible at the panel's 1 Hz poll rate.
        self.peak: float = 0.0
        self._peak_hold: float = 0.0
        self._peak_ts: float = 0.0
        self.clipped_samples: int = 0
        self._clip_until: float = 0.0
        self._last_voice_ts: float = 0.0
        self._last_audio_ts: float | None = None
        # Frames dropped at the driver->resampler hand-off when the raw queue
        # backs up. Correct to drop (never block the driver thread), but a
        # silent drop looks identical to a healthy feed -- so count it.
        self.dropped_capture = 0

    # -- consumers ----------------------------------------------------
    def add_consumer(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        q.dropped = 0  # frames evicted for this consumer when its uplink stalls
        self._consumers.add(q)
        return q

    def remove_consumer(self, q: asyncio.Queue) -> None:
        self._consumers.discard(q)

    # -- lifecycle ----------------------------------------------------
    def _deliver(self, block: "np.ndarray") -> None:
        """Called by the source, on any thread, with each mono float32 block."""
        try:
            self._raw.put_nowait(block)
        except queue.Full:
            self.dropped_capture += 1  # drop rather than block the source's thread

    def _open_source(self, source: AudioSource, selector, channel) -> SourceInfo:
        """Open `source`; on failure put its fixed sentence in self.error and raise RuntimeError."""
        try:
            info = source.open(selector, channel, self._deliver)
        except SourceError as exc:
            self.error = str(exc)
            raise RuntimeError(self.error) from exc
        self._last_block_ts = time.time()
        return info

    def start(self, selector, channel: int | str = 0, source: str = PORTAUDIO) -> dict:
        self.stop()
        src = get_source(source)
        if src is None:
            self.error = "That audio source is not available. Check its module is switched on."
            raise RuntimeError(self.error)

        self.notice = src.notice(selector)
        self._selector, self._channel_arg = selector, channel
        self.source = src
        try:
            info = self._open_source(src, selector, channel)
        except RuntimeError:
            self.source = None
            raise

        self.device = info.device
        self.native_rate = int(info.rate)
        self.channel = info.channel
        self.error = None
        self.dropped_capture = 0
        self.peak = 0.0
        self._peak_hold = 0.0
        self._peak_ts = 0.0
        self.clipped_samples = 0
        self._clip_until = 0.0
        self.running = True
        self._stop.clear()
        self._pump = threading.Thread(target=self._run_pump, name="resample", daemon=True)
        self._pump.start()
        log.info("Capture started on %s @ %d Hz (%s)", info.device.get("label", src.label), info.rate,
                 "mix of %d ch" % info.device["channels"] if info.channel == "mix"
                 else "ch %s" % info.channel)
        return self.device

    def stop(self) -> None:
        self._stop.set()
        with self._stream_lock:
            if self.source is not None:
                try:
                    self.source.close()
                except Exception:
                    log.exception("audio source %s did not close cleanly", self.source.id)
        if self._pump is not None:
            self._pump.join(timeout=2.0)
            self._pump = None
        self.source = None
        while not self._raw.empty():
            try:
                self._raw.get_nowait()
            except queue.Empty:
                break
        self.running = False
        self.reconnecting = False
        self.level = 0.0
        self.speaking = False
        self.peak = 0.0
        self._peak_hold = 0.0
        self._clip_until = 0.0

    # -- resample + fan-out -------------------------------------------
    def _run_pump(self) -> None:
        # Silence gating: the transcription model hallucinates confident text
        # (often in unrelated languages) when fed a room's noise floor, so by
        # default only audio around detected speech is sent. PREROLL_FRAMES of
        # lead-in are replayed on onset so word starts are not clipped. Set
        # realtime.gate_silence = false to restore the old continuous send.
        gate = getattr(self, "gate_silence", True)
        preroll: "collections.deque[bytes]" = collections.deque(maxlen=PREROLL_FRAMES)
        resampler = soxr.ResampleStream(
            self.native_rate, TARGET_RATE, 1, dtype="float32", quality="HQ"
        )
        chunk_samples = int(TARGET_RATE * CHUNK_MS / 1000)
        pending = np.zeros(0, dtype=np.float32)

        while not self._stop.is_set():
            try:
                block = self._raw.get(timeout=0.25)
                self._last_block_ts = time.time()
                if self.reconnecting:
                    self._recovered()
            except queue.Empty:
                if self._input_lost():
                    if self._reconnect():
                        # The new device may run at another rate; start the resampler over for it.
                        resampler = soxr.ResampleStream(self.native_rate, TARGET_RATE, 1, dtype="float32",
                                                        quality="HQ")
                        pending = np.zeros(0, dtype=np.float32)
                continue

            rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0
            now = time.time()
            self.level = rms
            if rms >= SPEAKING_RMS:
                self._last_voice_ts = now
            self.speaking = (now - self._last_voice_ts) < SPEAKING_HOLD_S
            self._meter(block, now)

            try:
                out = resampler.resample_chunk(block)
            except Exception as exc:  # pragma: no cover
                log.warning("resample failed: %s", exc)
                continue
            if out.size == 0:
                continue
            pending = np.concatenate([pending, out.astype(np.float32, copy=False)])

            while pending.size >= chunk_samples:
                frame, pending = pending[:chunk_samples], pending[chunk_samples:]
                pcm = np.clip(frame, -1.0, 1.0)
                pcm = (pcm * 32767.0).astype("<i2").tobytes()
                self._last_audio_ts = now
                if not gate or self.speaking:
                    while preroll:
                        self.loop.call_soon_threadsafe(self._dispatch, preroll.popleft())
                    self.loop.call_soon_threadsafe(self._dispatch, pcm)
                else:
                    preroll.append(pcm)

    # -- unplug / replug ------------------------------------------------
    def _input_lost(self) -> bool:
        """No audio for LOST_AFTER_S, or PortAudio says the stream stopped: the device went away."""
        src = self.source
        dead = src is None or not src.alive()
        return dead or (time.time() - self._last_block_ts) > LOST_AFTER_S

    def _recovered(self) -> None:
        self.reconnecting = False
        self.error = None
        log.info("Audio input is back on %s", self.device["label"] if self.device else "the same device")

    def _reconnect(self) -> bool:
        """Try once to re-open the input that was lost; runs on the pump thread, which calls it again
        every RETRY_EVERY_S while the input stays gone. Captions stay up in between: viewers see no
        new text, and the admin page says the input is missing. True once a stream is open again."""
        if not self.reconnecting:
            log.warning("Audio input lost on %s; waiting for it to come back",
                        self.device["label"] if self.device else "the device")
        self.reconnecting = True
        self.error = INPUT_LOST_SENTENCE
        self.level = 0.0
        self.speaking = False
        if self._stop.wait(timeout=RETRY_EVERY_S):
            return False
        with self._stream_lock:
            src = self.source
            if src is None:
                return False
            src.close()                     # nothing is open now, so a PortAudio backend can be re-probed
            try:
                while not self._raw.empty():
                    self._raw.get_nowait()
                info = self._open_source(src, self._selector, self._channel_arg)
            except (RuntimeError, queue.Empty):
                self.error = INPUT_LOST_SENTENCE      # the source's own sentence would hide that it is a replug wait
                return False
            if self._stop.is_set():
                src.close()
                return False
            self.device, self.native_rate, self.channel = info.device, int(info.rate), info.channel
        return True

    # -- meter ---------------------------------------------------------
    def _meter(self, block: "np.ndarray", now: float) -> None:
        """Peak, peak-hold and clip detection for one input block.

        Measured on the *input* block, not the resampled frame: clipping is an
        interface/console problem, and the `np.clip` further down only destroys
        a sample that arrived at the rail already. (The HQ resampler can also
        ring a fraction of a dB past a hot input; counting that would report
        clipping the operator cannot act on.)
        """
        if not block.size:
            return
        mag = np.abs(block)
        peak = float(mag.max())
        clipped = int(np.count_nonzero(mag >= CLIP_LEVEL))
        if clipped:
            self.clipped_samples += clipped
            self._clip_until = now + CLIP_HOLD_S

        self.peak = peak
        elapsed = (now - self._peak_ts) if self._peak_ts else 0.0
        self._peak_ts = now
        decayed = self._peak_hold
        if elapsed > 0.0:
            decayed *= 10.0 ** (-(PEAK_DECAY_DB_PER_S * elapsed) / 20.0)
        self._peak_hold = max(peak, decayed)

    def clipping(self) -> bool:
        return time.time() < self._clip_until

    def _dispatch(self, pcm: bytes) -> None:
        for q in list(self._consumers):
            try:
                q.put_nowait(pcm)
            except asyncio.QueueFull:
                # A stalled session must not back-pressure the others. Evict its
                # oldest frame to make room, and count what was lost.
                try:
                    q.get_nowait()
                    q.put_nowait(pcm)
                    q.dropped = getattr(q, "dropped", 0) + 1
                except Exception:
                    pass

    # -- status -------------------------------------------------------
    def status(self) -> dict:
        return {
            "running": self.running,
            "device": self.device["label"] if self.device else None,
            "native_rate": self.native_rate,
            "target_rate": TARGET_RATE,
            "channel": self.channel,
            "level": round(self.level, 5),
            "peak": round(self.peak, 5),
            "peak_hold": round(self._peak_hold, 5),
            "rms_dbfs": round(dbfs(self.level), 1),
            "peak_dbfs": round(dbfs(self._peak_hold), 1),
            "clipping": self.clipping(),
            "clipped_samples": self.clipped_samples,
            "speaking": self.speaking,
            "error": self.error,
            "notice": self.notice,
            "reconnecting": self.reconnecting,
            "last_audio_ts": self._last_audio_ts,
            "consumers": len(self._consumers),
            "dropped_capture": self.dropped_capture,
        }
