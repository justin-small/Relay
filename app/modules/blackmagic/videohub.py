"""A Blackmagic Videohub or Smart Videohub over its Ethernet protocol (#433): TCP port 9990, plain text.

Written from Blackmagic's "Videohub Ethernet Protocol" (version 2.x). Reading was checked on 2026-11-17 against the
router inside an ATEM 2 M/E Constellation HD (protocol 2.7, 45 x 16): the prelude, labels, routing, locks and ping all
parsed. Writes (route, labels, locks) have only run against the fake in tests/test_blackmagic_text.py. On connect the router sends everything it knows as
blocks, each a heading line ending in a colon, lines of content, and a blank line: `PROTOCOL PREAMBLE:`,
`VIDEOHUB DEVICE:` (model, unique id, how many inputs and outputs), `INPUT LABELS:`, `OUTPUT LABELS:`,
`VIDEO OUTPUT LOCKS:`, `VIDEO OUTPUT ROUTING:` and last `END PRELUDE:`. After that it sends a block again whenever
something changes. A command is the same shape (`VIDEO OUTPUT ROUTING:` then `<output> <input>` lines, all from zero);
the router answers `ACK` or `NAK`.

A lock is `O` (held by this connection), `L` (held by another control surface) or `U` (free). Relay never forces
another surface's lock open, and it does not send a change for an output another surface holds. A lock Relay takes
is the router's to release when the connection closes.
"""
from __future__ import annotations

from collections import deque

from .textclient import TextClient

PORT = 9990
MAX_PORTS = 288               # the largest Videohub has 288 inputs and outputs
LABEL_MAX = 60

# Why the router refused a command, as a fixed sentence for the page.
REFUSED = "The Videohub refused that."


def _int(value, default: int = -1) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def clean_label(value) -> str:
    """A label as the protocol can carry it: one line, no control characters."""
    if not isinstance(value, str):
        return ""
    return "".join(c for c in " ".join(value.split()) if c.isprintable())[:LABEL_MAX]


class State:
    def __init__(self) -> None:
        self.version = ""
        self.model = ""
        self.uid = ""
        self.present = True
        self.inputs = 0
        self.outputs = 0
        self.input_labels: dict[int, str] = {}
        self.output_labels: dict[int, str] = {}
        self.routing: dict[int, int] = {}
        self.locks: dict[int, str] = {}
        self.ready = False
        self.notice = ""


class VideohubClient(TextClient):
    NAME = "Videohub"
    PING_S = 10.0

    def __init__(self, host, port=PORT, on_change=None) -> None:
        super().__init__(host, port, on_change)
        self.state = State()
        self._block: tuple[str, list[str]] | None = None
        self._asked: deque[str] = deque(maxlen=64)      # what each ACK or NAK answers, oldest first

    def _reset(self) -> None:
        self.state = State()
        self._block = None
        self._asked.clear()

    # ----------------------------------------------------------------------------------------- reading
    def consume(self, buf: bytes) -> bytes:
        while True:
            line, sep, rest = buf.partition(b"\n")
            if not sep:
                return buf
            buf = rest
            self._line(line.decode("utf-8", "replace").rstrip("\r"))

    def _line(self, line: str) -> None:
        if self._block is None:
            head = line.strip()
            if head:
                self._block = (head, [])
            return
        if line.strip() == "":
            head, rows = self._block
            self._block = None
            self._apply(head, rows)
            return
        self._block[1].append(line.strip())

    def _pairs(self, rows: list[str]):
        for row in rows:
            first, _, rest = row.partition(" ")
            n = _int(first)
            if 0 <= n < MAX_PORTS:
                yield n, rest.strip()

    def _apply(self, head: str, rows: list[str]) -> None:
        st = self.state
        name = head.rstrip(":").strip().upper()
        if name in ("ACK", "NAK"):
            if self._asked:
                self._asked.popleft()
            st.notice = "" if name == "ACK" else REFUSED
        elif name == "PROTOCOL PREAMBLE":
            for row in rows:
                key, _, value = row.partition(":")
                if key.strip().lower() == "version":
                    st.version = value.strip()[:10]
        elif name == "VIDEOHUB DEVICE":
            for row in rows:
                key, _, value = row.partition(":")
                key, value = key.strip().lower(), value.strip()
                if key == "model name":
                    st.model = value[:60]
                elif key == "unique id":
                    st.uid = value[:40].lower()
                elif key == "device present":
                    st.present = value.lower() != "false"
                elif key == "video inputs":
                    st.inputs = min(MAX_PORTS, max(0, _int(value, 0)))
                elif key == "video outputs":
                    st.outputs = min(MAX_PORTS, max(0, _int(value, 0)))
        elif name == "INPUT LABELS":
            for n, label in self._pairs(rows):
                st.input_labels[n] = label[:LABEL_MAX]
        elif name == "OUTPUT LABELS":
            for n, label in self._pairs(rows):
                st.output_labels[n] = label[:LABEL_MAX]
        elif name == "VIDEO OUTPUT ROUTING":
            for n, src in self._pairs(rows):
                if 0 <= _int(src) < MAX_PORTS:
                    st.routing[n] = _int(src)
        elif name == "VIDEO OUTPUT LOCKS":
            for n, mode in self._pairs(rows):
                if mode in ("O", "L", "U"):
                    st.locks[n] = mode
        elif name == "END PRELUDE":
            st.ready = True
            self.connected, self.error = True, ""
        self.on_change()

    # ----------------------------------------------------------------------------------------- polling
    def ping(self) -> None:
        self._send("PING:\n\n")

    def _send(self, text: str) -> bool:
        ok = self.send_text(text)
        if ok:
            self._asked.append(text.split(":", 1)[0])
        return ok

    # ----------------------------------------------------------------------------------------- commands
    def can_route(self, output: int) -> bool:
        """False for an output another control surface holds."""
        return self.state.locks.get(output, "U") != "L"

    def route(self, pairs: dict[int, int]) -> bool:
        """Send (output -> input) changes in one block. Locked outputs and numbers out of range are the caller's to check."""
        rows = "".join(f"{o} {i}\n" for o, i in sorted(pairs.items()))
        return bool(rows) and self._send("VIDEO OUTPUT ROUTING:\n" + rows + "\n")

    def set_labels(self, kind: str, labels: dict[int, str]) -> bool:
        head = "INPUT LABELS:" if kind == "input" else "OUTPUT LABELS:"
        rows = "".join(f"{n} {clean_label(t)}\n" for n, t in sorted(labels.items()))
        return bool(rows) and self._send(head + "\n" + rows + "\n")

    def lock(self, output: int, take: bool) -> bool:
        return self._send(f"VIDEO OUTPUT LOCKS:\n{output} {'O' if take else 'U'}\n\n")
