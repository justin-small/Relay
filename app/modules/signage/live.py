"""What changes reach the players, and what each screen reports it is playing (#205).

`version` goes up on every signage change. Each player's event stream checks it once a second and, when it has
moved, works out that screen's state again and sends it only if it differs. Players report the item they are
showing, which the admin page lists with when each screen was last heard from. Both live in memory: a restart
loses nothing that matters, since players reconnect and report again within seconds.
"""
from __future__ import annotations

import threading
import time

version = 0
_lock = threading.Lock()
_now: dict[str, dict] = {}
NOW_MAX = 500


def bump() -> None:
    global version
    with _lock:
        version += 1


def report(slug: str, item_id: int | None, label: str) -> None:
    with _lock:
        if slug not in _now and len(_now) >= NOW_MAX:
            return
        prev = _now.get(slug) or {}
        since = prev.get("since") if prev.get("item_id") == item_id else time.time()
        _now[slug] = {"item_id": item_id, "label": label[:120], "since": since, "seen": time.time()}


def playing(slug: str) -> dict | None:
    with _lock:
        v = _now.get(slug)
        return dict(v) if v else None


def forget(slug: str | None = None) -> None:
    with _lock:
        if slug is None:
            _now.clear()
        else:
            _now.pop(slug, None)
