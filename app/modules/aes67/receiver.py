"""Receiving one AES67 multicast stream (#266): a socket, a thread, loss and drift counters. No kernel driver.

Blocks go to `deliver` as float32 (frames, channels) at the stream's own rate; the audio pump resamples. Packets that
never arrive are replaced with silence of the same length so the timing of what follows stays true, and the
count is kept: it is what the dropout check of the hour-long soak reads. The clock is not locked to PTP: the
arrival rate against the wall clock is measured instead (`drift_ppm`), which is what a caption feed that resamples
anyway needs to know.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

import numpy as np

from ... import netroute, netsock
from ...audio import SourceError
from . import rtp
from .sdp import Stream

log = logging.getLogger("relay.aes67")

POLL_S = 0.25
LOST_AFTER_S = 3.0
MAX_FILL_PACKETS = 100          # a longer hole than this is a restart of the sender, not lost packets
DRIFT_AFTER_S = 30.0
RCVBUF = 1 << 20
NO_PORT = "The AES67 network port is not available."
NO_SOCKET = "Could not listen for the AES67 stream. Check the network port and that nothing else holds the address."


class Receiver:
    def __init__(self, stream: Stream, deliver: Callable[[np.ndarray], None]) -> None:
        self.stream = stream
        self._deliver = deliver
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.packets = self.frames = self.lost = self.late = self.ignored = self.bad = 0
        self.ssrc: int | None = None
        self._expected: int | None = None
        self._first = self._last = 0.0
        self._started = time.monotonic()

    # ------------------------------------------------------------------------------------------------ run
    def start(self) -> None:
        r = netroute.resolve("aes67")
        if not r.available:
            raise SourceError(NO_PORT)
        try:
            sock = netsock.multicast_socket(r, self.stream.group, self.stream.port)
        except (OSError, ValueError):
            raise SourceError(NO_SOCKET) from None
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF)
        except OSError:
            pass
        sock.settimeout(POLL_S)
        self._sock, self._stop = sock, threading.Event()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, name=f"aes67-{self.stream.port}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        t, self._thread = self._thread, None
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

    def alive(self) -> bool:
        t = self._thread
        if t is None or not t.is_alive():
            return False
        seen = self._last or self._started
        return time.monotonic() - seen < LOST_AFTER_S

    def _run(self) -> None:
        sock = self._sock
        while not self._stop.is_set() and sock is not None:
            try:
                data, addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                log.warning("aes67: receive failed", exc_info=True)
                return
            self.handle(data, addr[0])

    # ------------------------------------------------------------------------------------------------ packets
    def handle(self, data: bytes, sender: str) -> None:
        """One datagram. Public so a test can feed packets without a socket."""
        if self.stream.source and sender != self.stream.source:
            self.ignored += 1
            return
        pkt = rtp.parse(data)
        if pkt is None or pkt.payload_type != self.stream.payload_type:
            self.bad += 1
            return
        if self.ssrc is None:
            self.ssrc = pkt.ssrc
        elif pkt.ssrc != self.ssrc:
            self.ignored += 1
            return
        block = rtp.decode(pkt.payload, self.stream.encoding, self.stream.channels)
        if block is None:
            self.bad += 1
            return
        now = time.monotonic()
        with self._lock:
            if self._expected is not None:
                gap = rtp.seq_gap(self._expected, pkt.seq)
                if gap < 0:
                    self.late += 1
                    return
                if gap:
                    self.lost += gap
                    if gap <= MAX_FILL_PACKETS:
                        self._deliver(np.zeros((gap * len(block), block.shape[1]), dtype=np.float32))
            else:
                self._first = now
            self._expected = (pkt.seq + 1) & 0xFFFF
            self.packets += 1
            self.frames += len(block)
            self._last = now
        self._deliver(block)

    # ------------------------------------------------------------------------------------------------ numbers
    def drift_ppm(self) -> float | None:
        """Parts per million the sender's samples arrive faster (+) or slower (-) than the stream's rate says, over
        the time since the first packet; None until DRIFT_AFTER_S of audio has been counted."""
        with self._lock:
            if not self._first or self._last - self._first < DRIFT_AFTER_S:
                return None
            elapsed = self._last - self._first
            counted = self.frames + self.lost * (self.frames // max(1, self.packets))
        return round((counted / elapsed / self.stream.rate - 1.0) * 1e6, 1)

    def stats(self) -> dict:
        return {"packets": self.packets, "lost": self.lost, "late": self.late, "ignored": self.ignored,
                "bad": self.bad, "drift_ppm": self.drift_ppm(), "receiving": self.alive()}
