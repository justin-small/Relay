"""Scheduled call-time pages (#229): a Planning Center team is paged at a set time with the admin's own words.

A rule is either "N minutes before each service time of today's plan" or "a weekday at a clock time" in Planning
Center's time zone. It pages the people on that team serving today whose accounts matched (#227), as an ordinary
page that buzzes until read (paging.py). Everything is worked out here on the Relay host, never by a phone.

A rule never fires late: the minute after its time is the only chance, so a Relay that was off or a Planning Center
that was unreachable at the call time skips it. Each rule fires once per occasion (a plan time, or a date).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime

from ... import audit, db, timewin
from . import paging, people

log = logging.getLogger("relay.crew")

GRACE_S = 60
MAX_RULES = 50
TEAM_MAX = 80
MAX_BEFORE_MIN = 24 * 60


def clean(raw) -> dict:
    """A rule from the admin page, checked. Raises Invalid with a sentence the page shows."""
    raw = raw if isinstance(raw, dict) else {}
    team = raw.get("team")
    team = " ".join(team.split()) if isinstance(team, str) else ""
    if not team or len(team) > TEAM_MAX:
        raise paging.Invalid("Name the Planning Center team to page.")
    out = {"team": team, "message": paging.clean_message(raw.get("message")),
           "enabled": raw.get("enabled") is not False, "minutes": None, "weekday": None, "clock": None}
    kind = raw.get("kind")
    if kind == "before":
        m = raw.get("minutes")
        if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= MAX_BEFORE_MIN:
            raise paging.Invalid(f"Minutes before the service is a whole number from 0 to {MAX_BEFORE_MIN}.")
        out["minutes"] = m
    elif kind == "fixed":
        d, c = raw.get("weekday"), raw.get("clock")
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6:
            raise paging.Invalid("Choose a day of the week.")
        if not timewin.is_clock(c):
            raise paging.Invalid("Give the time as HH:MM, for example 08:00.")
        out["weekday"], out["clock"] = d, c
    else:
        raise paging.Invalid("Choose before the service, or a day and time.")
    out["kind"] = kind
    return out


def list_rules() -> list[dict]:
    people.ready()
    rows = db.query(
        "select c.id, c.team, c.kind, c.minutes, c.weekday, c.clock, c.message, c.enabled,"
        " (select r.fired_ts from crew_calltime_runs r where r.rule_id = c.id order by r.fired_ts desc limit 1) as last_ts,"
        " (select r.people from crew_calltime_runs r where r.rule_id = c.id order by r.fired_ts desc limit 1) as last_people"
        " from crew_calltimes c order by lower(c.team), c.id")
    return [{**r, "enabled": bool(r["enabled"])} for r in rows]


def save(rule: dict, rule_id: int | None = None) -> int:
    people.ready()
    cols = (rule["team"], rule["kind"], rule["minutes"], rule["weekday"], rule["clock"], rule["message"],
            int(rule["enabled"]))
    if rule_id is None:
        if db.one("select count(*) as n from crew_calltimes")["n"] >= MAX_RULES:
            raise paging.Invalid(f"At most {MAX_RULES} call times.")
        return db.one("insert into crew_calltimes (team, kind, minutes, weekday, clock, message, enabled, created_at)"
                      " values (?, ?, ?, ?, ?, ?, ?, ?) returning id", cols + (db.now(),))["id"]
    if not db.execute("update crew_calltimes set team = ?, kind = ?, minutes = ?, weekday = ?, clock = ?,"
                      " message = ?, enabled = ? where id = ?", cols + (rule_id,)):
        raise paging.Invalid("No such call time.", 404)
    return rule_id


def delete(rule_id: int) -> None:
    people.ready()
    if not db.execute("delete from crew_calltimes where id = ?", (rule_id,)):
        raise paging.Invalid("No such call time.", 404)


# -- when a rule is due ----------------------------------------------------------------------------------------------
def occasions(rule: dict, tracks, now: float, zone) -> list[tuple[str, float, str | None]]:
    """(occasion key, epoch to fire at, plan time id or None) for each moment this rule is aimed at today."""
    team_today = paging.serving(tracks).get(rule["team"])
    if not team_today:
        return []              # nobody on that team is serving today
    if rule["kind"] == "before":
        out = []
        for tr in tracks:
            plan = tr.plan or {}
            for t in plan.get("times") or []:
                if t.get("type") == "service" and t.get("starts_at") is not None:
                    out.append((f"{plan.get('id')}:{t['id']}", t["starts_at"] - rule["minutes"] * 60, t["id"]))
        return out
    local = datetime.fromtimestamp(now, zone)
    if timewin.weekday(local.date()) != rule["weekday"]:   # Sunday = 0
        return []
    mins = timewin.minutes(rule["clock"])
    at = datetime.combine(local.date(), dtime(mins // 60, mins % 60), tzinfo=zone)
    return [(local.date().isoformat(), at.timestamp(), None)]


def next_for(user_id: int, tracks, zone, now: float | None = None) -> dict | None:
    """The next call time (booth-off screen, #234) for this person: the soonest enabled rule for a team they serve on
    today whose moment has not passed. {"ts", "team"}, or None. Works from the same rules the pager fires."""
    from . import checklists

    ts = time.time() if now is None else now
    if not tracks or zone is None:
        return None
    teams = checklists.teams_of(user_id, tracks)
    pid = checklists._pc_person(user_id)
    best = None
    for rule in list_rules():
        if not rule["enabled"] or rule["team"] not in teams:
            continue
        for _occasion, at, time_id in occasions(rule, tracks, ts, zone):
            serves = any(r["person"] == pid for r in paging.serving(tracks, time_id).get(rule["team"], []))
            if serves and at >= ts and (best is None or at < best["ts"]):
                best = {"ts": at, "team": rule["team"]}
    return best


def _state():
    """Today's followed plans and Planning Center's zone, when that module is on."""
    from .. import modules as registry

    if not registry.enabled("planning_center"):
        return [], None
    from ..planning_center.pclive import follower

    return list(follower.tracks.values()), follower.zone or follower.fallback_zone()


def run(now: float | None = None, tracks=None, zone=None) -> int:
    """Page for every rule whose moment is this minute. Returns how many pages were sent. Blocking."""
    people.ready()
    if tracks is None:
        tracks, zone = _state()
    if not tracks or zone is None:
        return 0
    ts = time.time() if now is None else now
    sent = 0
    for rule in list_rules():
        if not rule["enabled"]:
            continue
        for occasion, at, time_id in occasions(rule, tracks, ts, zone):
            if not at <= ts < at + GRACE_S:
                continue          # early, or the minute has passed: never late
            if not db.execute("insert into crew_calltime_runs (rule_id, occasion, fired_ts) values (?, ?, ?)"
                              " on conflict do nothing", (rule["id"], occasion, ts)):
                continue          # already fired for this occasion
            ids, _ = paging.resolve([], [rule["team"]], tracks, time_id=time_id)
            page = None
            if ids:
                try:
                    page = paging.create(rule["message"], None, ids, now=ts)
                    sent += 1
                except paging.Invalid:
                    page = None
            db.execute("update crew_calltime_runs set page_id = ?, people = ? where rule_id = ? and occasion = ?",
                       (page, len(ids) if page else 0, rule["id"], occasion))
            audit.record("crew.calltime", "Sent a scheduled call-time page", target=rule["id"],
                         detail={"team": rule["team"], "people": len(ids) if page else 0})
    return sent
