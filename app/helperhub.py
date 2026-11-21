"""Relay Helpers (#497): small programs on show computers (native/relay-helper) that dial out to Relay and do what Relay
cannot do from another machine. This is Relay's end of that connection: one-use pairing codes, a token per helper,
the open connections, and `call()` for the rest of Relay to ask a helper's capability to do something.

A helper is not an integration with another product, so this is core; a module that needs one (ProPresenter today)
asks `call("propresenter", "open_files", {...})` and never imports the helper's code. The protocol is in
docs/helper.md. Nothing a helper sends is stored except its name, system and version; its token is kept as a sha256
(like the API tokens in users.py), shown once, when the helper pairs.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import config

CODE_TTL_S = 600
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"      # no 0/O, 1/I/L
MAX_HELPERS = 32
MAX_CODES = 8
PAIR_FAILS_MAX = 10                  # wrong codes in PAIR_FAIL_WINDOW_S, then pairing waits
PAIR_FAIL_WINDOW_S = 600
FIRST_FRAME_TIMEOUT_S = 10.0
CALL_TIMEOUT_S = 30.0
MAX_FRAME = 24 * 1024 * 1024
MAX_PENDING = 64                     # calls waiting on one helper
NAME_MAX = 60
_ID = re.compile(r"^[0-9a-f]{16}$")


class HelperError(Exception):
    """Something Relay can say to the operator; `message` is a fixed sentence."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Socket(Protocol):
    async def receive_text(self) -> str: ...
    async def send_text(self, data: str) -> None: ...
    async def close(self, code: int = 1000) -> None: ...


def _now() -> float:
    return time.time()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_name(raw: Any, fallback: str) -> str:
    name = " ".join(str(raw or "").split())[:NAME_MAX]
    return name or fallback


@dataclass
class Connection:
    ws: Socket
    helper_id: str
    name: str
    os: str
    version: str
    caps: list[dict]
    connected_at: float = field(default_factory=_now)
    pending: dict[int, "asyncio.Future[dict]"] = field(default_factory=dict)
    next_id: int = 1

    def has(self, cap: str, op: str | None = None) -> bool:
        for c in self.caps:
            if c.get("id") == cap and (op is None or op in (c.get("ops") or [])):
                return True
        return False


class Hub:
    def __init__(self) -> None:
        self.codes: dict[str, float] = {}
        self.connections: dict[str, Connection] = {}
        self.fails: list[float] = []
        self.on_change: Any = None

    # ---------------------------------------------------------------- what is stored
    @staticmethod
    def stored() -> dict[str, dict[str, Any]]:
        return config.get().get("helpers") or {}

    @staticmethod
    def _save(stored: dict[str, dict[str, Any]]) -> None:
        config.save({"helpers": stored}, replace=("helpers",))

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # a listener must never break the connection
                pass

    # ---------------------------------------------------------------- pairing
    def make_code(self) -> dict:
        """A one-use code the operator types into `relay-helper pair`. Valid for ten minutes."""
        now = _now()
        self.codes = {c: t for c, t in self.codes.items() if t > now}
        if len(self.stored()) >= MAX_HELPERS:
            raise HelperError("This Relay already has as many helpers as it allows. Remove one first.")
        if len(self.codes) >= MAX_CODES:
            raise HelperError("Several pairing codes are already waiting. Use one or wait a few minutes.")
        raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = f"{raw[:4]}-{raw[4:]}"
        self.codes[code] = now + CODE_TTL_S
        return {"code": code, "expires_in": CODE_TTL_S}

    def _blocked(self) -> bool:
        cutoff = _now() - PAIR_FAIL_WINDOW_S
        self.fails = [t for t in self.fails if t > cutoff]
        return len(self.fails) >= PAIR_FAILS_MAX

    def pair(self, code: Any, name: Any, os_name: Any, version: Any) -> dict:
        """Trade a code for a token. A code works once; ten wrong ones in ten minutes stop pairing for a while."""
        if self._blocked():
            raise HelperError("Too many wrong pairing codes. Wait a few minutes and make a new code.")
        norm = str(code or "").strip().upper().replace(" ", "")
        now = _now()
        found = None
        for known, expires in list(self.codes.items()):
            if hmac.compare_digest(known, norm) and expires > now:
                found = known
        if found is None:
            self.fails.append(now)
            raise HelperError("That pairing code is not right or has run out. Make a new one on the Helpers page.")
        del self.codes[found]
        stored = self.stored()
        if len(stored) >= MAX_HELPERS:
            raise HelperError("This Relay already has as many helpers as it allows. Remove one first.")
        helper_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(32)
        stored[helper_id] = {"name": _clean_name(name, "Show computer"), "token_hash": _hash(token),
                             "os": _clean_name(os_name, "")[:20], "version": _clean_name(version, "")[:20],
                             "created": _now()}
        self._save(stored)
        self._changed()
        return {"helper_id": helper_id, "token": token}

    def _authenticate(self, helper_id: Any, token: Any) -> dict | None:
        rec = self.stored().get(str(helper_id)) if _ID.match(str(helper_id or "")) else None
        if rec is None or not isinstance(token, str) or not token:
            return None
        return rec if hmac.compare_digest(str(rec.get("token_hash") or ""), _hash(token)) else None

    # ---------------------------------------------------------------- the connection
    async def serve(self, ws: Socket) -> None:
        """Run one helper's connection from its first frame until it ends. Never raises."""
        try:
            first = json.loads(await asyncio.wait_for(ws.receive_text(), FIRST_FRAME_TIMEOUT_S))
        except Exception:
            await _quietly(ws.close(1008))
            return
        kind = first.get("type") if isinstance(first, dict) else None
        if kind == "pair":
            try:
                out = self.pair(first.get("code"), first.get("name"), first.get("os"), first.get("version"))
                await ws.send_text(json.dumps({"type": "paired", **out}))
            except HelperError as exc:
                await ws.send_text(json.dumps({"type": "refused", "reason": exc.message}))
            await _quietly(ws.close(1000))
            return
        if kind != "hello" or self._authenticate(first.get("helper_id"), first.get("token")) is None:
            await _quietly(ws.send_text(json.dumps({"type": "refused", "reason": "This helper is not paired with this Relay."})))
            await _quietly(ws.close(1008))
            return
        helper_id = str(first["helper_id"])
        conn = Connection(ws=ws, helper_id=helper_id, name=_clean_name(first.get("name"), "Show computer"),
                          os=_clean_name(first.get("os"), "")[:20], version=_clean_name(first.get("version"), "")[:20],
                          caps=_clean_caps(first.get("capabilities")))
        old = self.connections.get(helper_id)
        if old is not None:                    # the same helper dialling again: the old socket is dead or stale
            await self._drop(old, "replaced")
        self.connections[helper_id] = conn
        self._remember(conn)
        self._changed()
        await _quietly(ws.send_text(json.dumps({"type": "welcome", "relay_version": os.environ.get("RELAY_VERSION") or "dev"})))
        try:
            while True:
                text = await ws.receive_text()
                if len(text) > MAX_FRAME:
                    break
                self._on_frame(conn, text)
        except Exception:
            pass
        finally:
            if self.connections.get(helper_id) is conn:
                del self.connections[helper_id]
            await self._drop(conn, "closed")
            self._changed()

    def _remember(self, conn: Connection) -> None:
        stored = self.stored()
        rec = stored.get(conn.helper_id)
        if rec is None:
            return
        if (rec.get("os"), rec.get("version")) != (conn.os, conn.version) or not rec.get("name"):
            rec.update({"os": conn.os, "version": conn.version, "name": rec.get("name") or conn.name})
            self._save(stored)

    def _on_frame(self, conn: Connection, text: str) -> None:
        try:
            msg = json.loads(text)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        if msg.get("type") == "result":
            fut = conn.pending.pop(msg.get("id"), None) if isinstance(msg.get("id"), int) else None
            if fut is not None and not fut.done():
                fut.set_result(msg)
        # "pong" and "event" need no answer; events have no listener yet

    async def _drop(self, conn: Connection, why: str) -> None:
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(HelperError("The helper disconnected before it answered."))
        conn.pending.clear()
        if why == "replaced":
            await _quietly(conn.ws.close(1000))

    # ---------------------------------------------------------------- calling a helper
    def find(self, cap: str, op: str | None = None, helper_id: str | None = None) -> Connection | None:
        """The connected helper that can do `cap` (the named one, or the one that has been connected longest)."""
        options = [c for c in self.connections.values() if c.has(cap, op) and (helper_id is None or c.helper_id == helper_id)]
        return min(options, key=lambda c: c.connected_at) if options else None

    async def call(self, cap: str, op: str, args: Any = None, *, helper_id: str | None = None,
                   timeout: float = CALL_TIMEOUT_S) -> Any:
        """Ask a helper to run one operation and return its `data`. Raises HelperError with a fixed sentence when no
        helper can, it fails, or it does not answer."""
        conn = self.find(cap, op, helper_id)
        if conn is None:
            raise HelperError(f"No connected helper can do '{cap}'. Install the Relay Helper on that computer and pair it.")
        if len(conn.pending) >= MAX_PENDING:
            raise HelperError("The helper is busy. Try again in a moment.")
        call_id = conn.next_id
        conn.next_id += 1
        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        conn.pending[call_id] = fut
        try:
            await conn.ws.send_text(json.dumps({"type": "call", "id": call_id, "cap": cap, "op": op, "args": args if args is not None else {}}))
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise HelperError("The helper did not answer in time.") from None
        except HelperError:
            raise
        except Exception:
            raise HelperError("The helper could not be reached.") from None
        finally:
            conn.pending.pop(call_id, None)
        if not msg.get("ok"):
            # The helper words its own errors as sentences for the operator (and never carries secrets).
            raise HelperError(str(msg.get("error") or "The helper could not do that.")[:300])
        return msg.get("data")

    # ---------------------------------------------------------------- what the admin page shows
    def view(self) -> list[dict]:
        out = []
        for hid, rec in self.stored().items():
            conn = self.connections.get(hid)
            out.append({
                "id": hid, "name": rec.get("name") or "Show computer", "os": rec.get("os") or "",
                "version": rec.get("version") or "", "created": rec.get("created"),
                "online": conn is not None,
                "connected_at": conn.connected_at if conn else None,
                "capabilities": conn.caps if conn else [],
            })
        out.sort(key=lambda h: (not h["online"], h["name"].lower()))
        return out

    def pending_codes(self) -> int:
        now = _now()
        return sum(1 for t in self.codes.values() if t > now)

    async def remove(self, helper_id: str) -> dict | None:
        """Forget a helper: its token stops working and a live connection is closed. Returns what was removed."""
        stored = self.stored()
        rec = stored.pop(helper_id, None)
        if rec is None:
            return None
        self._save(stored)
        conn = self.connections.pop(helper_id, None)
        if conn is not None:
            await _quietly(conn.ws.send_text(json.dumps({"type": "refused", "reason": "This helper was removed from Relay."})))
            await self._drop(conn, "replaced")
        self._changed()
        return rec

    def rename(self, helper_id: str, name: Any) -> dict | None:
        stored = self.stored()
        rec = stored.get(helper_id)
        if rec is None:
            return None
        rec["name"] = _clean_name(name, rec.get("name") or "Show computer")
        self._save(stored)
        self._changed()
        return rec


def _clean_caps(raw: Any) -> list[dict]:
    """What a helper says it can do, cut down to ids, versions and operation names of sane size."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for c in raw[:32]:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", cid):
            continue
        ops = [str(o) for o in (c.get("ops") or []) if isinstance(o, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,31}", o)][:64]
        version = c.get("version") if isinstance(c.get("version"), int) else 0
        out.append({"id": cid, "version": version, "ops": ops})
    return out


async def _quietly(coro: Any) -> None:
    try:
        await coro
    except Exception:
        pass


hub = Hub()
