"""Sending AES67 (#267): the channel mixer, RTP packets, the SDP and SAP announcement, and the thread that paces them.

Pure pieces first (`encode`, `build_sdp`, `build_sap`, `Mixer`, `tone`), each testable with no socket; `Sender` is the
thread that puts them on the network. Relay does not lock to PTP: the stream is paced by this computer's clock and
announces `ts-refclk:local`. Packets go out every millisecond (48 frames), the packet time every AES67 receiver takes.
"""
from __future__ import annotations

import logging
import math
import random
import socket
import threading
import time

import numpy as np

from ... import netroute, netsock

log = logging.getLogger("relay.aes67")

RATE = 48000
PTIME_MS = 1
FRAMES = RATE * PTIME_MS // 1000
PAYLOAD_TYPE = 97
MAX_CHANNELS = 8                        # 8 x L24 x 48 frames fits one packet, and a Dante AES67 flow carries 8
MAX_QUEUE_S = 3.0                       # a channel holds at most this much waiting audio
SAP_GROUP = "239.255.255.255"
SAP_PORT = 9875
SAP_EVERY_S = 30.0
NO_PORT = "The AES67 network port is not available."
NO_SEND = "Could not send the AES67 stream. Check the network port."
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0


# -- encoding -------------------------------------------------------------------------------------------------------
def encode(block: np.ndarray, encoding: str) -> bytes:
    """A float32 (frames, channels) block as big-endian L16 or L24, clipped at full scale."""
    x = np.clip(block, -1.0, 1.0)
    if encoding == "L16":
        return np.rint(x * 32767.0).astype(">i2").tobytes()
    v = np.rint(x * 8388607.0).astype(np.int32).reshape(-1) & 0xFFFFFF
    out = np.empty((v.size, 3), dtype=np.uint8)
    out[:, 0], out[:, 1], out[:, 2] = v >> 16, (v >> 8) & 0xFF, v & 0xFF
    return out.tobytes()


def rtp_packet(seq: int, timestamp: int, ssrc: int, payload: bytes, payload_type: int = PAYLOAD_TYPE) -> bytes:
    return (bytes((0x80, payload_type & 0x7F)) + (seq & 0xFFFF).to_bytes(2, "big")
            + (timestamp & 0xFFFFFFFF).to_bytes(4, "big") + (ssrc & 0xFFFFFFFF).to_bytes(4, "big") + payload)


def resample(x: np.ndarray, rate: int) -> np.ndarray:
    """Mono float32 at `rate` as mono float32 at 48 kHz (linear: tones and speech, not music)."""
    if rate == RATE or x.size < 2:
        return x
    n = max(1, int(round(x.size * RATE / rate)))
    return np.interp(np.arange(n) * (rate / RATE), np.arange(x.size), x).astype(np.float32)


def pcm_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm[:len(pcm) - len(pcm) % 2], dtype="<i2").astype(np.float32) / 32768.0


def tone(hz: float, seconds: float, level_db: float = -12.0) -> np.ndarray:
    """A sine with 5 ms fades, so it starts and ends without a click."""
    n = max(1, int(seconds * RATE))
    t = np.arange(n, dtype=np.float32) / RATE
    wave = np.sin(2 * math.pi * hz * t).astype(np.float32) * (10.0 ** (level_db / 20.0))
    fade = min(n // 2, int(0.005 * RATE))
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        wave[:fade] *= ramp
        wave[-fade:] *= ramp[::-1]
    return wave


# One alert level -> beeps (hz, count, seconds each, gap seconds): more and higher as it gets more urgent.
ALERT_TONES = {"info": (880.0, 1, 0.20, 0.0), "attention": (988.0, 2, 0.15, 0.10), "urgent": (1320.0, 3, 0.15, 0.08)}


def alert_tone(level: str) -> np.ndarray:
    hz, count, length, gap = ALERT_TONES.get(level, ALERT_TONES["info"])
    beep, rest = tone(hz, length), np.zeros(int(gap * RATE), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i in range(count):
        parts.append(beep)
        if i < count - 1 and rest.size:
            parts.append(rest)
    return np.concatenate(parts)


# -- the channel mixer ----------------------------------------------------------------------------------------------
class Mixer:
    """Audio waiting to go out, one queue per channel, and a gain per channel. `overlay` mixes a clip into what is
    already waiting, starting at once (a tone cuts in at the next packet); `append` queues behind it (speech, a lane).
    Thread-safe: callers and the sender's thread meet here."""

    def __init__(self, channels: int, gains_db: list[float] | None = None) -> None:
        self.channels = channels
        self._lock = threading.Lock()
        self._q = [np.zeros(0, dtype=np.float32) for _ in range(channels)]
        self._peak = [0.0] * channels
        self.dropped = 0
        self.gain = [1.0] * channels
        self.set_gains(gains_db or [])

    def set_gains(self, gains_db: list[float]) -> None:
        self.gain = [10.0 ** (float(gains_db[i] if i < len(gains_db) else 0.0) / 20.0) for i in range(self.channels)]

    @property
    def cap(self) -> int:
        return int(MAX_QUEUE_S * RATE)

    def overlay(self, channel: int, x: np.ndarray) -> None:
        if not 0 <= channel < self.channels or not x.size:
            return
        x = x[:self.cap]
        with self._lock:
            q = self._q[channel]
            if q.size < x.size:
                q = np.concatenate((q, np.zeros(x.size - q.size, dtype=np.float32)))
            else:
                q = q.copy()
            q[:x.size] += x
            self._q[channel] = q

    def append(self, channel: int, x: np.ndarray) -> int:
        """Queue behind what waits; returns the frames taken (fewer when the queue is full)."""
        if not 0 <= channel < self.channels or not x.size:
            return 0
        with self._lock:
            q = self._q[channel]
            take = max(0, min(x.size, self.cap - q.size))
            if take < x.size:
                self.dropped += x.size - take
            if take:
                self._q[channel] = np.concatenate((q, x[:take]))
            return take

    def queued(self, channel: int) -> int:
        with self._lock:
            return int(self._q[channel].size) if 0 <= channel < self.channels else 0

    def free(self, channel: int) -> int:
        return max(0, self.cap - self.queued(channel))

    def clear(self) -> None:
        with self._lock:
            self._q = [np.zeros(0, dtype=np.float32) for _ in range(self.channels)]

    def pull(self, frames: int = FRAMES) -> np.ndarray:
        """The next `frames` of every channel with gain applied, silence where nothing waits."""
        out = np.zeros((frames, self.channels), dtype=np.float32)
        with self._lock:
            for c in range(self.channels):
                q = self._q[c]
                if q.size:
                    n = min(frames, q.size)
                    out[:n, c] = q[:n] * self.gain[c]
                    self._q[c] = q[n:]
                    self._peak[c] = max(self._peak[c], float(np.max(np.abs(out[:n, c]))))
        return out

    def peaks_db(self) -> list[float | None]:
        """Each channel's loudest sample since the last call, in dBFS (None for silence)."""
        with self._lock:
            peaks, self._peak = self._peak, [0.0] * self.channels
        return [round(20.0 * math.log10(p), 1) if p > 1e-6 else None for p in peaks]


# -- what is announced ----------------------------------------------------------------------------------------------
def build_sdp(name: str, group: str, port: int, channels: int, encoding: str, address: str, labels: list[str],
              session: int) -> str:
    """The SDP a Dante receiver (AES67 mode) or any AES67 receiver subscribes from: the group, the format, the packet
    time and the clock reference. `address` is this computer's address on the AES67 port."""
    names = [(labels[i] if i < len(labels) and labels[i] else f"Ch {i + 1}") for i in range(channels)]
    lines = ["v=0", f"o=- {session} {session} IN IP4 {address}", f"s={name}", f"i={channels} channels: {', '.join(names)}",
             f"c=IN IP4 {group}/32", "t=0 0", f"m=audio {port} RTP/AVP {PAYLOAD_TYPE}",
             f"i={', '.join(names)}", f"c=IN IP4 {group}/32", f"a=rtpmap:{PAYLOAD_TYPE} {encoding}/{RATE}/{channels}",
             f"a=ptime:{PTIME_MS}", "a=ts-refclk:local", "a=mediaclk:direct=0",
             f"a=source-filter: incl IN IP4 {group} {address}"]
    lines += [f"a=label:{i + 1} {n}" for i, n in enumerate(names)]
    return "\r\n".join(lines) + "\r\n"


def build_sap(sdp: str, address: str, msg_id: int, withdraw: bool = False) -> bytes:
    """One SAP packet (RFC 2974): version 1, IPv4, no authentication, `withdraw` deletes the announcement."""
    flags = 0x20 | (0x04 if withdraw else 0)
    return (bytes((flags, 0)) + (msg_id & 0xFFFF).to_bytes(2, "big") + socket.inet_aton(address)
            + b"application/sdp\0" + sdp.encode("utf-8"))


# -- the thread -----------------------------------------------------------------------------------------------------
class Sender:
    """Opens the multicast socket on the AES67 port, announces the stream, and sends one packet per millisecond until
    stopped. Silence goes out when nothing is queued, so a receiver subscribed to it never sees the stream stop."""

    def __init__(self, mixer: Mixer, *, name: str, group: str, port: int, encoding: str, labels: list[str]) -> None:
        self.mixer = mixer
        self.name, self.group, self.port, self.encoding, self.labels = name, group, port, encoding, labels
        self.packets = 0
        self.late = 0
        self.address = ""
        self.sdp = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._session = int(time.time())
        self._msg_id = random.getrandbits(16) or 1

    def start(self) -> None:
        r = netroute.resolve("aes67")
        if not r.available:
            raise OSError(NO_PORT)
        try:
            self._sock = netsock.multicast_sender(r, ttl=32)
        except (OSError, ValueError):
            raise OSError(NO_SEND) from None
        self.address = r.address or "0.0.0.0"  # nosec B104 - an address in an SDP, not a bind
        self.sdp = build_sdp(self.name, self.group, self.port, self.mixer.channels, self.encoding, self.address,
                             self.labels, self._session)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="aes67-send", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    def _announce(self, withdraw: bool = False) -> None:
        try:
            self._sock.sendto(build_sap(self.sdp, self.address, self._msg_id, withdraw), (SAP_GROUP, SAP_PORT))
        except (OSError, AttributeError):
            pass

    def _run(self) -> None:
        ssrc, seq, ts = random.getrandbits(32), random.getrandbits(16), random.getrandbits(32)
        due = time.monotonic()
        next_sap = due
        sock = self._sock
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_sap:
                    self._announce()
                    next_sap = now + SAP_EVERY_S
                if now < due:
                    time.sleep(min(due - now, 0.002))
                    continue
                if now - due > 0.05:            # stalled (a laptop slept): start over rather than burst
                    self.late += 1
                    due = now
                payload = encode(self.mixer.pull(FRAMES), self.encoding)
                try:
                    sock.sendto(rtp_packet(seq, ts, ssrc, payload), (self.group, self.port))
                    self.packets += 1
                except OSError:
                    pass
                seq, ts, due = (seq + 1) & 0xFFFF, (ts + FRAMES) & 0xFFFFFFFF, due + PTIME_MS / 1000.0
        finally:
            self._announce(withdraw=True)
            try:
                sock.close()
            except OSError:
                pass
