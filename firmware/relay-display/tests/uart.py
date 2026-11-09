"""The board's console, over a socket QEMU serves.

The console is the whole of the interaction with the firmware: the boot log is
read off it, readiness is detected on it, and anything the firmware's serial console is asked goes
back down it. `qemu.py` gives QEMU `-serial tcp:…,server=on`, so this is a
plain TCP client and not a pty — a pty would have brought a platform difference
into the one place every test touches.

**Nothing here waits without a deadline.** It is the rule
this module exists to enforce: a hung firmware has to fail one test with a
readable message, not hang the suite until somebody notices. Every wait takes a
timeout, the timeout has a default, and running out of it raises with the log
attached.
"""

from __future__ import annotations

import contextlib
import re
import socket
import threading
import time
from collections.abc import Callable

# What a wait costs when the caller does not say.
#
# Generous on purpose: the emulator boots this image in about a second on a
# laptop and several times that on a loaded CI runner, and a timeout tuned to
# the laptop is a suite that fails for the reason nobody can reproduce. It is
# still a bound, which is the only property that matters.
DEFAULT_TIMEOUT = 30.0


class UartTimeout(AssertionError):
    """A line that never arrived.

    An `AssertionError` rather than an exception of its own, because that is
    what it means to a reader of the report: the firmware did not say the thing
    the test required it to say. Deriving from it keeps `pytest`'s assertion
    reporting and keeps a caller from having to catch two kinds of failure.
    """


class BoardDied(AssertionError):
    """The emulator is gone, and something was still talking to it.

    Separate from `UartTimeout` because it is a different sentence. A timeout
    says the firmware did not answer; this says there is no longer a machine to
    answer. One QEMU segfault used to end a full run as fifteen
    `BrokenPipeError` and `ConnectError` failures, none of which named the
    emulator, and the single test that happened to be waiting on a pattern held
    the only readable account of it. Raised from every place that touches the
    board, carrying the three things that message assembled: the exit status,
    QEMU's stderr, and the console tail.

    An `AssertionError` for the reason `UartTimeout` is one.
    """


class Uart:
    """A reader thread, a growing transcript, and waits with deadlines."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        connect_timeout: float = 10.0,
        context: Callable[[], str] | None = None,
    ) -> None:
        # **What the emulator was doing when a wait ran out.** A timeout on its
        # own says the board went quiet; it does not say whether the board hung,
        # crashed or was never running. `qemu.py` passes a callable that answers
        # that, and every message below carries it — the first time this suite
        # went quiet mid-boot, the difference between those three was an
        # afternoon.
        self._context = context
        self._sock = self._connect(host, port, connect_timeout)
        self._text = ""
        self._lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read_forever, daemon=True)
        self._reader.start()

    @staticmethod
    def _connect(host: str, port: int, timeout: float) -> socket.socket:
        """Retry until QEMU has the listener up.

        QEMU binds its serial socket during machine setup, and the harness
        starts the process and connects in the next breath. Retrying for a
        moment is cheaper than sleeping for one, and much cheaper than the
        flake it removes.
        """
        deadline = time.monotonic() + timeout
        last: OSError | None = None
        while time.monotonic() < deadline:
            try:
                return socket.create_connection((host, port), timeout=1.0)
            except OSError as error:  # pragma: no cover - timing dependent
                last = error
                time.sleep(0.05)
        raise UartTimeout(f"QEMU never opened its console socket on {host}:{port}: {last}")

    def _read_forever(self) -> None:
        self._sock.settimeout(0.2)
        while not self._closed:
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            if not chunk:
                return
            # `replace`, not `strict`. A reset mid-line cuts a UTF-8 sequence in
            # half and a decode error there would lose the transcript that says
            # why the test failed.
            with self._lock:
                self._text += chunk.decode("utf-8", "replace")

    @property
    def text(self) -> str:
        """Everything the board has said, from the ROM banner onward."""
        with self._lock:
            return self._text

    def wait_for(self, pattern: str, *, timeout: float = DEFAULT_TIMEOUT) -> re.Match[str]:
        """Block until `pattern` matches somewhere in the transcript.

        A regular expression over the whole transcript rather than line by line,
        because that is what the assertions in the issue are written as: the
        evidence is `push: input 1 attempt 1 -> 202`, which is one line, and
        `net: eth <ip>` where the address is the part being captured.

        **Searched from the start, every time.** A test that sets a mark and
        then waits for something the board already said would otherwise wait for
        it to be said twice — see `mark` for the other half of that.
        """
        deadline = time.monotonic() + timeout
        compiled = re.compile(pattern)
        while True:
            found = compiled.search(self.text)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                raise UartTimeout(
                    f"waited {timeout:.1f}s for /{pattern}/ and it never appeared.\n"
                    f"{self._state()}"
                    f"--- console transcript ---\n{self.text}"
                )
            time.sleep(0.02)

    def _state(self) -> str:
        return "" if self._context is None else self._context()

    def mark(self) -> int:
        """Where the transcript is now, for `wait_for_after`."""
        return len(self.text)

    def wait_for_after(
        self, mark: int, pattern: str, *, timeout: float = DEFAULT_TIMEOUT
    ) -> re.Match[str]:
        """`wait_for`, but only counting what was said after `mark`.

        The distinction matters for every test that drives an input twice. The
        second identical line is indistinguishable from the first in a
        transcript searched from the beginning, so a test asserting on it would
        pass on the echo of its own setup.
        """
        deadline = time.monotonic() + timeout
        compiled = re.compile(pattern)
        while True:
            found = compiled.search(self.text[mark:])
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                raise UartTimeout(
                    f"waited {timeout:.1f}s for /{pattern}/ after the mark and it never "
                    f"appeared.\n{self._state()}"
                    f"--- console since the mark ---\n{self.text[mark:]}"
                )
            time.sleep(0.02)

    def count(self, pattern: str, *, after: int = 0) -> int:
        """How many times `pattern` has been said. For the cases that count."""
        return len(re.findall(pattern, self.text[after:]))

    def send_line(self, line: str) -> None:
        """One command, terminated.

        `\\n` and not `\\r\\n`: the parser treats either as a terminator and a
        bare LF cannot be read as two empty commands by anything downstream.

        **A closed socket here is a dead board, and it says so**.
        `sendall` on the far side of an exited emulator raises
        `BrokenPipeError`, which names a file descriptor and not the machine.
        The state callable knows the exit status and QEMU's own last words, so
        the failure carries those instead.
        """
        try:
            self._sock.sendall(line.encode("ascii") + b"\n")
        except OSError as error:
            raise BoardDied(
                f"the board's console is closed; sending {line!r} failed: {error}\n"
                f"{self._state()}"
                f"--- console transcript ---\n{self.text[-4000:]}"
            ) from error

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(OSError):
            self._sock.close()
