"""One REAPER computer over OSC (#413): send commands to REAPER's OSC control surface and follow what it reports.

REAPER is set up with an OSC control surface (Preferences > Control/OSC/web) in "Configure device IP + local port"
mode with the stock pattern file: it listens on its local port (default 8000) and sends its state to the device IP
and port, which is Relay's listen port here (default 9001). Checked live against REAPER on macOS (2026-11-17):

- /record, /stop with no argument are triggers. REAPER answers /record, /play, /stop (1.0 or 0.0) and, while
  rolling, /time about every 90 ms.
- /action <int> runs any REAPER action by its command id. 40157 inserts a marker at the play (or edit) cursor and
  REAPER answers /lastmarker/number/str and /lastmarker/time; /lastmarker/name <text> then names that marker.
- /action 41743 ("Control surface: refresh all surfaces") makes REAPER send its whole state again. REAPER paces what
  it sends (about 80 packets of 1 KB a second), so a refresh queues ahead of live changes: with 64 tracks an answer
  came 8 to 14 s late, with 8 tracks 1.4 s. Relay refreshes on open and then only after REAPER was quiet for
  QUIET_S (meters and the clock keep a busy REAPER talking).
- Per-track feedback covers a bank of DEVICE_TRACK_COUNT tracks (8 in the stock file); /device/track/count sets it to
  the computer's `tracks` setting (DEFAULT_TRACKS, at most TRACKS). REAPER reports every slot in the bank, also slots
  with no track, as "Track N" at -inf dB with width 0.

UDP has no connection: REAPER counts as reached while it answered within LOST_S.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ... import netsock
from . import osc

log = logging.getLogger("relay.reaper")

TRAFFIC = "reaper"
DEFAULT_PORT = 8000          # REAPER listens here (its OSC surface's "local listen port")
DEFAULT_LISTEN = 9001        # Relay listens here (REAPER's "device port")
TRACKS = 64                  # the most tracks Relay follows
DEFAULT_TRACKS = 16          # REAPER's bank is set to this on open unless the computer's setting says otherwise
QUIET_S = 20.0               # REAPER said nothing for this long: ask for the whole state
LOST_S = 45.0                # no answer for this long: not reached (a refresh with 64 tracks took 14 s to answer)
RECV_BUFFER = 4 << 20        # a refresh is a burst of about 3,000 messages
MARKER_GAP_S = 0.05          # between inserting a marker and naming it, so they arrive in order

A_MARKER = 40157             # Markers: Insert marker at current position
A_REFRESH = 41743            # Control surface: refresh all surfaces

_TRACK_RE = re.compile(r"/track/(\d{1,3})/(name|recarm|mute|vu|volume/db|pan2/str)")


@dataclass
class Track:
    n: int
    name: str = ""
    armed: bool = False
    muted: bool = False
    vu: float = 0.0
    volume_db: float = 0.0
    width: str = ""
    loud_at: float = 0.0          # when the meter was last above the silence level (0: never)

    def real(self) -> bool:
        """REAPER reports empty slots too: "Track N", -inf dB and width 0. An armed slot is always a track."""
        if self.armed:
            return True
        return not (self.name == f"Track {self.n}" and self.volume_db <= -150.0 and self.width in ("0W", ""))


@dataclass
class State:
    recording: bool = False
    playing: bool = False
    stopped: bool = True
    time: float = 0.0
    record_since: float | None = None         # epoch seconds when recording started, as Relay saw it
    marker_name: str = ""
    marker_number: str = ""
    heard_at: float = 0.0
    tracks: dict[int, Track] = field(default_factory=dict)

    def track(self, n: int) -> Track:
        t = self.tracks.get(n)
        if t is None:
            t = self.tracks[n] = Track(n)
        return t


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, on_packet: Callable[[bytes, Any], None]) -> None:
        self.on_packet = on_packet

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.on_packet(data, addr)

    def error_received(self, exc: Exception) -> None:      # a refused send on some systems; REAPER may be closed
        pass


class Link:
    """The UDP socket to one REAPER and the state it reports. `on_change(kind)` is called on the event loop when
    something Relay acts on changes: "record", "marker", "tracks"."""

    def __init__(self, host: str, port: int, listen: int, silent_vu: float,
                 on_change: Callable[[str], None] | None = None, tracks: int = DEFAULT_TRACKS) -> None:
        self.host, self.port, self.listen = host, port, listen
        self.bank = max(1, min(TRACKS, int(tracks)))
        self.silent_vu = silent_vu
        self.on_change = on_change
        self.state = State()
        self.transport: asyncio.DatagramTransport | None = None
        self.peer: tuple[str, int] | None = None
        self._from: set[str] = set()

    # -- socket ------------------------------------------------------------------------------------------------
    async def open(self) -> None:
        """Resolve REAPER's address and bind Relay's listen port. Raises netsock.RouteError (a fixed sentence)."""
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(self.host, self.port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        except OSError:
            raise netsock.RouteError(f"Could not find {self.host}.") from None
        self.peer = infos[0][4][:2]
        self._from = {self.peer[0]}
        # REAPER on this computer sends to 127.0.0.1, which a socket bound to the network's address never hears.
        bind = "127.0.0.1" if is_loopback(self.host) else None
        transport, _ = await netsock.udp_endpoint(TRAFFIC, lambda: _Proto(self._packet), self.listen, host=bind)
        self.transport = transport
        sock = transport.get_extra_info("socket")
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECV_BUFFER)
        self.send("/device/track/count", self.bank)
        self.refresh()

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
        self.transport = None

    def send(self, address: str, *args: str | int | float) -> bool:
        """Send one message. False when the socket is not open or the system refused it."""
        if self.transport is None or self.peer is None:
            return False
        try:
            self.transport.sendto(osc.message(address, *args), self.peer)
        except OSError:
            return False
        return True

    def refresh(self) -> bool:
        return self.send("/action", A_REFRESH)

    def reached(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return bool(self.state.heard_at) and now - self.state.heard_at < LOST_S

    # -- commands (sent once, never retried) -------------------------------------------------------------------
    def record(self) -> bool:
        return self.send("/record")

    def stop(self) -> bool:
        return self.send("/stop")

    def action(self, command: int) -> bool:
        return self.send("/action", int(command))

    async def marker(self, name: str) -> bool:
        if not self.send("/action", A_MARKER):
            return False
        if name:
            await asyncio.sleep(MARKER_GAP_S)
            return self.send("/lastmarker/name", name)
        return True

    def arm(self, n: int, on: bool) -> bool:
        return self.send(f"/track/{int(n)}/recarm", 1.0 if on else 0.0)

    def mute(self, n: int, on: bool) -> bool:
        return self.send(f"/track/{int(n)}/mute", 1.0 if on else 0.0)

    # -- what REAPER says ----------------------------------------------------------------------------------------
    def _packet(self, data: bytes, addr: Any) -> None:
        src = addr[0] if isinstance(addr, tuple) and addr else ""
        if src not in self._from and not (is_loopback(src) and is_loopback(self.host)):
            return
        msgs = osc.parse(data)
        if not msgs:
            return
        self.apply(msgs)

    def apply(self, msgs: list[tuple[str, list[Any]]], now: float | None = None) -> None:
        now = time.time() if now is None else now
        st = self.state
        st.heard_at = now
        changed: set[str] = set()
        for address, args in msgs:
            a0 = args[0] if args else None
            num = float(a0) if isinstance(a0, (int, float)) else None
            if address == "/record" and num is not None:
                on = num >= 0.5
                if on != st.recording:
                    st.recording = on
                    st.record_since = now if on else None
                    changed.add("record")
            elif address == "/play" and num is not None:
                st.playing = num >= 0.5
            elif address == "/stop" and num is not None:
                st.stopped = num >= 0.5
            elif address == "/time" and num is not None:
                st.time = num
            elif address == "/lastmarker/name" and isinstance(a0, str):
                st.marker_name = a0[:100]
                changed.add("marker")
            elif address == "/lastmarker/number/str" and isinstance(a0, str):
                st.marker_number = a0[:10]
            else:
                m = _TRACK_RE.fullmatch(address)
                if not m:
                    continue
                n = int(m.group(1))
                if not 1 <= n <= self.bank:
                    continue
                t = st.track(n)
                what = m.group(2)
                if what == "name" and isinstance(a0, str):
                    if a0[:60] != t.name:
                        t.name = a0[:60]
                        changed.add("tracks")
                elif what == "recarm" and num is not None:
                    if (num >= 0.5) != t.armed:
                        t.armed = num >= 0.5
                        changed.add("tracks")
                elif what == "mute" and num is not None:
                    t.muted = num >= 0.5
                elif what == "vu" and num is not None:
                    t.vu = max(0.0, min(1.0, num))
                    if t.vu >= self.silent_vu:
                        t.loud_at = now
                elif what == "volume/db" and num is not None:
                    t.volume_db = num
                elif what == "pan2/str" and isinstance(a0, str):
                    t.width = a0[:8]
        if changed and self.on_change:
            for kind in sorted(changed):
                try:
                    self.on_change(kind)
                except Exception:
                    log.exception("REAPER change handler failed")
