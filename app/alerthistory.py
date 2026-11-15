"""Alert history (#217): who sent what, when, to which targets, how long until it was seen, and the reply.

Kept in Relay's database (app/db.py) and trimmed by age: rows older than KEEP_DAYS go in the hourly housekeeping.
`record` never raises: a database that is down loses the row and logs a warning, and the alert itself is not
affected.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

log = logging.getLogger("relay.alerthistory")

KEEP_DAYS = 30               # Justin's choice (2026-11-14)
MAX_ROWS = 50_000            # a ceiling whatever the age
PAGE = 100

_COLS = "id, at, kind, name, text, level, sender, targets, outcome, seen_on, seen_after_s, reply"


def record(*, kind: str, at: str | None = None, name: str = "", text: str = "", level: str = "", sender: str = "",
           targets: list[str] | None = None, outcome: str = "", seen_on: str = "",
           seen_after_s: int | None = None, reply: str = "") -> bool:
    """Write one row. Returns False (and logs why) instead of raising."""
    try:
        db.execute(
            "insert into alert_history (at, kind, name, text, level, sender, targets, outcome, seen_on,"
            " seen_after_s, reply) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (at or db.now(), kind, name, text, level, sender, json.dumps(targets or [], ensure_ascii=False),
             outcome, seen_on, seen_after_s, reply))
        return True
    except db.Unavailable:
        log.warning("alert history: the database is unavailable, a row was lost")
    except Exception as e:
        log.warning("alert history: row not written: %s", type(e).__name__)
    return False


def _row(r: dict) -> dict:
    try:
        r["targets"] = json.loads(r["targets"]) if r["targets"] else []
    except ValueError:
        r["targets"] = []
    return r


def search(*, before: int | None = None, limit: int = PAGE) -> dict[str, Any]:
    """Newest first: {"entries": [...], "more": bool}. `before` is an id to page from."""
    limit = max(1, min(int(limit), 500))
    where, args = "", []
    if before:
        where, args = " where id < ?", [int(before)]
    rows = db.query(f"select {_COLS} from alert_history{where} order by id desc limit ?", (*args, limit + 1))
    return {"entries": [_row(r) for r in rows[:limit]], "more": len(rows) > limit, "keep_days": KEEP_DAYS}


def prune() -> int:
    """Delete rows older than KEEP_DAYS, then any beyond the newest MAX_ROWS. Returns how many went."""
    gone = db.execute("delete from alert_history where at < ?", (db.later(-KEEP_DAYS * 86400),))
    gone += db.execute("delete from alert_history where id <= (select id from alert_history order by id desc"
                       " limit 1 offset ?)", (MAX_ROWS,))
    return gone
