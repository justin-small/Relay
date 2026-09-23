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
import threading
import time

import numpy as np
import sounddevice as sd
import soxr

log = logging.getLogger("relay.audio")

TARGET_RATE = 24000
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
    return _pulse_sources(out) or _number_repeats(out)


# Monitor sources replay a sink's output. They are never a microphone, and
# listing them next to the real inputs invites picking the speakers by mistake.
def _is_monitor(src) -> bool:
    return src.name.endswith(".monitor") or src.proplist.get("device.class") == "monitor"


def _pulse_sources(alsa: list[dict]) -> list[dict] | None:
    """The host's real inputs, when capture runs through a PulseAudio server.

    In Docker on macOS/Windows, PortAudio only sees ALSA's `default` and
    `pulse` devices, and both lead to whatever the server's default source is.
    The actual microphones live on the server, so ask it for them and show
    those instead. Each entry still opens the ALSA `pulse` device; start()
    sets PULSE_SOURCE so that open lands on the chosen source.

    None (and the plain ALSA list stands) when there is no server, or it
    cannot be reached.
    """
    if not os.environ.get("PULSE_SERVER"):
        return None
    bridge = next((d for d in alsa if d["hostapi"] == "ALSA" and d["name"] == "pulse"), None)
    bridge = bridge or next((d for d in alsa if d["hostapi"] == "ALSA" and d["name"] == "default"), None)
    if bridge is None:
        return None
    try:
        import pulsectl

        with pulsectl.Pulse("live-caption-relay", connect=False) as pulse:
            pulse.connect(timeout=3)
            default = pulse.server_info().default_source_name
            sources = [s for s in pulse.source_list() if not _is_monitor(s)]
    except Exception as exc:
        log.warning("Could not list PulseAudio sources, showing ALSA devices: %s", exc)
        return None
    if not sources:
        return None
    rate = int(os.environ.get("RELAY_NATIVE_RATE") or 0) or bridge["default_samplerate"]
    return _number_repeats(
        [
            {
                "index": bridge["index"],
                "name": s.description,
                "hostapi": "PulseAudio",
                "label": f"PulseAudio: {s.description}",
                "channels": s.channel_count,
                # Not s.sample_spec: pulsectl hands back that struct after
                # libpulse has freed it, and the rate reads as garbage. The
                # open rate comes from RELAY_NATIVE_RATE, as it always has.
                "default_samplerate": rate,
                "pulse_source": s.name,
                "default": s.name == default,
            }
            for s in sources
        ]
    )


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


def resolve_device(selector, refresh: bool = False) -> dict | None:
    """Match a saved device by 'API: Name' label, bare name, or index."""
    devices = list_input_devices(refresh=refresh)
    if not devices:
        return None
    if selector is None or selector == "":
        for d in devices:
            if d.get("default"):
                return d
        try:
            default_idx = sd.default.device[0]
        except Exception:
            default_idx = None
        for d in devices:
            if d["index"] == default_idx:
                return d
        return devices[0]
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
    for d in devices:
        if d.get("pulse_source") == str(selector).strip():
            return d
    # Saved before the panel listed PulseAudio's sources: those two ALSA
    # devices both meant "the server's default input", so keep meaning that.
    if sel in ("alsa: pulse", "alsa: default", "pulse", "default"):
        for d in devices:
            if d.get("default"):
                return d
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
    def start(self, selector, channel: int | str = 0) -> dict:
        self.stop()
        # Nothing is running here (stop() just ran), so a full hardware re-probe
        # is safe and picks up anything plugged in since the last look.
        dev = resolve_device(selector, refresh=True)
        if dev is None:
            self.error = "No input device matched the saved selection."
            raise RuntimeError(self.error)

        # PortAudio reports 44100 for ALSA's `pulse` device no matter what the
        # server actually runs at, which is how the Docker-over-PulseAudio path
        # ends up resampling 48k -> 44.1k -> 24k instead of straight to 24k.
        # RELAY_NATIVE_RATE pins the open rate for that case; unset (the native
        # run, and Linux /dev/snd) keeps the device's own default.
        rate = int(os.environ.get("RELAY_NATIVE_RATE") or 0) or dev["default_samplerate"] or 48000
        # libpulse reads PULSE_SOURCE whenever the ALSA plugin opens a stream,
        # which is what points the shared `pulse` device at this one source.
        if dev.get("pulse_source"):
            os.environ["PULSE_SOURCE"] = dev["pulse_source"]
        else:
            os.environ.pop("PULSE_SOURCE", None)
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
            self._stream = sd.InputStream(
                device=dev["index"],
                channels=channels,
                samplerate=rate,
                blocksize=blocksize,
                dtype="float32",
                callback=callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            self.error = f"Could not open {dev['label']}: {exc}"
            raise RuntimeError(self.error) from exc

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
            "mix of %d ch" % channels if mix else "ch %d" % pick,
        )
        return dev

    def stop(self) -> None:
        self._stop.set()
        _set_capture_live(False)
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
            except queue.Empty:
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
            "last_audio_ts": self._last_audio_ts,
            "consumers": len(self._consumers),
            "dropped_capture": self.dropped_capture,
        }
