"""The channel between Relay and the desktop shell that launches it (desktop/, #183).

Only active when the shell starts Relay with RELAY_SIDECAR=1; a plain `python run.py` never touches stdin and
prints nothing extra. The shell talks to Relay over its own stdin/stdout pipes, so there is no extra port to
open and it behaves the same on Windows, macOS and Linux:

  Relay -> shell   one line per event on stdout:  RELAY-EVENT {"event": "ready", ...}
  shell -> Relay   the line `quit` asks for a graceful stop; stdin closing (the shell is gone, even if it
                   crashed) does the same, so a backend is never left running without its parent.

Events: `ready` (both sockets are listening: ports, LAN addresses, fingerprint, version), `network` (the LAN
addresses changed) and `fingerprint` (the panel certificate was replaced, e.g. after a Wi-Fi change). The
shell pins the certificate it is told about here.
A graceful stop lets SQLite checkpoint and mDNS say goodbye; if it has not finished after STOP_GRACE_S the
process is ended anyway.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Callable

PREFIX = "RELAY-EVENT "
STOP_GRACE_S = 10.0

_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("RELAY_SIDECAR") == "1"


def emit(event: str, **fields) -> None:
    """Write one event line for the shell. A no-op unless the shell started Relay."""
    if not enabled():
        return
    line = PREFIX + json.dumps({"event": event, **fields}, separators=(",", ":"))
    with _lock:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass  # the shell has gone; stdin closing is what stops us


def _poll_stdin(interval: float = 0.25) -> bytes | None:
    """Windows: wait for bytes on stdin without blocking in a read. None when the pipe is closed."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    handle = wintypes.HANDLE(msvcrt.get_osfhandle(0))
    available = wintypes.DWORD(0)
    while True:
        ok = ctypes.windll.kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None)
        if not ok:
            return None  # the write end is gone (ERROR_BROKEN_PIPE): the shell has exited
        if available.value:
            data = os.read(0, available.value)
            return data or None
        time.sleep(interval)


def watch_parent(stop: Callable[[], None], stdin=None, grace: float = STOP_GRACE_S, kill=os._exit) -> threading.Thread:
    """Call `stop()` when the shell says `quit` or its pipe closes; end the process if that takes too long."""
    def lines():
        if stdin is not None:  # tests pass a stream
            yield from stdin
            return
        # The real pipe, read without ever holding a blocking read on it. On Windows a thread blocked in a read
        # on the stdin handle stalls the rest of start-up (the server did not come up until the shell closed the
        # pipe), so there the pipe is polled with PeekNamedPipe; elsewhere a plain os.read is fine.
        pending = b""
        while True:
            chunk = _poll_stdin() if sys.platform == "win32" else os.read(0, 4096)
            if not chunk:  # EOF (posix: b""; Windows: None, the pipe is closed)
                return
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line.decode("utf-8", "replace")

    def run() -> None:
        try:
            for line in lines():
                if line.strip().lower() == "quit":
                    break
        except (OSError, ValueError):
            pass
        stop()
        time.sleep(grace)
        kill(0)

    thread = threading.Thread(target=run, name="relay-sidecar-stdin", daemon=True)
    thread.start()
    return thread
