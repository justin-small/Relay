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
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
PLANS_PER_CHECK = 25  # plans read per service type when looking for today's
SERVICE_TAIL_S = 15 * 60       # keep polling fast this long after the planned end
SERVICE_DEFAULT_S = 90 * 60    # a service time with no planned end is taken to last this long
RUNNING_STALE_S = 4 * 3600     # an item "live" for longer than this was left open, not running

_sleep = time.sleep   # a test swaps this so a 429 does not take real seconds


class PlanningCenterError(Exception):
    """A call that did not work. `kind` is "auth", "rate", "network" or "http";
    the message is safe to show the operator (it never holds the secret)."""

    def __init__(self, kind: str, message: str, retry_after: float | None = None,
                 status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.status = status


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
                raise PlanningCenterError(
                    "http", f"Planning Center answered HTTP {exc.code}: {detail}", status=exc.code) from None
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

    def time_zone(self) -> str:
        """The organisation's time zone as Planning Center names it ("" if absent)."""
        doc = self.get("/services/v2")
        return str(((doc.get("data") or {}).get("attributes") or {}).get("time_zone") or "").strip()

    def plans_from(self, service_type: str, after: datetime) -> list[dict]:
        """The service type's next plans in date order, each with its times,
        starting from the first plan with a time after `after`."""
        base = f"/services/v2/service_types/{service_type}/plans"
        doc = self.get(base, {
            "filter": "after", "after": after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "order": "sort_date", "include": "plan_times", "per_page": PLANS_PER_CHECK,
        })
        inc = _included(doc)
        rows = doc.get("data") or []
        # The docs do not list plan_times among a Plan's relationships. If the
        # include comes back unlinked, ask each plan near the day for its own.
        linked = any("plan_times" in (r.get("relationships") or {}) for r in rows)
        out = []
        for row in rows:
            if linked:
                times = [inc.get(("PlanTime", i)) for i in _rel_ids(row, "plan_times")]
            else:
                sort = parse_time((row.get("attributes") or {}).get("sort_date"))
                near = sort is not None and abs(sort - after.timestamp()) < 3 * 86400
                times = (self.get(f"{base}/{row.get('id')}/plan_times", {"per_page": PAGE_SIZE})
                         .get("data") or []) if near else []
            out.append(_plan(row, [t for t in times if t]))
        return out

    def plan(self, service_type: str, plan_id: str) -> dict:
        """One plan with its times: for the service's `live_starts_at` once it is running."""
        doc = self.get(f"/services/v2/service_types/{service_type}/plans/{plan_id}", {"include": "plan_times"})
        times = [r for r in doc.get("included") or [] if r.get("type") == "PlanTime"]
        return _plan(doc.get("data") or {}, times)

    def live(self, service_type: str, plan_id: str) -> dict:
        """The plan's LIVE state. Nobody controlling, or LIVE never opened for
        the plan, is an ordinary answer (controller None), not an error."""
        try:
            doc = self.get(f"/services/v2/service_types/{service_type}/plans/{plan_id}/live",
                           {"include": "current_item_time,next_item_time,controller"})
        except PlanningCenterError as exc:
            if exc.status == 404:
                return {"id": None, "controller": None, "current": None, "next": None}
            raise
        row = doc.get("data") or {}
        inc = _included(doc)
        person = inc.get(("Person", _rel_id(row, "controller")))
        controller = None
        if person:
            a = person.get("attributes") or {}
            name = a.get("full_name") or a.get("name") or " ".join(
                x for x in (a.get("first_name"), a.get("last_name")) if x)
            controller = {"id": str(person.get("id")), "name": str(name or "Someone").strip()}
        return {
            "id": str(row["id"]) if row.get("id") is not None else None,
            "controller": controller,
            "current": _item_time(inc.get(("ItemTime", _rel_id(row, "current_item_time")))),
            "next": _item_time(inc.get(("ItemTime", _rel_id(row, "next_item_time")))),
        }

    def items(self, service_type: str, plan_id: str) -> list[dict]:
        """The plan's items in order, with key, arrangement, notes and per-service times."""
        out: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            doc = self.get(f"/services/v2/service_types/{service_type}/plans/{plan_id}/items",
                           {"include": "item_times,key,arrangement,item_notes",
                            "per_page": PAGE_SIZE, "offset": offset})
            rows = doc.get("data") or []
            inc = _included(doc)
            by_item: dict[str, dict[str, list]] = {}
            for (kind, _id), r in inc.items():
                if kind in ("ItemTime", "ItemNote"):
                    by_item.setdefault(_rel_id(r, "item") or "", {}).setdefault(kind, []).append(r)
            for row in rows:
                a = row.get("attributes") or {}
                iid = str(row.get("id"))
                arr = (inc.get(("Arrangement", _rel_id(row, "arrangement"))) or {}).get("attributes") or {}
                key = (inc.get(("Key", _rel_id(row, "key"))) or {}).get("attributes") or {}
                mine = by_item.get(iid, {})
                out.append({
                    "id": iid,
                    "title": str(a.get("title") or ""),
                    "type": str(a.get("item_type") or "item"),
                    "length": int(a.get("length") or 0),          # seconds
                    "position": str(a.get("service_position") or "during"),
                    "sequence": int(a.get("sequence") or 0),
                    "key": str(a.get("key_name") or key.get("name") or ""),
                    "arrangement": str(arr.get("name") or ""),
                    "arrangement_sequence": [str(x) for x in
                                             (a.get("custom_arrangement_sequence") or arr.get("sequence") or [])],
                    "notes": [{"category": str((n.get("attributes") or {}).get("category_name") or ""),
                               "content": str((n.get("attributes") or {}).get("content") or "")}
                              for n in mine.get("ItemNote", [])],
                    "times": [_item_time(t) for t in mine.get("ItemTime", [])],
                })
            if len(rows) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return sorted(out, key=lambda i: i["sequence"])


# -- reading JSON:API -------------------------------------------------------
def parse_time(raw: Any) -> float | None:
    """An ISO time from Planning Center as epoch seconds, or None."""
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def _included(doc: dict) -> dict[tuple[str, str | None], dict]:
    return {(str(r.get("type")), str(r.get("id"))): r for r in doc.get("included") or []}


def _rel_id(row: dict, name: str) -> str | None:
    data = ((row.get("relationships") or {}).get(name) or {}).get("data")
    return str(data["id"]) if isinstance(data, dict) and data.get("id") is not None else None


def _rel_ids(row: dict, name: str) -> list[str]:
    data = ((row.get("relationships") or {}).get(name) or {}).get("data")
    return [str(d["id"]) for d in data if d.get("id") is not None] if isinstance(data, list) else []


def _item_time(row: dict | None) -> dict | None:
    """ItemTime spells its fields live_start_at / live_end_at (no "s")."""
    if not row:
        return None
    a = row.get("attributes") or {}
    return {"id": str(row.get("id")), "item": _rel_id(row, "item"), "plan_time": _rel_id(row, "plan_time"),
            "live_start_at": parse_time(a.get("live_start_at")), "live_end_at": parse_time(a.get("live_end_at")),
            "exclude": bool(a.get("exclude"))}


def _plan(row: dict, times: list[dict]) -> dict:
    """PlanTime spells its fields live_starts_at / live_ends_at (with the "s")."""
    a = row.get("attributes") or {}
    out_times = []
    for t in times:
        ta = t.get("attributes") or {}
        out_times.append({
            "id": str(t.get("id")), "type": str(ta.get("time_type") or ""), "name": str(ta.get("name") or ""),
            "starts_at": parse_time(ta.get("starts_at")), "ends_at": parse_time(ta.get("ends_at")),
            "live_starts_at": parse_time(ta.get("live_starts_at")),
            "live_ends_at": parse_time(ta.get("live_ends_at")),
        })
    out_times.sort(key=lambda t: t["starts_at"] or 0)
    return {"id": str(row.get("id")), "title": str(a.get("title") or ""),
            "series_title": str(a.get("series_title") or ""), "dates": str(a.get("dates") or ""),
            "sort_date": parse_time(a.get("sort_date")), "updated_at": str(a.get("updated_at") or ""),
            "times": out_times}


# -- which plan is today's ---------------------------------------------------
# Planning Center may name the zone the Rails way rather than by IANA id.
_RAILS_ZONES = {
    "Eastern Time (US & Canada)": "America/New_York", "Central Time (US & Canada)": "America/Chicago",
    "Mountain Time (US & Canada)": "America/Denver", "Pacific Time (US & Canada)": "America/Los_Angeles",
    "Arizona": "America/Phoenix", "Alaska": "America/Juneau", "Hawaii": "Pacific/Honolulu",
    "Indiana (East)": "America/Indiana/Indianapolis", "Atlantic Time (Canada)": "America/Halifax",
    "Puerto Rico": "America/Puerto_Rico", "London": "Europe/London", "UTC": "UTC",
}


def zone_for(name: str) -> ZoneInfo | None:
    """The zone Planning Center named, or None if Relay cannot place it."""
    for cand in (_RAILS_ZONES.get(name), name):
        if cand:
            try:
                return ZoneInfo(cand)
            except (KeyError, ValueError, OSError):
                pass
    return None


def service_times(plan: dict) -> list[dict]:
    return [t for t in plan.get("times") or [] if t["type"] == "service" and t["starts_at"] is not None]


def _window(t: dict) -> tuple[float, float]:
    start = t["starts_at"]
    end = t["ends_at"] if t["ends_at"] and t["ends_at"] > start else start + SERVICE_DEFAULT_S
    return start, end


def local_day(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def pick_plan(plans: list[dict], now: datetime, tz: ZoneInfo) -> dict | None:
    """Today's plan: one with a `service` time today in the organisation's zone.
    Planning Center has no "today" filter, so the day boundary is worked out
    here. With several today (a morning and an evening plan), the one being run
    wins, then the first not yet over, then the last."""
    today = local_day(now, tz)
    todays = []
    for p in plans:
        mine = [t for t in service_times(p)
                if datetime.fromtimestamp(t["starts_at"], tz).date() == today]
        if mine:
            todays.append((mine[0]["starts_at"], p, mine))
    if not todays:
        return None
    todays.sort(key=lambda x: x[0])
    ts = now.timestamp()
    for _, p, mine in todays:
        if any(t["live_starts_at"] and not t["live_ends_at"]
               and ts - t["live_starts_at"] < RUNNING_STALE_S for t in mine):
            return p
    for _, p, mine in todays:
        if any(_window(t)[1] > ts for t in mine):
            return p
    return todays[-1][1]


def near_service(plan: dict | None, now: float, lead_s: float = 3600.0) -> bool:
    """True from an hour before a service time until a little after its planned end."""
    return any(start - lead_s <= now <= end + SERVICE_TAIL_S
               for start, end in (_window(t) for t in service_times(plan or {})))


def running(live: dict | None, now: float) -> bool:
    """True while LIVE has an item open (started, not ended, not left open for hours)."""
    cur = (live or {}).get("current")
    return bool(cur and cur["live_start_at"] and not cur["live_end_at"]
                and now - cur["live_start_at"] < RUNNING_STALE_S)


def timers(plan: dict | None, items: list[dict], live: dict | None, now: float,
           service_start: float | None = None) -> dict:
    """Planning Center gives no time remaining, so it is worked out here.

    item_left_s: the current item's start + its length - now (negative = over).
    service_over_s: how far behind plan the service is (negative = ahead): time
    since the service's live_starts_at, less the lengths of the items done, less
    what the current item has used of its own length.
    Both are None when nothing is running. Lengths are seconds.
    """
    out = {"current_item": None, "next_item": None, "item_started_at": None, "item_length_s": None,
           "item_left_s": None, "service_started_at": None, "service_over_s": None}
    cur, nxt = (live or {}).get("current"), (live or {}).get("next")
    out["next_item"] = nxt["item"] if nxt else None
    if not cur:
        return out
    out["current_item"] = cur["item"]
    if not running(live, now):
        return out
    by_id = {i["id"]: i for i in items}
    item = by_id.get(cur["item"])
    started = cur["live_start_at"]
    out["item_started_at"] = started
    if item is None:
        return out
    out["item_length_s"] = item["length"]
    out["item_left_s"] = round(started + item["length"] - now, 1)
    pt = next((t for t in (plan or {}).get("times") or [] if t["id"] == cur["plan_time"]), None)
    start = (pt or {}).get("live_starts_at") or service_start
    out["service_started_at"] = start
    if start is None or item["position"] != "during":
        return out
    before = sum(i["length"] for i in items
                 if i["position"] == "during" and i["sequence"] < item["sequence"])
    used = min(now - started, item["length"])
    out["service_over_s"] = round((now - start) - before - used, 1)
    return out


def clean_stored(raw: Any) -> dict:
    """The "planning_center" block of config.json: credentials, the service
    types Relay follows and any plan the operator picked for today, validated.
    Off (all blank) until a token is saved."""
    raw = raw if isinstance(raw, dict) else {}
    ids: list[str] = []
    for v in raw.get("service_types") or []:
        s = str(v).strip()
        if re.fullmatch(r"\d{1,20}", s) and s not in ids:
            ids.append(s)
    # The operator's pick of a different plan, good for the one day it was made.
    overrides: dict[str, dict[str, str]] = {}
    src = raw.get("plan_overrides")
    for st, o in (src.items() if isinstance(src, dict) else ()):
        if (str(st) in ids and isinstance(o, dict) and re.fullmatch(r"\d{1,20}", str(o.get("plan") or ""))
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(o.get("day") or ""))):
            overrides[str(st)] = {"plan": str(o["plan"]), "day": str(o["day"])}
    return {
        "app_id": str(raw.get("app_id") or "").strip(),
        "secret": str(raw.get("secret") or "").strip(),
        "service_types": ids[:200],
        "plan_overrides": overrides,
        # Item notes reach panels only when the operator turns this on.
        "show_notes": raw.get("show_notes") is True,
    }


def connected(cfg: dict) -> bool:
    pc = cfg.get("planning_center") or {}
    return bool(pc.get("app_id") and pc.get("secret"))


def client_from(cfg: dict) -> Client | None:
    """A client for the saved token, or None while the integration is off."""
    pc = cfg.get("planning_center") or {}
    return Client(pc["app_id"], pc["secret"]) if connected(cfg) else None
