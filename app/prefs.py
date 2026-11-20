"""Per-user preferences (#173): each person's own choices, in the same SQLite file as the users (app/db.py).

A preference is a JSON value kept under (user, scope, name). `scope` is "core" for core's preferences, or a
module's id for that module's own (a module imports this file; core never names a module). The rows follow
the person to every device they sign in from, and go when the user is deleted (a foreign-key cascade).

    prefs.get(user.id, "default_page", "/admin")                    # core's, with a fallback
    prefs.set(user.id, "layout", {"columns": 2}, scope="mixer")     # a module's own

The signed-in user reads and changes their own through /api/admin/me/prefs (main.py). These are personal
settings, not shared configuration: nothing here is for secrets, and nothing here is in the audit log.

Limits keep one person from filling the disk: a value is at most MAX_VALUE bytes of JSON, and a user has at
most MAX_PER_USER preferences across all scopes. A refusal raises Invalid with a fixed sentence.

A value is untrusted input (#288, #289): the API lets any signed-in user or token store any plain JSON under
core or any installed module's scope, so a module cannot assume its own code wrote what get() returns. Check
the type and range before use, never let one pick a host, path, URL, file name or command argument, and show
one on a page as text (textContent, Jinja autoescape), never as HTML. See docs/modules.md.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db

CORE = "core"
MAX_VALUE = 4000             # bytes of JSON for one value
MAX_PER_USER = 200           # preferences one user may hold, all scopes together

_SCOPE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")                     # "core" or a module id
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}(\.[a-z][a-z0-9_]{0,39}){0,2}$")


class Invalid(ValueError):
    """A preference that cannot be stored. str() is a fixed sentence, safe to show."""


def _check(scope: str, name: str) -> None:
    if not isinstance(scope, str) or not _SCOPE.match(scope):
        raise Invalid("That is not a valid preference scope.")
    if not isinstance(name, str) or not _NAME.match(name):
        raise Invalid("That is not a valid preference name.")


def _encode(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError):
        raise Invalid("A preference must be plain JSON.") from None
    if len(text.encode("utf-8")) > MAX_VALUE:
        raise Invalid(f"A preference can be at most {MAX_VALUE} bytes.")
    return text


def _decode(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def get(user_id: int, name: str, default: Any = None, *, scope: str = CORE) -> Any:
    """One preference, or `default` when it is not set."""
    _check(scope, name)
    row = db.one("select value from user_prefs where user_id = ? and scope = ? and name = ?",
                 (int(user_id), scope, name))
    return default if row is None else _decode(row["value"])


def scope_prefs(user_id: int, scope: str = CORE) -> dict[str, Any]:
    """Every preference this user has in one scope, by name."""
    _check(scope, "x")
    rows = db.query("select name, value from user_prefs where user_id = ? and scope = ? order by name",
                    (int(user_id), scope))
    return {r["name"]: _decode(r["value"]) for r in rows}


def all_prefs(user_id: int) -> dict[str, dict[str, Any]]:
    """Every preference this user has, as {scope: {name: value}}."""
    out: dict[str, dict[str, Any]] = {}
    for r in db.query("select scope, name, value from user_prefs where user_id = ? order by scope, name",
                      (int(user_id),)):
        out.setdefault(r["scope"], {})[r["name"]] = _decode(r["value"])
    return out


def set(user_id: int, name: str, value: Any, *, scope: str = CORE) -> None:     # noqa: A001 (the natural name)
    """Store one preference, replacing what was there. Setting None removes it."""
    _check(scope, name)
    if value is None:
        delete(user_id, name, scope=scope)
        return
    text = _encode(value)
    uid = int(user_id)
    with db.transaction() as tx:
        if not tx.one("select 1 as x from users where id = ?", (uid,)):
            raise Invalid("That user does not exist.")
        exists = tx.one("select 1 as x from user_prefs where user_id = ? and scope = ? and name = ?",
                        (uid, scope, name))
        if not exists:
            n = tx.one("select count(*) as n from user_prefs where user_id = ?", (uid,))["n"]
            if n >= MAX_PER_USER:
                raise Invalid(f"A person can keep at most {MAX_PER_USER} preferences.")
        tx.execute("insert into user_prefs (user_id, scope, name, value, updated_at) values (?, ?, ?, ?, ?)"
                   " on conflict (user_id, scope, name) do update set value = excluded.value,"
                   " updated_at = excluded.updated_at", (uid, scope, name, text, db.now()))


def delete(user_id: int, name: str, *, scope: str = CORE) -> bool:
    """Remove one preference. True when there was one."""
    _check(scope, name)
    return db.execute("delete from user_prefs where user_id = ? and scope = ? and name = ?",
                      (int(user_id), scope, name)) > 0


def clear(user_id: int, scope: str | None = None) -> int:
    """Remove all of a user's preferences, or only one scope's (a module forgetting its own). Returns how many."""
    if scope is None:
        return db.execute("delete from user_prefs where user_id = ?", (int(user_id),))
    _check(scope, "x")
    return db.execute("delete from user_prefs where user_id = ? and scope = ?", (int(user_id), scope))


# The operator panel's look (#434): which of these a person may choose. A stored value is untrusted (above), so
# the page reads each one through this table and falls back to the default for anything else; the value is only
# ever one of these words, and it reaches the page as an attribute, never as markup.
SHELL_DEFAULTS = {"ui_preset": "relay", "ui_mode": "dark", "ui_sidebar": "inset", "ui_collapse": "icon",
                  "ui_layout": "centered", "ui_navbar": "sticky", "ui_font": "system", "ui_collapsed": False}
SHELL_CHOICES = {"ui_preset": ("relay", "softpop", "tangerine", "brutalist"),
                 "ui_mode": ("light", "dark", "system"),
                 "ui_sidebar": ("inset", "sidebar", "floating"),
                 "ui_collapse": ("icon", "offcanvas"),
                 "ui_layout": ("centered", "full"),
                 "ui_navbar": ("sticky", "scroll"),
                 "ui_font": ("system", "rounded", "serif", "mono")}


def shell_view(stored: dict[str, Any] | None) -> dict[str, Any]:
    """The panel's look from a person's core preferences: every key of SHELL_DEFAULTS, each an allowed value."""
    stored = stored if isinstance(stored, dict) else {}
    out: dict[str, Any] = dict(SHELL_DEFAULTS)
    for name, allowed in SHELL_CHOICES.items():
        value = stored.get(name)
        if isinstance(value, str) and value in allowed:
            out[name] = value
    if stored.get("ui_collapsed") is True:
        out["ui_collapsed"] = True
    return out
