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
import sys
import threading
import time

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
_capture_live = False


def _set_capture_live(flag: bool) -> None:
    global _capture_live
    _capture_live = flag


def capture_is_live() -> bool:
    return _capture_live


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
    _device_cache = _probe_input_devices(reinit=not _capture_live)
    return _device_cache


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


class AudioCapture:
    """Owns the PortAudio stream and fans identical PCM to every consumer."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self._stream: sd.InputStream | None = None
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
    def _open_stream(self, dev: dict, channel) -> tuple[int, bool, int]:
        """Open and start a PortAudio stream on `dev`; returns (rate, mix, pick). Raises RuntimeError
        with a fixed sentence in self.error when it cannot be opened."""
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
            try:
                block = (
                    indata.mean(axis=1, dtype=np.float32)
                    if mix
                    else indata[:, pick].copy()
                )
                self._raw.put_nowait(block)
            except queue.Full:
                self.dropped_capture += 1  # drop rather than block the driver thread

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
            self.error = (f"Could not open {dev['label']}. Check it is plugged in and not in use by another app. "
                          + mic_blocked_hint()).strip()
            raise RuntimeError(self.error) from exc
        self._stream = stream
        self._last_block_ts = time.time()
        return rate, mix, pick

    def start(self, selector, channel: int | str = 0) -> dict:
        self.stop()
        # Nothing is running here (stop() just ran), so a full hardware re-probe
        # is safe and picks up anything plugged in since the last look.
        dev = resolve_device(selector, refresh=True)
        if dev is None:
            self.error = "No input device matched the saved selection."
            raise RuntimeError(self.error)

        self.notice = selection_notice(selector)
        self._selector, self._channel_arg = selector, channel
        rate, mix, pick = self._open_stream(dev, channel)

        self.device = dev
        self.native_rate = int(rate)
        self.channel = "mix" if mix else pick
        self.error = None
        self.dropped_capture = 0
        self.peak = 0.0
        self._peak_hold = 0.0
        self._peak_ts = 0.0
        self.clipped_samples = 0
        self._clip_until = 0.0
        self.running = True
        _set_capture_live(True)
        self._stop.clear()
        self._pump = threading.Thread(target=self._run_pump, name="resample", daemon=True)
        self._pump.start()
        log.info(
            "Capture started on %s @ %d Hz (%s)",
            dev["label"],
            rate,
            "mix of %d ch" % dev["channels"] if mix else "ch %d" % pick,
        )
        return dev

    def stop(self) -> None:
        self._stop.set()
        _set_capture_live(False)
        with self._stream_lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        if self._pump is not None:
            self._pump.join(timeout=2.0)
            self._pump = None
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
        s = self._stream
        dead = s is None or not getattr(s, "active", True)
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
            old, self._stream = self._stream, None
            if old is not None:
                try:
                    old.abort()
                    old.close()
                except Exception:
                    pass
            _set_capture_live(False)        # nothing is open, so the backend can be re-probed
            try:
                dev = resolve_device(self._selector, refresh=True)
                if dev is None:
                    return False
                while not self._raw.empty():
                    self._raw.get_nowait()
                rate, mix, pick = self._open_stream(dev, self._channel_arg)
            except (RuntimeError, queue.Empty):
                self.error = INPUT_LOST_SENTENCE      # _open_stream's own sentence would hide that it is a replug wait
                return False
            if self._stop.is_set():
                self._stream.close()
                self._stream = None
                return False
            self.device, self.native_rate = dev, int(rate)
            self.channel = "mix" if mix else pick
            _set_capture_live(True)
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
