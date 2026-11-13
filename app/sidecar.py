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


def watch_parent(stop: Callable[[], None], stdin=None, grace: float = STOP_GRACE_S, kill=os._exit) -> threading.Thread:
    """Call `stop()` when the shell says `quit` or its pipe closes; end the process if that takes too long."""
    stream = stdin if stdin is not None else sys.stdin

    def run() -> None:
        try:
            for line in stream:
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
