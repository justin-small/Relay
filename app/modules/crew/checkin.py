"""Crew check-in and the arrival board (#231).

A person is marked arrived when their phone reaches Relay from the church network (its request comes from a private
LAN address, which only happens on Relay's own HTTPS port), or when they tap Check in inside the church's geofence.
The distance is worked out here on the host from the position the phone sends; no position or address is stored.

The board shows the people on today's Planning Center plan who have a crew account against their team's earliest
call time (#229). Arrivals belong to the plan they were for, so they clear when a new plan takes over.
"""
from __future__ import annotations

import ipaddress
import math
import time
from datetime import datetime

from ... import db, directory
from . import calltimes, paging, people

RADIUS_DEFAULT_M = 150
RADIUS_MIN_M, RADIUS_MAX_M = 20, 5000
MAX_ACCURACY_M = 150          # a phone that cannot place itself this closely does not count


class Refused(people.Invalid):
    pass


def clean_geofence(raw) -> dict | None:
    """The saved church position, or None. Raises ValueError for a half-filled or impossible one (the admin page
    shows the sentence)."""
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("Give the church's latitude, longitude and radius.")
    lat, lon, r = raw.get("lat"), raw.get("lon"), raw.get("radius_m", RADIUS_DEFAULT_M)
    if lat is None and lon is None:
        return None
    for v in (lat, lon, r):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError("Latitude, longitude and radius are numbers.")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("That latitude or longitude is not on the map.")
    if not RADIUS_MIN_M <= r <= RADIUS_MAX_M:
        raise ValueError(f"The radius is {RADIUS_MIN_M} to {RADIUS_MAX_M} metres.")
    return {"lat": round(float(lat), 6), "lon": round(float(lon), 6), "radius_m": int(r)}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(a))


def is_lan(host) -> bool:
    """True for a private LAN address. Loopback is not: it is a proxy or this machine, not a phone on the Wi-Fi."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_unspecified


def inside(fence: dict | None, lat, lon, accuracy) -> bool:
    if not fence:
        return False
    for v in (lat, lon, accuracy):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return False
    if not -90 <= lat <= 90 or not -180 <= lon <= 180 or not 0 <= accuracy <= MAX_ACCURACY_M:
        return False
    return distance_m(fence["lat"], fence["lon"], lat, lon) <= fence["radius_m"]


# -- which plan an arrival is for --------------------------------------------------------------------------------
def plan_key(tracks, zone, now: float) -> str:
    ids = sorted(str(tr.plan["id"]) for tr in tracks if tr.plan)
    if ids:
        return "|".join(ids)
    local = datetime.fromtimestamp(now, zone) if zone else datetime.fromtimestamp(now)
    return "day:" + local.date().isoformat()


def _forget_old(key: str) -> None:
    db.execute("delete from crew_arrivals where plan_id != ?", (key,))


def arrive(user_id: int, key: str, method: str, now: float | None = None) -> float:
    """Record the first arrival for this plan; later ones change nothing. Returns when they arrived."""
    people.ready()
    ts = time.time() if now is None else now
    _forget_old(key)
    db.execute("insert into crew_arrivals (plan_id, user_id, arrived_ts, method) values (?, ?, ?, ?)"
               " on conflict do nothing", (key, user_id, ts, method))
    return db.one("select arrived_ts from crew_arrivals where plan_id = ? and user_id = ?", (key, user_id))["arrived_ts"]


def mine(user_id: int, key: str) -> dict | None:
    people.ready()
    return db.one("select arrived_ts as ts, method from crew_arrivals where plan_id = ? and user_id = ?",
                  (key, user_id))


def clear(key: str) -> int:
    people.ready()
    return db.execute("delete from crew_arrivals where plan_id = ?", (key,))


# -- the board ---------------------------------------------------------------------------------------------------
def _call_time(held: list[dict], rules: list[dict], tracks, now: float, zone) -> float | None:
    """The earliest call time today across the rules for this person's teams (None when no rule applies)."""
    at = []
    for rule in rules:
        if rule["enabled"] and any(paging.holds(m, rule.get("team_ref", ""), rule["team"]) for m in held):
            at += [t for _, t, _ in calltimes.occasions(rule, tracks, now, zone)]
    return min(at) if at else None


def board(tracks, zone, now: float | None = None) -> dict:
    """Everyone on today's plan, against their call time: arrived on time, arrived late, overdue, or waiting."""
    people.ready()
    ts = time.time() if now is None else now
    key = plan_key(tracks, zone, ts)
    _forget_old(key)
    arrivals = {r["user_id"]: r for r in db.query(
        "select user_id, arrived_ts, method from crew_arrivals where plan_id = ?", (key,))}
    linked = directory.links(directory.PCO)
    names = {r["id"]: r["name"] for r in db.query("select id, name from users")}
    rules = calltimes.list_rules()
    by_person: dict[str, dict] = {}
    for m in paging.active_members(tracks):
        p = by_person.setdefault(m["person"], {"name": m["name"], "teams": [], "held": []})
        if m["team"] not in p["teams"]:
            p["teams"].append(m["team"])
        p["held"].append(m)
    out = []
    for pid, p in by_person.items():
        uid = linked.get(pid)
        call = _call_time(p["held"], rules, tracks, ts, zone) if zone else None
        arr = arrivals.get(uid) if uid else None
        if uid is None:
            state = "no_account"
        elif arr:
            state = "late_arrival" if call is not None and arr["arrived_ts"] > call else "arrived"
        else:
            state = "overdue" if call is not None and ts > call else "waiting"
        out.append({"user_id": uid, "name": names.get(uid) or p["name"], "teams": sorted(p["teams"]),
                    "call_ts": call, "arrived_ts": arr["arrived_ts"] if arr else None,
                    "method": arr["method"] if arr else "", "state": state})
    out.sort(key=lambda r: (r["call_ts"] is None, r["call_ts"] or 0, r["name"].lower()))
    return {"plan": key, "people": out, "arrived": sum(1 for r in out if r["arrived_ts"]),
            "total": sum(1 for r in out if r["state"] != "no_account")}
