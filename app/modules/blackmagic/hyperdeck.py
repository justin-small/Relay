"""A Blackmagic HyperDeck over its Ethernet protocol (#432): TCP port 9993, plain text.

Written from Blackmagic's "HyperDeck Ethernet Protocol" (version 1.x) and not yet run against a real recorder: the
fake in tests/test_blackmagic.py answers the way that document says. On connect the deck sends `500 connection info:`
with its model and protocol version. Every reply is a line `<code> <text>`: three-digit codes from 100 are errors,
`200 ok` is a plain answer, and a text ending in a colon is followed by `key: value` lines and a blank line. Codes
from 500 are unasked notifications (`508 transport info:`, `502 slot info:`, `512 remote info:`).

What Relay does with it: asks to be told about transport, slot and remote changes, keeps the transport state (stopped,
record, play, the clip and timecode), each slot's disk and the recording time left on it, and the clip list. It can
start and stop recording and play. Nothing here ever deletes or formats a disk.
"""
from __future__ import annotations

import re
import time

from .textclient import TextClient

PORT = 9993
MAX_CLIPS = 100
MAX_SLOTS = 4

# What an error code means, as a fixed sentence for the page. Never the deck's own text.
REFUSED = {
    100: "The HyperDeck did not understand that.",
    101: "The HyperDeck does not support that setting.",
    102: "The HyperDeck refused that value.",
    103: "The HyperDeck does not support that.",
    104: "The HyperDeck's disk is full.",
    105: "There is no disk in the HyperDeck.",
    106: "The HyperDeck's disk has a problem.",
    107: "The HyperDeck's timeline is empty.",
    108: "The HyperDeck had a problem of its own.",
    109: "That is out of range for this HyperDeck.",
    110: "The HyperDeck has no video input.",
    111: "Remote control is turned off on the HyperDeck.",
    120: "The HyperDeck refused the connection.",
    150: "The HyperDeck cannot do that in its current state.",
    151: "The HyperDeck does not support that codec.",
    160: "The HyperDeck does not support that format.",
    161: "The HyperDeck refused that.",
    162: "The HyperDeck's disk is not ready.",
}
STATUSES = ("preroll", "stopped", "play", "forward", "rewind", "jog", "shuttle", "record")


def _int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _bool(value) -> bool:
    return str(value).strip().lower() == "true"


_TC = re.compile(r"\d\d[:;]\d\d[:;]\d\d[:;]\d\d")


def _timecode(value: str) -> str:
    v = (value or "").strip()
    return v if _TC.fullmatch(v) else ""


class State:
    def __init__(self) -> None:
        self.model = ""
        self.protocol = ""
        self.status = ""
        self.slot = 0
        self.clip_id = 0
        self.video_format = ""
        self.loop = False
        self.timecode = ""
        self.slots: dict[int, dict] = {}
        self.clips: list[dict] = []
        self.remote_enabled = True
        self.remote_override = False
        self.notice = ""


class HyperDeckClient(TextClient):
    NAME = "HyperDeck"
    PING_S = 10.0

    def __init__(self, host, port=PORT, on_change=None) -> None:
        super().__init__(host, port, on_change)
        self.state = State()
        self._pending: tuple[int, str, dict[str, str], list[str]] | None = None
        self._slot_asked = 0.0

    def _reset(self) -> None:
        self.state = State()
        self._pending = None
        self._slot_asked = 0.0

    # ----------------------------------------------------------------------------------------- reading
    def consume(self, buf: bytes) -> bytes:
        while True:
            line, sep, rest = buf.partition(b"\n")
            if not sep:
                return buf
            buf = rest
            self._line(line.decode("utf-8", "replace").rstrip("\r"))

    def _line(self, line: str) -> None:
        if self._pending is not None:
            code, head, fields, extra = self._pending
            if line.strip() == "":
                self._pending = None
                self._message(code, head, fields, extra)
                return
            key, sep, value = line.partition(":")
            if sep:
                fields.setdefault(key.strip().lower(), value.strip())
            extra.append(line.strip())
            return
        text = line.strip()
        if not text:
            return
        code_text, _, head = text.partition(" ")
        if not (len(code_text) == 3 and code_text.isdigit()):
            return
        code = int(code_text)
        if head.endswith(":"):
            self._pending = (code, head[:-1].strip().lower(), {}, [])
        else:
            self._message(code, head.strip().lower(), {}, [])

    def _message(self, code: int, head: str, f: dict[str, str], lines: list[str]) -> None:
        st = self.state
        if code == 500:
            st.model = f.get("model", "")[:60]
            st.protocol = f.get("protocol version", "")[:10]
            self.connected, self.error = True, ""
            self.send_text("notify: remote: true transport: true slot: true\r\n"
                           "transport info\r\nslot info: slot id: 1\r\nslot info: slot id: 2\r\nclips get\r\n")
            self._slot_asked = time.monotonic()
        elif code in (208, 508):
            was = st.status
            st.status = f.get("status", st.status) if f.get("status", "") in STATUSES else st.status
            if was == "record" and st.status != "record":
                self.refresh_clips()      # a finished recording is a new clip
            st.slot = _int(f.get("slot id"), st.slot)
            st.clip_id = _int(f.get("clip id"), st.clip_id)
            st.video_format = f.get("video format", st.video_format)[:30]
            st.loop = _bool(f.get("loop")) if "loop" in f else st.loop
            st.timecode = _timecode(f.get("display timecode") or f.get("timecode", "")) or st.timecode
        elif code in (202, 502):
            sid = _int(f.get("slot id"))
            if 1 <= sid <= MAX_SLOTS:
                st.slots[sid] = {"id": sid, "status": f.get("status", "")[:20], "volume": f.get("volume name", "")[:40],
                                 "seconds_left": max(0, _int(f.get("recording time"))),
                                 "video_format": f.get("video format", "")[:30]}
        elif code == 205:
            clips = []
            for raw in lines:
                num, sep, rest = raw.partition(":")
                if not sep or not num.strip().isdigit():
                    continue
                parts = rest.strip().rsplit(None, 2)
                if len(parts) == 3:
                    clips.append({"id": int(num), "name": parts[0][:80], "start": parts[1][:11], "duration": parts[2][:11]})
                if len(clips) >= MAX_CLIPS:
                    break
            st.clips = clips
        elif code in (212, 512):
            st.remote_enabled = _bool(f.get("enabled", "true"))
            st.remote_override = _bool(f.get("override"))
        elif code == 200:
            st.notice = ""
        elif 100 <= code < 200:
            st.notice = REFUSED.get(code, "The HyperDeck refused that.")
        self.on_change()

    # ----------------------------------------------------------------------------------------- polling
    def ping(self) -> None:
        self.send_text("ping\r\n")

    def tick(self, now: float) -> None:
        if not self.connected:
            return
        # The deck announces a slot only when its disk changes; the recording time left moves every minute, so ask.
        every = 5.0 if self.state.status == "record" else 30.0
        if now - self._slot_asked >= every and 1 <= self.state.slot <= MAX_SLOTS:
            self._slot_asked = now
            self.send_text(f"slot info: slot id: {self.state.slot}\r\n")

    # ----------------------------------------------------------------------------------------- commands
    def record(self) -> bool:
        return self.send_text("record\r\n")

    def stop(self) -> bool:
        return self.send_text("stop\r\n")

    def play(self) -> bool:
        return self.send_text("play\r\n")

    def refresh_clips(self) -> None:
        self.send_text("clips get\r\n")

    # ----------------------------------------------------------------------------------------- what the page shows
    def current_clip(self) -> dict | None:
        st = self.state
        return next((c for c in st.clips if c["id"] == st.clip_id), None)
