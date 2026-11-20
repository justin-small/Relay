"""Signing Relay in to another service (#304): YouTube, Facebook and whatever comes next.

The operator presses **Connect** on a module's page and gets a link to the service's own sign-in. Relay, on a
church's own computer, has no address the service can send the browser back to, so the service sends it to Relay's
cloud (relay-cloud, `CLOUD`, at `/oauth/<service>/callback`), which holds Relay's app secrets, swaps the sign-in for
tokens and keeps them until this install, polling with a secret only it was given, collects them once. Refreshing
needs the app secret too, so it goes through relay-cloud as well. Relay keeps the tokens, refreshes them before they
run out, and says on the module's page and in its health whether the account is still connected.

A church that uses its own app instead (the module's settings, "bring your own app") signs in with OAuth 2.0 device
authorization (RFC 8628): the page shows a short code to enter at the service's address (google.com/device) on any
phone or computer, and Relay talks to the service directly with the church's client. An account remembers which way
it was connected ("via": "cloud"), since its refresh token only works with the app that issued it.

Nothing here knows any one service. A module describes its service as a `Provider` (the defaults follow the RFC,
which Google does; a module overrides what its service does differently) and keeps one `Account` per connection:

    from ... import oauth

    class Google(oauth.Provider):
        id, name = "google", "YouTube"
        cloud = "google"                                 # its name at relay-cloud
        device_url = "https://oauth2.googleapis.com/device/code"
        token_url = "https://oauth2.googleapis.com/token"
        revoke_url = "https://oauth2.googleapis.com/revoke"
        async def identity(self, tokens): ...           # {"id": ..., "name": ...}, the account signed in

    ACCOUNT = oauth.Account("youtube", Google("youtube"), settings_key="youtube",
                            client=church_client, scopes=("...youtube.readonly",))   # Client("") means Relay's app
    oauth.add_routes(router, "/api/admin/youtube/account", ACCOUNT,
                     view=require("youtube.view"), manage=require("youtube.manage"))

What is kept: the tokens, the account's name, the scopes and who connected it, as one JSON value in the module's own
settings (`field`, "account" by default), which the module lists in `secret_settings` so config.json keeps it sealed
like the OpenAI key (app/secretbox.py). Its `public()` drops it; the page reads `Account.status()`, which never holds a
token. A refreshed access token is kept in memory only, so config.json is not rewritten every hour.

Every message here is a fixed sentence: a service's own error text never reaches a page or the log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from fastapi import APIRouter, Depends, Request

from . import audit, config, netsock

log = logging.getLogger("relay.oauth")

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
EARLY = 120             # refresh this many seconds before the access token runs out
MAX_WAIT = 30 * 60      # never wait longer than this for a code to be entered, whatever the service says
SLOW_DOWN = 5           # RFC 8628 3.5: add this to the interval when told to slow down
MIN_INTERVAL = 1        # never ask more often than this, whatever the service says

# Relay's cloud, where every service sends the sign-in back to. A plain constant: CodeQL treats a value read from
# an environment variable named like OAUTH as a password, and fails a test that prints it.
CLOUD = "https://cloud.relaysystems.dev"
CLOUD_DOWN = "Relay's sign-in service did not answer. Try again in a minute."

# The states an account is in, as `status()["state"]` gives them.
DISCONNECTED, WAITING, CONNECTED, SIGN_IN = "disconnected", "waiting", "connected", "sign_in"


class Problem(Exception):
    """A sign-in step failed. The text is a fixed sentence, safe to show the operator."""


class NeedsSignIn(Problem):
    """The service no longer accepts Relay's token (revoked, password changed, Page role lost, expired)."""


@dataclass(frozen=True)
class Client:
    """The church's own app (bring your own app), used with the device flow. An empty id means Relay's own app,
    whose secret only relay-cloud holds."""
    id: str
    secret: str = ""


@dataclass
class Code:
    """What the operator is shown while Relay waits: enter `user_code` at `url` (device flow), or, through relay-cloud
    (`session` set), open `url` and sign in. `poll` is the secret that collects the tokens; it never leaves Relay."""
    device_code: str
    user_code: str
    url: str
    expires_at: float
    interval: float
    url_complete: str = ""
    session: str = ""
    poll: str = ""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str = ""
    expires_at: float = 0.0          # 0: the service gave no expiry (a Facebook long-lived Page token)
    scope: str = ""
    extra: dict = field(default_factory=dict)   # what a provider keeps beside the tokens (a Page id and token)

    def stale(self, now: float | None = None) -> bool:
        return bool(self.expires_at) and (now or time.time()) >= self.expires_at - EARLY


class Provider:
    """One service's sign-in. The defaults are RFC 8628 with a refresh token (Google). Every call goes out through
    `netsock.fetch` on the module's traffic class, so it follows the network port the operator chose."""
    id = ""                 # lower_snake_case, for the log and the audit entry ("google")
    name = ""               # what the operator reads ("YouTube")
    cloud = ""              # its name at relay-cloud ("google"); "" when it signs in only with the church's own app
    device_url = ""
    token_url = ""
    revoke_url = ""
    send_secret = True      # Google wants the client secret on the token calls; RFC public clients do not

    def __init__(self, traffic: str) -> None:
        self.traffic = traffic

    # ---------------------------------------------------------------- transport
    async def call(self, url: str, form: dict | None = None, *, token: str = "") -> tuple[int, dict]:
        """POST `form` (or GET when None) to `url`; (status, JSON object). A connection that fails is a Problem."""
        if not url.startswith("https://"):
            raise Problem(f"{self.name} sign-in is not set up in this copy of Relay.")
        # Cloudflare (in front of relay-cloud) refuses Python's default User-Agent with a 403, so name Relay.
        headers = {"User-Agent": "Relay"} | ({"Authorization": f"Bearer {token}"} if token else {})
        try:
            status, body = await asyncio.to_thread(netsock.fetch, self.traffic, url, form=form, headers=headers)
        except OSError:
            raise Problem(netsock.failure(self.traffic, f"Could not reach {self.name}.")) from None
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        return status, data if isinstance(data, dict) else {}

    def client_form(self, client: Client) -> dict:
        form = {"client_id": client.id}
        if self.send_secret and client.secret:
            form["client_secret"] = client.secret
        return form

    # ---------------------------------------------------------------- the steps a module may override
    async def device_code(self, client: Client, scopes: Iterable[str]) -> Code:
        status, d = await self.call(self.device_url, {"client_id": client.id, "scope": " ".join(scopes)})
        if status != 200 or not d.get("device_code") or not d.get("user_code"):
            log.warning("oauth: %s refused a sign-in code (HTTP %s, %s)", self.id, status, _err(d))
            raise Problem(f"{self.name} did not give a sign-in code. Try again in a minute.")
        return Code(device_code=str(d["device_code"]), user_code=str(d["user_code"]),
                    url=str(d.get("verification_url") or d.get("verification_uri") or ""),
                    url_complete=str(d.get("verification_uri_complete") or ""),
                    expires_at=time.time() + min(_num(d.get("expires_in"), 600), MAX_WAIT),
                    interval=max(_num(d.get("interval"), 5), MIN_INTERVAL))

    async def poll(self, client: Client, code: Code) -> Tokens | str:
        """Tokens once the operator has said yes; otherwise "pending", "slow_down", "denied" or "expired"."""
        form = self.client_form(client) | {"device_code": code.device_code, "grant_type": DEVICE_GRANT}
        status, d = await self.call(self.token_url, form)
        if status == 200 and d.get("access_token"):
            return self.tokens(d)
        err = _err(d)
        if err in ("authorization_pending", "slow_down"):
            return "pending" if err == "authorization_pending" else "slow_down"
        if err == "access_denied":
            return "denied"
        if err == "expired_token":
            return "expired"
        log.warning("oauth: %s refused the sign-in (HTTP %s, %s)", self.id, status, err)
        raise Problem(f"{self.name} refused the sign-in. Try again.")

    def tokens(self, d: dict, old: Tokens | None = None) -> Tokens:
        """Tokens from a token response; a refresh that gives no new refresh token keeps the old one."""
        expires = _num(d.get("expires_in"), 0)
        return Tokens(access_token=str(d["access_token"]),
                      refresh_token=str(d.get("refresh_token") or (old.refresh_token if old else "")),
                      expires_at=time.time() + expires if expires else 0.0,
                      scope=str(d.get("scope") or (old.scope if old else "")),
                      extra=dict(old.extra) if old else {})

    async def refresh(self, client: Client, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise NeedsSignIn(f"{self.name} needs signing in again.")
        form = self.client_form(client) | {"refresh_token": tokens.refresh_token, "grant_type": "refresh_token"}
        status, d = await self.call(self.token_url, form)
        return self._refreshed(status, d, tokens)

    def _refreshed(self, status: int, d: dict, tokens: Tokens) -> Tokens:
        if status == 200 and d.get("access_token"):
            return self.tokens(d, tokens)
        if _err(d) in ("invalid_grant", "unauthorized_client", "invalid_client") or status == 401:
            raise NeedsSignIn(f"{self.name} needs signing in again.")
        log.warning("oauth: %s refused a refresh (HTTP %s, %s)", self.id, status, _err(d))
        raise Problem(f"{self.name} did not answer as expected. Relay will try again.")

    # ---------------------------------------------------------------- through relay-cloud
    def cloud_url(self, step: str) -> str:
        return f"{CLOUD}/v1/oauth/{self.cloud}/{step}"

    async def cloud_start(self, scopes: Iterable[str]) -> Code:
        """Ask relay-cloud for a sign-in link; the operator opens it and the service comes back to relay-cloud."""
        try:
            status, d = await self.call(self.cloud_url("start"), {"scopes": " ".join(scopes)})
        except Problem:
            raise Problem(CLOUD_DOWN) from None
        url, session, poll = str(d.get("url") or ""), str(d.get("session") or ""), str(d.get("poll") or "")
        if status != 201 or not url.startswith("https://") or not session or not poll:
            log.warning("oauth: relay-cloud did not start a %s sign-in (HTTP %s, %s)", self.id, status, _err(d))
            raise Problem(CLOUD_DOWN)
        return Code(device_code="", user_code="", url=url, session=session, poll=poll,
                    expires_at=time.time() + min(_num(d.get("expires_in"), 900), MAX_WAIT),
                    interval=max(_num(d.get("interval"), 2), MIN_INTERVAL))

    async def cloud_poll(self, code: Code) -> Tokens | str:
        """Like poll(): tokens once the operator has signed in, else "pending", "denied" or "expired". A failed
        request is "pending" (asked again), so a moment without network does not end the sign-in."""
        try:
            status, d = await self.call(self.cloud_url("result"), {"session": code.session, "poll": code.poll})
        except Problem:
            return "pending"
        state = d.get("status") if status == 200 else ""
        t = d.get("tokens")
        if state == "done" and isinstance(t, dict) and t.get("access_token"):
            return self.tokens(t)
        if state in ("denied", "expired"):
            return state
        if state == "failed":
            raise Problem(f"{self.name} did not finish the sign-in. Press Connect to try again.")
        return "pending"

    async def cloud_refresh(self, tokens: Tokens) -> Tokens:
        if not tokens.refresh_token:
            raise NeedsSignIn(f"{self.name} needs signing in again.")
        status, d = await self.call(self.cloud_url("refresh"), {"refresh_token": tokens.refresh_token})
        return self._refreshed(status, d, tokens)

    async def revoke(self, client: Client, tokens: Tokens) -> bool:
        """Ask the service to forget Relay's access. Best effort: True when it said so."""
        if not self.revoke_url:
            return False
        status, _ = await self.call(self.revoke_url, {"token": tokens.refresh_token or tokens.access_token})
        return status == 200

    async def identity(self, tokens: Tokens) -> dict:
        """{"id": ..., "name": ...} of the account that signed in (a channel, a Page). May fill `tokens.extra`."""
        raise NotImplementedError


class Account:
    """One connection of a module to its service: Connect, wait, connected, refresh, disconnect."""

    def __init__(self, module_id: str, provider: Provider, *, settings_key: str, field: str = "account",
                 client: Callable[[], Client], scopes: Callable[[], Iterable[str]] | Iterable[str],
                 on_change: Callable[[], None] | None = None) -> None:
        self.module_id = module_id
        self.on_change = on_change          # called after connecting and disconnecting (check the service now)
        self.provider = provider
        self.settings_key = settings_key
        self.field = field
        self._client = client
        self._scopes = scopes
        self._raw: str | None = None        # the stored value last read, to notice a change (a restore)
        self._record: dict = {}
        self._tokens: Tokens | None = None
        self._needs_sign_in = False
        self._code: Code | None = None
        self._task: asyncio.Task | None = None
        self._message = ""                  # the last thing to tell the operator ("Sign-in was declined.")
        self._lock: asyncio.Lock | None = None
        self._lock_loop: Any = None

    # ---------------------------------------------------------------- what is kept
    def client(self) -> Client:
        return self._client()

    def scopes(self) -> list[str]:
        s = self._scopes() if callable(self._scopes) else self._scopes
        return [str(x) for x in s]

    def _load(self) -> dict:
        raw = (config.get().get(self.settings_key) or {}).get(self.field) or ""
        if raw != self._raw:
            self._raw = raw
            self._needs_sign_in = False
            try:
                rec = json.loads(raw) if raw else {}
            except ValueError:
                log.warning("oauth: the stored %s account could not be read; it needs connecting again", self.module_id)
                rec = {}
            self._record = rec if isinstance(rec, dict) else {}
            t = self._record.get("tokens")
            self._tokens = Tokens(**{k: t[k] for k in Tokens.__dataclass_fields__ if k in t}) \
                if isinstance(t, dict) and t.get("access_token") else None
        return self._record

    def _store(self, record: dict) -> None:
        raw = json.dumps(record, separators=(",", ":")) if record else ""
        config.save({self.settings_key: {self.field: raw}})
        self._raw = None
        self._load()

    def connected(self) -> bool:
        self._load()
        return self._tokens is not None

    def status(self) -> dict:
        """For the module's page: never a token."""
        rec = self._load()
        if self._code and self._task and not self._task.done():
            state = WAITING
        elif self._tokens is None:
            state = DISCONNECTED
        else:
            state = SIGN_IN if self._needs_sign_in else CONNECTED
        out: dict[str, Any] = {"state": state, "service": self.provider.name, "message": self._message}
        if self._tokens is not None:
            out.update(account=str(rec.get("name") or ""), scopes=sorted(set(self._tokens.scope.split())),
                       connected_at=rec.get("connected_at") or 0, connected_by=str(rec.get("connected_by") or ""))
        if state == WAITING and self._code:
            out.update(code=self._code.user_code, url=self._code.url, url_complete=self._code.url_complete,
                       expires_at=self._code.expires_at)
        return out

    def health(self) -> tuple[str, str] | None:
        """("warn", sentence) when the account needs the operator; None while it is fine or was never connected."""
        self._load()
        if self._tokens is not None and self._needs_sign_in:
            return "warn", f"{self.provider.name} needs signing in again."
        return None

    # ---------------------------------------------------------------- connect
    async def connect(self, actor: Any = None) -> dict:
        """Start a sign-in (a link through relay-cloud, or a device code with the church's own app) and wait for
        the operator to finish it. Replaces a wait already running."""
        await self.cancel()
        self._message = ""
        client = self.client()
        if client.id:
            self._code = await self.provider.device_code(client, self.scopes())
        elif self.provider.cloud:
            self._code = await self.provider.cloud_start(self.scopes())
        else:
            raise Problem(f"{self.provider.name} sign-in is not set up in this copy of Relay.")
        self._task = asyncio.create_task(self._wait(self._code, client, actor))
        return self.status()

    async def cancel(self) -> None:
        task, self._task, self._code = self._task, None, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def close(self) -> None:
        """The module is stopping: stop waiting for a code."""
        await self.cancel()

    async def _wait(self, code: Code, client: Client, actor: Any) -> None:
        interval = code.interval
        try:
            while time.time() < code.expires_at:
                await asyncio.sleep(interval)
                try:
                    got = await (self.provider.cloud_poll(code) if code.session else self.provider.poll(client, code))
                except Problem as e:
                    self._message = str(e)
                    return
                if got == "slow_down":
                    interval += SLOW_DOWN
                elif got == "denied":
                    self._message = "Sign-in was declined."
                    return
                elif got == "expired":
                    break
                elif isinstance(got, Tokens):
                    await self._connected(got, actor, via="cloud" if code.session else "device")
                    return
            self._message = ("The sign-in link ran out before it was used. Press Connect for a new one."
                             if code.session else "The code ran out before it was entered. Press Connect for a new one.")
        finally:
            if self._code is code:
                self._code = None

    async def _connected(self, tokens: Tokens, actor: Any, via: str = "device") -> None:
        try:
            who = await self.provider.identity(tokens)
        except Problem as e:
            self._message = str(e)
            await self._revoke_quietly(tokens)
            return
        name = str(who.get("name") or who.get("id") or "")
        self._store({"tokens": tokens.__dict__, "id": str(who.get("id") or ""), "name": name, "via": via,
                     "connected_at": int(time.time()), "connected_by": str(getattr(actor, "name", "") or "")})
        self._message = ""
        audit.record(f"{self.module_id}.connect", f"Connected {self.provider.name}", actor=actor, target=name,
                     detail={"scopes": tokens.scope})
        log.info("oauth: %s connected as %s", self.module_id, name)
        self._changed()

    def _changed(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                log.exception("oauth: %s on_change failed", self.module_id)

    # ---------------------------------------------------------------- use
    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    async def token(self) -> str:
        """A current access token, refreshed when it is about to run out. Raises NeedsSignIn when the service no
        longer accepts Relay, and Problem when it could not be asked (Relay will be asked again next time)."""
        async with self._guard():
            self._load()
            t = self._tokens
            if t is None:
                raise NeedsSignIn(f"{self.provider.name} is not connected.")
            if self._needs_sign_in:
                raise NeedsSignIn(f"{self.provider.name} needs signing in again.")
            if not t.stale():
                return t.access_token
            if not t.refresh_token:
                self._signed_out()
                raise NeedsSignIn(f"{self.provider.name} needs signing in again.")
            try:
                # A refresh token works only with the app that issued it: Relay's (through relay-cloud) or the church's.
                if self._record.get("via") == "cloud":
                    new = await self.provider.cloud_refresh(t)
                elif self.client().id:
                    new = await self.provider.refresh(self.client(), t)
                else:       # the church's app was removed (or the account came from an app this copy no longer has)
                    raise NeedsSignIn(f"{self.provider.name} needs signing in again.")
            except NeedsSignIn:
                self._signed_out()
                raise
            if new.refresh_token != t.refresh_token:        # a rotated refresh token must be kept
                rec = dict(self._record, tokens=new.__dict__)
                self._store(rec)
            self._tokens = new
            return new.access_token

    def refused(self) -> None:
        """The service refused a token Relay thought was good (HTTP 401): refresh before the next call, or, with
        nothing to refresh with, ask the operator to sign in again."""
        self._load()
        if self._tokens is None:
            return
        if self._tokens.refresh_token:
            self._tokens.expires_at = 1.0
        else:
            self._signed_out()

    def _signed_out(self) -> None:
        if not self._needs_sign_in:
            self._needs_sign_in = True
            log.warning("oauth: %s needs signing in again", self.module_id)
            audit.record(f"{self.module_id}.signed_out", f"{self.provider.name} stopped accepting Relay's sign-in",
                         target=str(self._record.get("name") or ""))

    # ---------------------------------------------------------------- disconnect
    async def disconnect(self, request: Request | None = None) -> dict:
        """Forget the account here and ask the service to forget Relay's access."""
        await self.cancel()
        rec = self._load()
        t, name = self._tokens, str(rec.get("name") or "")
        self._store({})
        self._message = ""
        if t is not None:
            revoked = await self._revoke_quietly(t)
            if not revoked:
                self._message = (f"Relay forgot the account, but {self.provider.name} did not confirm it removed "
                                 "Relay's access. Remove it from the account's security settings.")
            if request is not None:
                audit.note(request, f"{self.module_id}.disconnect", f"Disconnected {self.provider.name}",
                           target=name, detail={"revoked": revoked})
        self._changed()
        return self.status()

    async def _revoke_quietly(self, t: Tokens) -> bool:
        try:
            return await self.provider.revoke(self.client(), t)
        except Problem:
            return False


def add_routes(router: APIRouter, prefix: str, account: Account, *, view: Callable, manage: Callable) -> None:
    """The Connect / Disconnect routes for one account, on the module's own router under its own prefix and
    permissions: GET `prefix` (status), POST `prefix`/connect, /cancel and /disconnect."""

    @router.get(prefix, dependencies=[Depends(view)])
    def account_status() -> dict:
        return account.status()

    @router.post(prefix + "/connect", dependencies=[Depends(manage)])
    async def account_connect(request: Request) -> dict:
        try:
            out = await account.connect(getattr(request.state, "user", None))
        except Problem as e:
            return {"ok": False, "error": str(e)} | account.status()
        audit.note(request, f"{account.module_id}.connect_start", f"Started connecting {account.provider.name}")
        return {"ok": True} | out

    @router.post(prefix + "/cancel", dependencies=[Depends(manage)])
    async def account_cancel() -> dict:
        await account.cancel()
        return {"ok": True} | account.status()

    @router.post(prefix + "/disconnect", dependencies=[Depends(manage)])
    async def account_disconnect(request: Request) -> dict:
        return {"ok": True} | await account.disconnect(request)


def _num(v: Any, default: float) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return float(default)
    return n if n > 0 else float(default)


def _err(d: dict) -> str:
    """The OAuth error code, a short fixed token like "invalid_grant" (never the description, which is free text)."""
    e = d.get("error")
    if isinstance(e, dict):              # Graph API errors are objects
        return str(e.get("code") or "")[:40]
    return str(e or "")[:40] if str(e or "").replace("_", "").isalnum() else ""
