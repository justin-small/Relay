"""Accounts, roles, permissions, sessions and trusted devices (everything lives in app/db.py's SQLite file).

Five fixed roles, ranked member < viewer < operator < admin < owner, sit on top of named permissions: code asks
`user.can("settings.manage")`, never "is the role at least admin". Today a permission's `min_role` decides who
has it (`role_permissions`, the only place that does), so a roles table can replace that one function later.

A member never opens the operator panel (it lacks `admin.open`): it signs in only to use the features it is
granted, such as sending on /alert, and lands on the home page that lists them (#175).

Core registers the permission catalogue below; each module registers its own (`<module id>.<action>`).

Rules that protect the system are enforced here, not in the web layer, which only maps `Refused.key` to a
status and to the fixed sentence in MESSAGES (never `str(exc)`). An `actor` of None is the system itself
(first-run setup, the lockout tool) and is bound by no rank rule.

Every call blocks on the database and can raise `db.Unavailable`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
from dataclasses import dataclass
from typing import Iterable

from . import db

ROLES = ("member", "viewer", "operator", "admin", "owner")      # rank = index + 1

PASSWORD_SESSION_SECONDS = 30 * 24 * 3600
PIN_SESSION_SECONDS = 12 * 3600
PIN_ATTEMPTS = 5

# scrypt cost, stored inside every hash so it can be raised later without locking anyone out. N=2**15, r=8
# is about 32 MiB and 50-100 ms per check. Tests lower SCRYPT_N to stay fast.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1

_USERNAME = re.compile(r"[a-z0-9._-]{3,32}")
_PIN = re.compile(r"[0-9]{4,8}")


def _rank(role: str) -> int:
    return ROLES.index(role) + 1 if role in ROLES else 0


# ------------------------------------------------------------------ permissions

@dataclass(frozen=True)
class Permission:
    id: str
    label: str
    group: str
    min_role: str


_CORE = (
    Permission("admin.open", "Open the operator panel", "Operator panel", "viewer"),
    Permission("session.view", "See session health and status", "Session", "viewer"),
    Permission("session.control", "Start and stop captions", "Session", "operator"),
    Permission("audio.manage", "Change audio device, input channel and source language", "Audio", "admin"),
    Permission("targets.manage", "Change target languages", "Session", "operator"),
    Permission("panels.view", "See panels", "Panels", "viewer"),
    Permission("panels.manage", "Change panels, firmware, flash, reboot, forget", "Panels", "admin"),
    Permission("alerts.view", "See alert setup", "Alerts", "viewer"),
    Permission("alerts.manage", "Change alert setup", "Alerts", "admin"),
    Permission("alerts.send", "Send alerts from /alert when it needs a sign-in", "Alerts", "member"),
    Permission("overlays.view", "See overlays", "Overlays", "viewer"),
    Permission("overlays.manage", "Create, change and delete overlays", "Overlays", "operator"),
    Permission("recordings.view", "List and download recordings", "Recordings", "operator"),
    Permission("recordings.manage", "Delete recordings and change recording settings", "Recordings", "admin"),
    Permission("schedules.view", "See schedules", "Schedules", "viewer"),
    Permission("schedules.manage", "Change schedules", "Schedules", "admin"),
    Permission("blocklist.manage", "Edit blocked words", "Settings", "admin"),
    Permission("settings.view", "Read the configuration (secrets hidden)", "Settings", "viewer"),
    Permission("settings.manage", "Change appearance, tuning and realtime settings", "Settings", "admin"),
    Permission("credentials.manage", "Change the OpenAI API key", "Settings", "owner"),
    Permission("modules.view", "See which modules exist and their health", "Modules", "viewer"),
    Permission("modules.manage", "Switch modules on and off, run a module's test", "Modules", "admin"),
    Permission("users.view", "See users and trusted devices", "Users", "admin"),
    Permission("users.manage", "Create and change users, reset passwords, trust devices", "Users", "admin"),
    Permission("tokens.own", "Create API tokens for your own scripts", "Users", "admin"),
    Permission("tokens.manage", "See and revoke everyone's API tokens", "Users", "owner"),
)

# What an API token never does, whatever its user's role: manage accounts, passwords, devices, tokens or the
# OpenAI key. A leaked token can then use Relay but cannot take over an account or mint more tokens. The
# signed-in-user routes with no permission of their own (password, PIN, own tokens) refuse tokens in main.py.
TOKEN_BARRED = frozenset({"users.view", "users.manage", "credentials.manage", "tokens.own", "tokens.manage"})

_PERM_ID = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_perm_lock = threading.Lock()
_perms: dict[str, Permission] = {}
_role_cache: dict[str, frozenset[str]] = {}


def register(perms: Iterable[Permission]) -> None:
    """Add permissions to the catalogue. A bad id or role, or an id already registered differently, raises
    ValueError and registers none of them. Registering an identical permission again is a no-op."""
    perms = list(perms)
    with _perm_lock:
        seen: dict[str, Permission] = {}
        for p in perms:
            if not isinstance(p, Permission) or not _PERM_ID.match(p.id or ""):
                raise ValueError(f"bad permission id: {getattr(p, 'id', p)!r}")
            if p.min_role not in ROLES:
                raise ValueError(f"permission {p.id}: unknown role {p.min_role!r}")
            if p.id in seen and seen[p.id] != p:
                raise ValueError(f"permission {p.id} is declared twice")
            have = _perms.get(p.id)
            if have is not None and have != p:
                raise ValueError(f"permission {p.id} is already registered")
            seen[p.id] = p
        for p in seen.values():
            _perms.setdefault(p.id, p)
        _role_cache.clear()


def catalogue() -> list[Permission]:
    with _perm_lock:
        return list(_perms.values())


def role_permissions(role: str) -> frozenset[str]:
    """The permissions a role grants. The one place that decides it: a roles table would replace this body."""
    with _perm_lock:
        got = _role_cache.get(role)
        if got is None:
            rank = _rank(role)
            got = frozenset(p.id for p in _perms.values() if rank and _rank(p.min_role) <= rank)
            _role_cache[role] = got
        return got


register(_CORE)


@dataclass(frozen=True)
class User:
    id: int
    username: str
    name: str
    role: str
    disabled: bool
    must_change_password: bool
    pin_set: bool

    def can(self, perm: str) -> bool:
        return perm in role_permissions(self.role)

    def permissions(self) -> frozenset[str]:
        return role_permissions(self.role)


@dataclass(frozen=True)
class TokenUser(User):
    """A user acting through one of their API tokens (#174): their role, less TOKEN_BARRED."""
    token_id: int = 0

    def can(self, perm: str) -> bool:
        return perm not in TOKEN_BARRED and super().can(perm)

    def permissions(self) -> frozenset[str]:
        return role_permissions(self.role) - TOKEN_BARRED


# ------------------------------------------------------------------ refusals

class Refused(Exception):
    """A rule said no. `key` indexes MESSAGES, the only text that may reach a browser."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    @property
    def message(self) -> str:
        return MESSAGES[self.key]


MESSAGES: dict[str, str] = {
    "bad_login": "Wrong username or password.",
    "disabled": "This account is turned off. Ask an admin to turn it back on.",
    "bad_username": "A username is 3 to 32 characters: letters, digits, dot, dash or underscore.",
    "bad_name": "A name is 1 to 80 characters.",
    "bad_password": "A password is 8 to 200 characters.",
    "bad_pin": "A PIN is 4 to 8 digits.",
    "bad_role": "That is not a role.",
    "bad_device_name": "A device name is 1 to 80 characters.",
    "username_taken": "That username is already taken.",
    "not_found": "That does not exist any more.",
    "role_too_high": "You cannot give a role higher than your own.",
    "account_above_you": "You cannot change an account with a higher role than yours.",
    "last_owner": "There must always be one active owner.",
    "self_action": "You cannot turn off or delete your own account.",
    "wrong_password": "The password is wrong.",
    "same_password": "Choose a different password from the current one.",
    "wrong_pin": "That PIN is not right.",
    "pin_cleared": "Too many wrong PINs. Sign in with your password and set a new PIN.",
    "no_device": "This browser is not a trusted device.",
    "not_allowed": "Not allowed",
    "bad_token_name": "A token name is 1 to 80 characters.",
    "bad_expiry": "A token expires after 30, 90 or 365 days, or never.",
    "too_many_tokens": "You have the most tokens allowed. Revoke one you no longer use first.",
}


# ------------------------------------------------------------------ validation and hashing

def _clean_username(v) -> str:
    v = v.strip().lower() if isinstance(v, str) else ""
    if not _USERNAME.fullmatch(v):
        raise Refused("bad_username")
    return v


def _clean_name(v, key="bad_name") -> str:
    v = v.strip() if isinstance(v, str) else ""
    if not 1 <= len(v) <= 80 or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise Refused(key)
    return v


def _check_password(v) -> str:
    if not isinstance(v, str) or not 8 <= len(v) <= 200:
        raise Refused("bad_password")
    return v


def _check_pin(v) -> str:
    if not isinstance(v, str) or not _PIN.fullmatch(v):
        raise Refused("bad_pin")
    return v


def _check_role(v) -> str:
    if v not in ROLES:
        raise Refused("bad_role")
    return v


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _scrypt(secret: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32,
                          maxmem=256 * n * r * p + (1 << 20))


def hash_secret(secret: str) -> str:
    """`scrypt$N$r$p$salt$hash`: a fresh random salt each time, the cost stored alongside."""
    salt = os.urandom(16)
    n, r, p = SCRYPT_N, SCRYPT_R, SCRYPT_P
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(_scrypt(secret, salt, n, r, p))}"


def verify_secret(secret: str, stored: str | None) -> bool:
    try:
        scheme, n, r, p, salt, want = (stored or "").split("$")
        n, r, p = int(n), int(r), int(p)
        if scheme != "scrypt" or n < 2 or n & (n - 1) or n > 2 ** 20 or not 1 <= r <= 32 or not 1 <= p <= 16:
            return False
        got = _scrypt(secret, base64.b64decode(salt), n, r, p)
        return hmac.compare_digest(got, base64.b64decode(want))
    except (ValueError, TypeError):
        return False


_dummy_hash = ""


def _burn() -> None:
    """Spend the time a real check would, so an unknown username answers as slowly as a wrong password."""
    global _dummy_hash
    if not _dummy_hash:
        _dummy_hash = hash_secret("not a real password")
    verify_secret("x", _dummy_hash)


# ------------------------------------------------------------------ accounts

_COLS = "id, username, name, role, disabled, must_change_password, (pin_hash is not null) as pin_set"
_COLS_U = ("u.id, u.username, u.name, u.role, u.disabled, u.must_change_password,"
           " (u.pin_hash is not null) as pin_set")


def _user(row: dict) -> User:
    return User(id=row["id"], username=row["username"], name=row["name"], role=row["role"],
                disabled=bool(row["disabled"]), must_change_password=bool(row["must_change_password"]),
                pin_set=bool(row["pin_set"]))


def count() -> int:
    return db.one("select count(*) as n from users")["n"]


def list_users() -> list[dict]:
    return db.query(
        "select id, username, name, role, disabled, must_change_password, (pin_hash is not null) as pin_set,"
        " last_login, created_at as created from users order by lower(name), id")


def get(user_id) -> User | None:
    row = db.one(f"select {_COLS} from users where id = ?", (user_id,))
    return _user(row) if row else None


def get_by_username(username: str) -> User | None:
    row = db.one(f"select {_COLS} from users where username = ?",
                 ((username or "").strip().lower() if isinstance(username, str) else "",))
    return _user(row) if row else None


def _target(tx, user_id, actor: User | None) -> dict:
    """The row being changed, locked, after the actor's rank rule. Raises not_found / account_above_you."""
    row = tx.one(f"select {_COLS} from users where id = ?", (user_id,))
    if row is None:
        raise Refused("not_found")
    if actor is not None and _rank(row["role"]) > _rank(actor.role):
        raise Refused("account_above_you")
    return row


def _other_owner(tx, user_id) -> bool:
    return tx.one("select 1 as x from users where role = 'owner' and not disabled and id <> ? limit 1",
                  (user_id,)) is not None


def create(username, name, role, password, *, temporary: bool = True, actor: User | None = None) -> User:
    username, name = _clean_username(username), _clean_name(name)
    role, password = _check_role(role), _check_password(password)
    if actor is not None and _rank(role) > _rank(actor.role):
        raise Refused("role_too_high")
    pw = hash_secret(password)
    with db.transaction() as tx:
        row = tx.one(
            "insert into users (username, name, role, password_hash, must_change_password)"
            f" values (?, ?, ?, ?, ?) on conflict (username) do nothing returning {_COLS}",
            (username, name, role, pw, bool(temporary)))
        if row is None:
            raise Refused("username_taken")
    return _user(row)


def update(user_id, *, name=None, role=None, disabled=None, actor: User | None) -> User:
    new_name = _clean_name(name) if name is not None else None
    new_role = _check_role(role) if role is not None else None
    with db.transaction() as tx:
        cur = _target(tx, user_id, actor)
        if actor is not None:
            if new_role is not None and _rank(new_role) > _rank(actor.role):
                raise Refused("role_too_high")
            if disabled and actor.id == cur["id"]:
                raise Refused("self_action")
        final_role = new_role if new_role is not None else cur["role"]
        final_disabled = bool(disabled) if disabled is not None else cur["disabled"]
        if cur["role"] == "owner" and not cur["disabled"] and (final_role != "owner" or final_disabled):
            if not _other_owner(tx, cur["id"]):
                raise Refused("last_owner")
        row = tx.one(
            "update users set name = ?, role = ?, disabled = ?, updated_at = ? where id = ?"
            f" returning {_COLS}",
            (new_name if new_name is not None else cur["name"], final_role, int(final_disabled), db.now(),
             cur["id"]))
        if final_disabled:
            tx.execute("delete from sessions where user_id = ?", (cur["id"],))
        if _rank(final_role) < _rank(TOKEN_MIN_ROLE):
            tx.execute("delete from api_tokens where user_id = ?", (cur["id"],))     # they could not make them now
    return _user(row)


def delete(user_id, *, actor: User | None) -> None:
    with db.transaction() as tx:
        cur = _target(tx, user_id, actor)
        if actor is not None and actor.id == cur["id"]:
            raise Refused("self_action")
        if cur["role"] == "owner" and not cur["disabled"] and not _other_owner(tx, cur["id"]):
            raise Refused("last_owner")
        tx.execute("delete from users where id = ?", (cur["id"],))     # sessions go with it (cascade)


def set_password(user_id, new_password, *, temporary: bool, actor: User | None = None) -> None:
    """Reset a password. Ends every session of that user. A temporary one must be changed at next sign-in, and
    also clears the PIN (a forgotten or exposed password is no reason to keep the quicker way in)."""
    _check_password(new_password)
    pw = hash_secret(new_password)
    with db.transaction() as tx:
        cur = _target(tx, user_id, actor)
        tx.execute(
            "update users set password_hash = ?, must_change_password = ?, updated_at = ?,"
            " pin_hash = case when ? then null else pin_hash end,"
            " pin_failures = case when ? then 0 else pin_failures end where id = ?",
            (pw, int(bool(temporary)), db.now(), int(bool(temporary)), int(bool(temporary)), cur["id"]))
        tx.execute("delete from sessions where user_id = ?", (cur["id"],))


def change_own_password(user: User, current: str, new: str, *, keep_token: str | None = None) -> None:
    """The user changes their own password. Their other sessions end; `keep_token` (the one making this
    request) stays, so they are not signed out of the browser they are using. Clears must_change_password."""
    _check_password(new)
    with db.transaction() as tx:
        row = tx.one("select password_hash from users where id = ?", (user.id,))
        if row is None:
            raise Refused("not_found")
        if not isinstance(current, str) or not verify_secret(current, row["password_hash"]):
            raise Refused("wrong_password")
        if new == current:
            raise Refused("same_password")
        tx.execute("update users set password_hash = ?, must_change_password = 0, updated_at = ?"
                   " where id = ?", (hash_secret(new), db.now(), user.id))
        _end_sessions(tx, user.id, keep_token)


def set_pin(user: User, password: str, pin: str) -> None:
    _check_pin(pin)
    row = db.one("select password_hash from users where id = ?", (user.id,))
    if row is None:
        raise Refused("not_found")
    if not isinstance(password, str) or not verify_secret(password, row["password_hash"]):
        raise Refused("wrong_password")
    db.execute("update users set pin_hash = ?, pin_failures = 0, updated_at = ? where id = ?",
               (hash_secret(pin), db.now(), user.id))


def clear_pin(user_id) -> None:
    with db.transaction() as tx:
        tx.execute("update users set pin_hash = null, pin_failures = 0, updated_at = ? where id = ?",
                   (db.now(), user_id))
        tx.execute("delete from sessions where user_id = ? and via = 'pin'", (user_id,))


def authenticate(username, password) -> User:
    """Refused("bad_login") for an unknown user or a wrong password alike; "disabled" only once the password
    was right (so it tells nothing to someone guessing)."""
    uname = username.strip().lower() if isinstance(username, str) else ""
    row = db.one(f"select {_COLS}, password_hash from users where username = ?", (uname,)) if uname else None
    if row is None or not isinstance(password, str) or len(password) > 200:
        _burn()
        raise Refused("bad_login")
    if not verify_secret(password, row["password_hash"]):
        raise Refused("bad_login")
    if row["disabled"]:
        raise Refused("disabled")
    db.execute("update users set last_login = ? where id = ?", (db.now(), row["id"]))
    return _user(row)


def authenticate_pin(user_id, pin, device_id) -> User:
    """Sign in with a PIN. Only from a trusted device (`device_id` from device_for). The 5th wrong PIN in a
    row clears the PIN: Refused("pin_cleared")."""
    if device_id is None or db.one("select 1 as x from trusted_devices where id = ?", (device_id,)) is None:
        raise Refused("no_device")
    row = db.one(f"select {_COLS}, pin_hash from users where id = ?", (user_id,))
    if row is None or not row["pin_hash"]:
        _burn()
        raise Refused("wrong_pin")
    if row["disabled"]:
        raise Refused("disabled")
    if isinstance(pin, str) and len(pin) <= 16 and verify_secret(pin, row["pin_hash"]):
        db.execute("update users set pin_failures = 0, last_login = ? where id = ?",
                   (db.now(), row["id"]))
        return _user(row)
    with db.transaction() as tx:
        n = tx.one("update users set pin_failures = pin_failures + 1 where id = ? and pin_hash is not null"
                   " returning pin_failures", (row["id"],))
        locked = n is not None and n["pin_failures"] >= PIN_ATTEMPTS
        if locked:
            tx.execute("update users set pin_hash = null, pin_failures = 0 where id = ?", (row["id"],))
            tx.execute("delete from sessions where user_id = ? and via = 'pin'", (row["id"],))
    raise Refused("pin_cleared" if locked else "wrong_pin")


def pin_users() -> list[dict]:
    return db.query("select id, name from users where not disabled and pin_hash is not null"
                    " order by lower(name), id")


# ------------------------------------------------------------------ sessions

def _hash_token(token) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _valid_token(token) -> bool:
    return isinstance(token, str) and 0 < len(token) <= 200


def start_session(user: User, *, via: str, device_id: int | None = None) -> tuple[str, int]:
    """Returns (cookie token, max_age seconds). A "pin" session needs the trusted device it came from."""
    if via not in ("password", "pin"):
        raise ValueError("via must be 'password' or 'pin'")
    if via == "pin" and device_id is None:
        raise ValueError("a pin session needs a device")
    age = PASSWORD_SESSION_SECONDS if via == "password" else PIN_SESSION_SECONDS
    token = secrets.token_urlsafe(32)
    with db.transaction() as tx:
        tx.execute("delete from sessions where expires_at < ?", (db.now(),))      # housekeeping, no timer needed
        tx.execute("insert into sessions (user_id, token_hash, via, device_id, expires_at)"
                   " values (?, ?, ?, ?, ?)",
                   (user.id, _hash_token(token), via, device_id if via == "pin" else None, db.later(age)))
    return token, age


def session_user(token: str) -> User | None:
    if not _valid_token(token):
        return None
    row = db.one(f"select {_COLS_U} from sessions s join users u on u.id = s.user_id"
                 " where s.token_hash = ? and s.expires_at > ? and not u.disabled",
                 (_hash_token(token), db.now()))
    return _user(row) if row else None


def end_session(token: str) -> None:
    if _valid_token(token):
        db.execute("delete from sessions where token_hash = ?", (_hash_token(token),))


def _end_sessions(tx, user_id, except_token) -> None:
    if _valid_token(except_token):
        tx.execute("delete from sessions where user_id = ? and token_hash <> ?",
                   (user_id, _hash_token(except_token)))
    else:
        tx.execute("delete from sessions where user_id = ?", (user_id,))


def end_sessions(user_id, *, except_token: str | None = None) -> None:
    with db.transaction() as tx:
        _end_sessions(tx, user_id, except_token)


# ------------------------------------------------------------------ trusted devices

_DEVICE_COLS = ("d.id, d.name, d.created_at as created, u.name as created_by, d.last_used"
                " from trusted_devices d left join users u on u.id = d.created_by")


def trust_device(name: str, actor: User) -> tuple[str, dict]:
    name = _clean_name(name, "bad_device_name")
    token = secrets.token_urlsafe(32)
    with db.transaction() as tx:
        new = tx.one("insert into trusted_devices (name, token_hash, created_by) values (?, ?, ?)"
                     " returning id", (name, _hash_token(token), actor.id))
        row = tx.one(f"select {_DEVICE_COLS} where d.id = ?", (new["id"],))
    return token, row


def device_for(token: str) -> dict | None:
    """The trusted device a cookie token belongs to ({id, name}), noting that it was used. The note is
    refreshed at most every ten minutes: this runs on every admin page load from a trusted browser, and a
    read should not cost a write each time."""
    if not _valid_token(token):
        return None
    row = db.one("select id, name, (last_used is null or last_used < ?) as stale"
                 " from trusted_devices where token_hash = ?", (db.later(-600), _hash_token(token)))
    if row is None:
        return None
    if row.pop("stale"):
        db.execute("update trusted_devices set last_used = ? where id = ?", (db.now(), row["id"]))
    return row


def list_devices() -> list[dict]:
    return db.query(f"select {_DEVICE_COLS} order by d.created_at, d.id")


def revoke_device(device_id) -> None:
    """Forget a device; the PIN sessions that came from it end with it (cascade)."""
    if db.execute("delete from trusted_devices where id = ?", (device_id,)) == 0:
        raise Refused("not_found")


# ------------------------------------------------------------------ API tokens (#174)
# A token is for scripts: `Authorization: Bearer relay_…` on /api/admin routes. It acts as its user, minus
# TOKEN_BARRED, and is checked against the user row on every request, so turning the user off stops it at
# once and deleting them deletes it. Only the sha256 is stored; the secret is shown once, when it is made.
# `hint` is the first characters after the prefix, so a person can tell which token a script holds.

TOKEN_PREFIX = "relay_"
TOKEN_MIN_ROLE = "admin"               # who holds tokens.own; a user moved below it loses their tokens
TOKEN_EXPIRY_DAYS = (30, 90, 365, None)  # None: never
TOKEN_DEFAULT_DAYS = 90
TOKENS_PER_USER = 25
_HINT_LEN = 6

_TOKEN_COLS = ("t.id, t.name, t.hint, t.created_at as created, t.expires_at as expires, t.last_used,"
               " t.user_id, u.name as user_name, u.username"
               " from api_tokens t join users u on u.id = t.user_id")


def _token_row(tx, token_id) -> dict | None:
    return tx.one(f"select {_TOKEN_COLS} where t.id = ?", (token_id,))


def create_token(user: User, name, days) -> tuple[str, dict]:
    """A new token for `user` (a signed-in person, not a token). Returns (secret, row); the secret is never
    stored or returned again."""
    if user.id <= 0 or isinstance(user, TokenUser) or not user.can("tokens.own"):
        raise Refused("not_allowed")
    name = _clean_name(name, "bad_token_name")
    if isinstance(days, bool) or days not in TOKEN_EXPIRY_DAYS:
        raise Refused("bad_expiry")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = db.later(days * 24 * 3600) if days else None
    with db.transaction() as tx:
        tx.execute("delete from api_tokens where expires_at is not null and expires_at < ?", (db.now(),))
        if tx.one("select count(*) as n from api_tokens where user_id = ?", (user.id,))["n"] >= TOKENS_PER_USER:
            raise Refused("too_many_tokens")
        new = tx.one("insert into api_tokens (user_id, name, token_hash, hint, expires_at)"
                     " values (?, ?, ?, ?, ?) returning id",
                     (user.id, name, _hash_token(secret), secret[len(TOKEN_PREFIX):][:_HINT_LEN], expires))
        row = _token_row(tx, new["id"])
    return secret, row


def _valid_api_token(secret) -> bool:
    return isinstance(secret, str) and secret.startswith(TOKEN_PREFIX) and len(secret) <= 200


def token_user(secret: str) -> TokenUser | None:
    """The user a token acts as, or None when it is unknown, expired, or its user is turned off. Notes when
    it was used, at most once a minute (this runs on every request a script makes)."""
    if not _valid_api_token(secret):
        return None
    row = db.one(f"select {_COLS_U}, t.id as token_id, (t.last_used is null or t.last_used < ?) as stale"
                 " from api_tokens t join users u on u.id = t.user_id"
                 " where t.token_hash = ? and (t.expires_at is null or t.expires_at > ?) and not u.disabled",
                 (db.later(-60), _hash_token(secret), db.now()))
    if row is None:
        return None
    if row["stale"]:
        db.execute("update api_tokens set last_used = ? where id = ?", (db.now(), row["token_id"]))
    base = _user(row)
    return TokenUser(**{**base.__dict__, "token_id": row["token_id"]})


def list_tokens(user_id: int | None = None) -> list[dict]:
    """Tokens that have not expired, newest first: one user's, or everyone's (None)."""
    where, args = "(t.expires_at is null or t.expires_at > ?)", [db.now()]
    if user_id is not None:
        where += " and t.user_id = ?"
        args.append(user_id)
    return db.query(f"select {_TOKEN_COLS} where {where} order by t.created_at desc, t.id desc", args)


def revoke_token(token_id, actor: User) -> None:
    """Anyone may revoke their own tokens; only tokens.manage (Owners) may revoke someone else's. A token
    that is not yours and not revocable by you is "not found", so its existence is not given away."""
    with db.transaction() as tx:
        row = tx.one("select user_id from api_tokens where id = ?", (token_id,))
        if row is None or (row["user_id"] != actor.id and not actor.can("tokens.manage")):
            raise Refused("not_found")
        tx.execute("delete from api_tokens where id = ?", (token_id,))
