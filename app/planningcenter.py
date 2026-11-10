"""Planning Center: the one small client every call to it goes through.

Relay signs in to one church's account with a Personal Access Token (an
application ID and a secret). Every call runs as the person who made the token
and only within their permissions. Nothing is sent until a token is saved.

Stdlib HTTP only, like the text translator (`httpx` is not in requirements.txt).
The secret is used for the Authorization header and nowhere else: it is not
logged, not put in an exception message, and `config.redacted()` withholds it
from the browser.

Rate limits are read from the response (`X-PCO-API-Request-Rate-*`), never
hard-coded: the default is 100 requests per 20 seconds per user, but the number
is Planning Center's to change. A 429 is waited out for the `Retry-After` it
names and retried; if it keeps coming the caller gets `RateLimited`.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .realtime import _SSL

log = logging.getLogger("relay.planningcenter")

BASE_URL = "https://api.planningcenteronline.com"
API_VERSION = "2018-11-01"  # sent as X-PCO-API-Version on every Services request
TIMEOUT_S = 15.0
ATTEMPTS = 3          # tries per call when Planning Center answers 429
MAX_WAIT_S = 60.0     # never sleep longer than this for one Retry-After
DEFAULT_WAIT_S = 20.0  # when a 429 names no usable Retry-After: the default window
PAGE_SIZE = 100
MAX_PAGES = 20        # a runaway "next" chain must end somewhere

_sleep = time.sleep   # a test swaps this so a 429 does not take real seconds


class PlanningCenterError(Exception):
    """A call that did not work. `kind` is "auth", "rate", "network" or "http";
    the message is safe to show the operator (it never holds the secret)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def user_agent() -> str:
    """Names Relay and its version; requests without a User-Agent can get 403."""
    version = re.sub(r"[^\w.+-]", "", os.environ.get("RELAY_VERSION") or "") or "dev"
    return f"Relay/{version} (church caption relay)"


def _retry_after(headers: Any, period: float | None) -> float:
    raw = (headers.get("Retry-After") or "").strip()
    try:
        wait = float(raw)
    except ValueError:
        wait = period or DEFAULT_WAIT_S
    return max(0.0, min(MAX_WAIT_S, wait))


class Client:
    def __init__(self, app_id: str, secret: str, *, base_url: str | None = None):
        self._app_id = app_id
        self._secret = secret
        self.base_url = (base_url or BASE_URL).rstrip("/")
        # Last rate-limit reading: what Planning Center said, not what we assume.
        self.rate: dict[str, int | None] = {"count": None, "limit": None, "period": None}

    # -- plumbing -----------------------------------------------------
    def _headers(self) -> dict[str, str]:
        token = base64.b64encode(f"{self._app_id}:{self._secret}".encode("utf-8")).decode("ascii")
        return {
            "Authorization": f"Basic {token}",
            "X-PCO-API-Version": API_VERSION,
            "User-Agent": user_agent(),
            "Accept": "application/json",
        }

    def _note_rate(self, headers: Any) -> None:
        # The period arrives as "20 seconds", the other two as bare numbers.
        for key, name in (("count", "Count"), ("limit", "Limit"), ("period", "Period")):
            m = re.match(r"\s*(\d+)", headers.get(f"X-PCO-API-Request-Rate-{name}") or "")
            if m:
                self.rate[key] = int(m.group(1))

    def _clean(self, text: str) -> str:
        return text.replace(self._secret, "…") if self._secret else text

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in range(1, ATTEMPTS + 1):
            # Already at the ceiling for this window: wait for it to roll over
            # rather than spend a request on a certain 429.
            count, limit, period = (self.rate[k] for k in ("count", "limit", "period"))
            if count is not None and limit is not None and count >= limit:
                _sleep(max(0.0, min(MAX_WAIT_S, float(period or DEFAULT_WAIT_S))))
                self.rate["count"] = None
            req = urllib.request.Request(url, headers=self._headers())
            try:
                kw = {"context": _SSL} if url.startswith("https:") else {}
                with urllib.request.urlopen(req, timeout=TIMEOUT_S, **kw) as resp:
                    self._note_rate(resp.headers)
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                self._note_rate(exc.headers)
                if exc.code == 429:
                    wait = _retry_after(exc.headers, self.rate["period"])
                    if attempt < ATTEMPTS:
                        log.info("Planning Center is rate limiting; waiting %.0f s", wait)
                        _sleep(wait)
                        self.rate["count"] = None
                        continue
                    raise PlanningCenterError(
                        "rate", f"Planning Center is rate limiting this account. Try again in {wait:.0f} seconds.",
                        retry_after=wait) from None
                if exc.code == 401:
                    raise PlanningCenterError(
                        "auth", "Planning Center did not accept that application ID and secret.") from None
                if exc.code == 403:
                    raise PlanningCenterError(
                        "auth", "Planning Center accepted the token, but the person who made it "
                        "does not have access to this in Services.") from None
                detail = self._clean(exc.read().decode("utf-8", "replace")[:200])
                raise PlanningCenterError("http", f"Planning Center answered HTTP {exc.code}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = self._clean(str(getattr(exc, "reason", exc)))
                raise PlanningCenterError("network", f"Could not reach Planning Center: {reason}") from None
            except ValueError:
                raise PlanningCenterError("http", "Planning Center sent something that was not JSON.") from None
        raise PlanningCenterError("rate", "Planning Center is rate limiting this account.")  # pragma: no cover

    # -- calls ---------------------------------------------------------
    def service_types(self) -> list[dict[str, str]]:
        """Every service type on the account as {"id", "name"}, by name. Archived
        and deleted ones are left out: there is nothing live to follow in them."""
        out: list[dict[str, str]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            page = self.get("/services/v2/service_types", {"per_page": PAGE_SIZE, "offset": offset})
            rows = page.get("data") or []
            for row in rows:
                attrs = row.get("attributes") or {}
                name = (attrs.get("name") or "").strip()
                if attrs.get("archived_at") or attrs.get("deleted_at"):
                    continue
                if row.get("id") is not None:
                    out.append({"id": str(row["id"]), "name": name or f"Service type {row['id']}"})
            # A short page is the last one. per_page tops out at 100.
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return sorted(out, key=lambda t: t["name"].lower())


def clean_stored(raw: Any) -> dict:
    """The "planning_center" block of config.json: credentials plus the service
    types Relay follows, validated. Off (all blank) until a token is saved."""
    raw = raw if isinstance(raw, dict) else {}
    ids: list[str] = []
    for v in raw.get("service_types") or []:
        s = str(v).strip()
        if re.fullmatch(r"\d{1,20}", s) and s not in ids:
            ids.append(s)
    return {
        "app_id": str(raw.get("app_id") or "").strip(),
        "secret": str(raw.get("secret") or "").strip(),
        "service_types": ids[:200],
    }


def connected(cfg: dict) -> bool:
    pc = cfg.get("planning_center") or {}
    return bool(pc.get("app_id") and pc.get("secret"))


def client_from(cfg: dict) -> Client | None:
    """A client for the saved token, or None while the integration is off."""
    pc = cfg.get("planning_center") or {}
    return Client(pc["app_id"], pc["secret"]) if connected(cfg) else None
