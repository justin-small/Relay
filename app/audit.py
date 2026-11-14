"""The audit log (#172): who did what and when, in the same SQLite file as the users (app/db.py).

What is recorded: sign-ins and failed sign-ins, sign-outs, user, role, device and token changes, and every
change made through the admin page or API (`POST`, `PUT`, `PATCH` and `DELETE` on /api/admin, written by
main.py's middleware once the change succeeded), each tied to the person, or the API token, that made it.

Two calls, for core and modules alike (a module imports this file; core never names a module):

* `note(request, action, summary, target=..., detail=...)` inside an admin route: says what this request did
  in words. The middleware writes it with the signed-in user and their address once the response succeeds,
  so a refused or failed change leaves no entry. A route that notes nothing still gets a plain entry naming
  the method and path.
* `record(action, summary, actor=..., ...)` for anything else: a sign-in, or a module's own work with no
  request behind it (`actor=None`: the system).

`action` is a dotted id: core's start with an area ("signin.failed", "user.update"); a module's start with its
own id ("propresenter.playlist_build"). `summary` is the sentence people read. `detail` is a small dict of
plain values and must never hold a secret: no passwords, PINs, tokens or keys, and no request bodies.

Writing never raises: the audit log is not worth a failed request or a stopped caption, so a database that is
down loses the entry and logs a warning. Entries are kept KEEP_DAYS days (`prune`, run hourly by main.py).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import db

log = logging.getLogger("relay.audit")

KEEP_DAYS = 90               # Justin's choice (2026-11-14)
MAX_ROWS = 200_000           # a ceiling whatever the age, so a flood of failed sign-ins cannot fill the disk
PAGE = 100

_ACTION = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,3}$")
_MAX_SUMMARY = 300
_MAX_TARGET = 200
_MAX_DETAIL = 4000

_COLS = "id, at, actor_id, actor_name, actor_username, token_name, action, summary, target, detail, ip"


def _clip(v: Any, n: int) -> str:
    s = "" if v is None else str(v)
    s = "".join(c if c.isprintable() else " " for c in s)
    return s[:n]


def _detail(detail: dict | None) -> str:
    if not detail:
        return ""
    try:
        text = json.dumps(detail, default=str, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return ""
    return text if len(text) <= _MAX_DETAIL else json.dumps({"truncated": True})


def _actor(actor) -> tuple[int | None, str, str, str]:
    """(actor_id, name, username, token name) for a users.User, a TokenUser, or None (the system)."""
    if actor is None:
        return None, "", "", ""
    uid = getattr(actor, "id", 0) or None          # id 0 is the setup token standing in for an Owner
    token_name = ""
    token_id = getattr(actor, "token_id", 0)
    if token_id:
        row = db.one("select name from api_tokens where id = ?", (token_id,))
        token_name = row["name"] if row else "(revoked token)"
    return uid, _clip(actor.name, 80), _clip(actor.username, 32), _clip(token_name, 80)


def record(action: str, summary: str, *, actor=None, target: Any = "", detail: dict | None = None,
           ip: str = "") -> bool:
    """Write one entry. Returns False (and logs why) instead of raising; True once written."""
    if not isinstance(action, str) or not _ACTION.match(action):
        log.error("audit: bad action id %r, entry dropped", action)
        return False
    try:
        uid, name, username, token = _actor(actor)
        db.execute(
            f"insert into audit_log (at, actor_id, actor_name, actor_username, token_name, action, summary,"
            f" target, detail, ip) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.now(), uid, name, username, token, action, _clip(summary, _MAX_SUMMARY) or action,
             _clip(target, _MAX_TARGET), _detail(detail), _clip(ip, 64)))
        return True
    except db.Unavailable:
        log.warning("audit: the database is unavailable, entry %s lost", action)
    except Exception as e:                                       # never let the log break what it records
        log.warning("audit: entry %s not written: %s", action, type(e).__name__)
    return False


def note(request, action: str, summary: str, *, target: Any = "", detail: dict | None = None) -> None:
    """Say what this admin request did. Written by the middleware with the request's user and address only
    when the response succeeds. Several notes in one request make several entries."""
    notes = getattr(request.state, "audit", None)
    if not isinstance(notes, list):
        notes = []
        request.state.audit = notes
    notes.append({"action": action, "summary": summary, "target": target, "detail": detail})


def pending(request) -> list[dict]:
    """The notes this request made (main.py's middleware)."""
    notes = getattr(request.state, "audit", None)
    return notes if isinstance(notes, list) else []


# ------------------------------------------------------------------ reading

def _row(r: dict) -> dict:
    try:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else {}
    except ValueError:
        r["detail"] = {}
    return r


def search(*, actor: str | int | None = None, kind: str | None = None, q: str | None = None,
           days: int | None = None, before: int | None = None, limit: int = PAGE) -> dict:
    """Newest first. `actor` is a user id or "system" (no person); `kind` an action's area ("signin") or a
    whole action; `q` text in the summary, target, person or address; `days` how far back; `before` an id to
    page from. Returns {"entries": [...], "more": bool}."""
    where, args = [], []
    if actor == "system":
        where.append("actor_id is null")
    elif actor is not None:
        where.append("actor_id = ?")
        args.append(int(actor))
    if kind:
        where.append("(action = ? or action like ? escape '\\')")
        args += [kind, _like(kind) + ".%"]
    if q:
        pat = "%" + _like(q) + "%"
        where.append("(summary like ? escape '\\' or target like ? escape '\\' or actor_name like ? escape '\\'"
                     " or actor_username like ? escape '\\' or token_name like ? escape '\\' or ip like ? escape '\\')")
        args += [pat] * 6
    if days:
        where.append("at >= ?")
        args.append(db.later(-int(days) * 86400))
    if before:
        where.append("id < ?")
        args.append(int(before))
    limit = max(1, min(int(limit), 500))
    sql = f"select {_COLS} from audit_log" + (" where " + " and ".join(where) if where else "")
    rows = db.query(sql + " order by id desc limit ?", (*args, limit + 1))
    return {"entries": [_row(r) for r in rows[:limit]], "more": len(rows) > limit}


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def people() -> list[dict]:
    """Everyone the log names, by their latest name in it."""
    return db.query(
        "select a.actor_id as id, a.actor_name as name, a.actor_username as username from audit_log a"
        " join (select actor_id, max(id) as last from audit_log where actor_id is not null group by actor_id) m"
        " on a.id = m.last order by lower(a.actor_name), a.actor_id")


def kinds() -> list[str]:
    """The areas of the actions in the log ("signin", "user", a module's id, ...)."""
    rows = db.query("select distinct action from audit_log")
    return sorted({r["action"].split(".", 1)[0] for r in rows})


# ------------------------------------------------------------------ retention

def prune() -> int:
    """Delete entries older than KEEP_DAYS, then any beyond the newest MAX_ROWS. Returns how many went."""
    gone = db.execute("delete from audit_log where at < ?", (db.later(-KEEP_DAYS * 86400),))
    gone += db.execute("delete from audit_log where id <= (select id from audit_log order by id desc"
                       " limit 1 offset ?)", (MAX_ROWS,))
    if gone:
        log.info("audit: %d old entr%s deleted", gone, "y" if gone == 1 else "ies")
    return gone
