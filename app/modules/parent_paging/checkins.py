"""Planning Center Check-Ins reads for parent paging (#220): the rooms (Check-Ins locations) and the kids checked in
to one room and not yet checked out, with the security code Check-Ins printed on their label.

Read-only. Only the fields paging needs are asked for (`fields[CheckIn]`): never medical notes or emergency contacts.
Paths, filters and fields were taken from Check-Ins' published OpenAPI description (version 2025-05-28), not checked
against a live account.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .. import using

PAGE_SIZE = 100
MAX_PAGES = 10
KIDS_CACHE_S = 20.0        # a room's list is read from Planning Center at most this often
CHECKED_IN_WITHIN_H = 12   # "today": checked in within this many hours
CHECK_IN_FIELDS = "first_name,last_name,security_code,created_at,checked_out_at"
_CODE = re.compile(r"[^A-Za-z0-9]")


class Unavailable(Exception):
    """Planning Center could not be read. `.message` is a fixed sentence safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _PlanningCenter:
    """Planning Center's client code, which that module provides as "plan.api" (#523) and this one never imports.
    Asked while Planning Center is off, it says so instead of reading anything."""

    def __getattr__(self, name: str) -> Any:
        api = using("plan.api")
        if api is None:
            raise Unavailable("Planning Center is off.")
        return getattr(api, name)


pc = _PlanningCenter()


def _refusal(exc: pc.PlanningCenterError) -> Unavailable:
    if exc.kind == "auth" and exc.status == 403:
        return Unavailable("Planning Center accepted the token, but the person who made it does not have access to "
                           "Check-Ins.")
    if exc.kind == "auth":
        return Unavailable("Planning Center did not accept the saved application ID and secret.")
    if exc.kind == "rate":
        return Unavailable("Planning Center is rate limiting this account. Try again in a minute.")
    if exc.kind == "network":
        return Unavailable("Could not reach Planning Center. Check this machine's internet connection.")
    return Unavailable("Planning Center could not be read right now.")


def clean_code(raw: object) -> str:
    """A security code as shown on screen: letters and digits only, upper case, at most 8."""
    return _CODE.sub("", str(raw or "")).upper()[:8]


def short_name(first: object, last: object) -> str:
    """"Emma S.": the first name and the last name's initial, for the room's own list."""
    first = " ".join(str(first or "").split())[:40]
    last = " ".join(str(last or "").split())
    return f"{first} {last[0].upper()}." if first and last else (first or "(no name)")


def _pages(client: pc.Client, path: str, params: dict[str, Any]):
    offset = 0
    for _ in range(MAX_PAGES):
        doc = client.get(path, {**params, "per_page": PAGE_SIZE, "offset": offset})
        rows = doc.get("data") or []
        yield doc, rows
        if len(rows) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def locations(client: pc.Client) -> list[dict[str, str]]:
    """Every room in every event that is not archived, as {"id", "name", "event"}, by event then name. Folders are
    left out: kids are checked in to the rooms inside them."""
    out: list[dict[str, str]] = []
    try:
        events = [r for _, rows in _pages(client, "/check-ins/v2/events", {"filter": "not_archived"}) for r in rows]
        for ev in events:
            ename = str((ev.get("attributes") or {}).get("name") or "")
            for _, rows in _pages(client, f"/check-ins/v2/events/{ev.get('id')}/locations", {}):
                for r in rows:
                    a = r.get("attributes") or {}
                    if str(a.get("kind") or "Location").lower() == "folder" or not r.get("id"):
                        continue
                    out.append({"id": str(r["id"]), "name": str(a.get("name") or "")[:80], "event": ename[:80]})
    except pc.PlanningCenterError as exc:
        raise _refusal(exc) from None
    return sorted(out, key=lambda x: (x["event"].lower(), x["name"].lower()))


def kids_in(client: pc.Client, location_id: str, now: datetime | None = None) -> list[dict[str, str]]:
    """The kids (not volunteers) checked in to one room in the last CHECKED_IN_WITHIN_H hours and not checked out,
    as {"id", "name", "code"}, by name."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=CHECKED_IN_WITHIN_H)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"filter": "attendee,not_checked_out", "where[location_ids]": location_id,
              "where[created_at][gte]": since, "fields[CheckIn]": CHECK_IN_FIELDS, "order": "first_name"}
    out: list[dict[str, str]] = []
    try:
        for _, rows in _pages(client, "/check-ins/v2/check_ins", params):
            for r in rows:
                a = r.get("attributes") or {}
                code = clean_code(a.get("security_code"))
                if not r.get("id") or not code or a.get("checked_out_at"):
                    continue
                out.append({"id": str(r["id"]), "name": short_name(a.get("first_name"), a.get("last_name")),
                            "code": code})
    except pc.PlanningCenterError as exc:
        raise _refusal(exc) from None
    return sorted(out, key=lambda k: (k["name"].lower(), k["code"]))


class Cache:
    """Each room's list, read at most every KIDS_CACHE_S so a room of phones polling does not use up the account's
    rate limit. A failed read keeps the last good list and says why."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rooms: dict[str, tuple[float, list[dict[str, str]]]] = {}

    def clear(self) -> None:
        with self._lock:
            self._rooms.clear()

    def kids(self, client: pc.Client, location_id: str) -> list[dict[str, str]]:
        with self._lock:
            hit = self._rooms.get(location_id)
        if hit is not None and time.monotonic() - hit[0] < KIDS_CACHE_S:
            return hit[1]
        rows = kids_in(client, location_id)
        with self._lock:
            self._rooms[location_id] = (time.monotonic(), rows)
        return rows

    def known(self, location_id: str) -> list[dict[str, str]]:
        """The last list read for the room, without asking Planning Center."""
        with self._lock:
            hit = self._rooms.get(location_id)
        return hit[1] if hit else []


cache = Cache()
