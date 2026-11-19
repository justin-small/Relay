"""Scheduled call-time pages (#229): a Planning Center team is paged at a set time with the admin's own words.

A rule is either "N minutes before each service time of today's plan" or "a weekday at a clock time" in Planning
Center's time zone. It pages the people on that team serving today whose accounts matched (#227), as an ordinary
page that buzzes until read (notifications.py). Everything is worked out here on the Relay host, never by a phone.

A rule never fires late: the minute after its time is the only chance, so a Relay that was off or a Planning Center
that was unreachable at the call time skips it. Each rule fires once per occasion (a plan time, or a date).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

from ... import audit, config, db, directory, schedules, timewin
from . import notifications, people, refs

log = logging.getLogger("relay.crew")

GRACE_S = 60
MAX_RULES = 50
TEAM_MAX = 80
MAX_BEFORE_MIN = 24 * 60


def clean(raw) -> dict:
    """A rule from the admin page, checked. Raises Invalid with a sentence the page shows."""
    raw = raw if isinstance(raw, dict) else {}
    try:
        ref, team = refs.clean_pick(raw.get("team_ref"), raw.get("team"), (directory.TEAM,), TEAM_MAX,
                                    "team to page", required=True)
    except people.Invalid as exc:
        raise notifications.Invalid(exc.message) from None
    out = {"team": team, "team_ref": ref, "message": notifications.clean_message(raw.get("message")),
           "enabled": raw.get("enabled") is not False, "minutes": None, "weekday": None, "clock": None,
           "schedule_ref": ""}
    kind = raw.get("kind")
    if kind == "before":
        m = raw.get("minutes")
        if isinstance(m, bool) or not isinstance(m, int) or not 0 <= m <= MAX_BEFORE_MIN:
            raise notifications.Invalid(f"Minutes before the service is a whole number from 0 to {MAX_BEFORE_MIN}.")
        out["minutes"] = m
        named = str(raw.get("schedule_ref") or "").strip()
        if named and schedules.find(config.get()["schedules"], named) is None:
            raise notifications.Invalid("Choose one of the named schedules.")
        out["schedule_ref"] = named
    elif kind == "fixed":
        d, c = raw.get("weekday"), raw.get("clock")
        if isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6:
            raise notifications.Invalid("Choose a day of the week.")
        if not timewin.is_clock(c):
            raise notifications.Invalid("Give the time as HH:MM, for example 08:00.")
        out["weekday"], out["clock"] = d, c
    else:
        raise notifications.Invalid("Choose before the service, or a day and time.")
    out["kind"] = kind
    return out


def list_rules() -> list[dict]:
    people.ready()
    rows = db.query(
        "select c.id, c.team, c.team_ref, c.kind, c.minutes, c.weekday, c.clock, c.message, c.enabled, c.schedule_ref,"
        " (select r.fired_at from crew_calltime_runs r where r.rule_id = c.id order by r.fired_at desc limit 1) as last_at,"
        " (select r.people from crew_calltime_runs r where r.rule_id = c.id order by r.fired_at desc limit 1) as last_people"
        " from crew_calltimes c order by lower(c.team), c.id")
    out = []
    for r in rows:
        f = refs.flags(r["team_ref"], r["team"])
        named = schedules.find(config.get()["schedules"], r["schedule_ref"]) if r["schedule_ref"] else None
        out.append({**r, "last_ts": db.epoch(r["last_at"]), "enabled": bool(r["enabled"]), "team": f["name"], "missing": f["missing"],
                    "schedule_name": named["name"] if named else ("(deleted)" if r["schedule_ref"] else ""),
                    "unmatched": f["unmatched"]})
    return out


def save(rule: dict, rule_id: int | None = None) -> int:
    people.ready()
    cols = (rule["team"], rule.get("team_ref", ""), rule["kind"], rule["minutes"], rule["weekday"], rule["clock"], rule["message"],
            int(rule["enabled"]), rule.get("schedule_ref", ""))
    if rule_id is None:
        if db.one("select count(*) as n from crew_calltimes")["n"] >= MAX_RULES:
            raise notifications.Invalid(f"At most {MAX_RULES} call times.")
        return db.one("insert into crew_calltimes (team, team_ref, kind, minutes, weekday, clock, message, enabled,"
                      " schedule_ref, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) returning id", cols + (db.now(),))["id"]
    if not db.execute("update crew_calltimes set team = ?, team_ref = ?, kind = ?, minutes = ?, weekday = ?, clock = ?,"
                      " message = ?, enabled = ?, schedule_ref = ? where id = ?", cols + (rule_id,)):
        raise notifications.Invalid("No such call time.", 404)
    return rule_id


def delete(rule_id: int) -> None:
    people.ready()
    if not db.execute("delete from crew_calltimes where id = ?", (rule_id,)):
        raise notifications.Invalid("No such call time.", 404)


# -- when a rule is due ----------------------------------------------------------------------------------------------
def occasions(rule: dict, tracks, now: float, zone) -> list[tuple[str, float, str | None]]:
    """(occasion key, epoch to fire at, plan time id or None) for each moment this rule is aimed at today."""
    team_today = notifications.serving_for(tracks, rule.get("team_ref", ""), rule["team"])
    if not team_today:
        return []              # nobody on that team is serving today
    if rule["kind"] == "before" and rule.get("schedule_ref"):
        return _schedule_occasions(rule, now)
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


def _schedule_occasions(rule: dict, now: float) -> list[tuple[str, float, str | None]]:
    """Each window of the rule's named schedule that starts today (Relay zone), `minutes` before it. A schedule that
    is gone or off has none, so its call times go quiet rather than fire at the wrong time."""
    from ... import modules

    sched = schedules.find(config.get()["schedules"], rule["schedule_ref"])
    if sched is None or not sched["enabled"]:
        return []
    tz = ZoneInfo(config.timezone())
    day = datetime.fromtimestamp(now, tz).date()
    frm = schedules.localize(day, dtime(0, 0), tz)
    to = schedules.localize(day + timedelta(days=1), dtime(0, 0), tz)
    wins = schedules.windows([sched], frm, to, config.timezone(), modules.using("schedules.plan_times"))
    return [(f"{rule['schedule_ref']}:{w.start.isoformat()}", w.start.timestamp() - rule["minutes"] * 60, None)
            for w in wins if frm <= w.start < to]


def schedule_users(ref: str) -> list[dict]:
    """The call-time rules aimed at named schedule `ref`, for the Schedules page's "used by"."""
    return [{"where": "Crew", "what": f"Call time for {r['team']}, {r['minutes']} min before"}
            for r in list_rules() if r["schedule_ref"] == ref]


def next_for(user_id: int, tracks, zone, now: float | None = None) -> dict | None:
    """The next call time (booth-off screen, #234) for this person: the soonest enabled rule for a team they serve on
    today whose moment has not passed. {"ts", "team"}, or None. Works from the same rules the pager fires."""
    from . import checklists

    ts = time.time() if now is None else now
    if not tracks or zone is None:
        return None
    mine = checklists.held(user_id, tracks)
    pid = directory.external_id(user_id, directory.PCO)
    best = None
    for rule in list_rules():
        if not rule["enabled"] or not any(notifications.holds(m, rule.get("team_ref", ""), rule["team"]) for m in mine):
            continue
        for _occasion, at, time_id in occasions(rule, tracks, ts, zone):
            serves = any(r["person"] == pid for r in notifications.serving_for(tracks, rule.get("team_ref", ""), rule["team"], time_id))
            if serves and at >= ts and (best is None or at < best["ts"]):
                best = {"ts": at, "team": rule["team"]}
    return best


def _state():
    """Today's followed plans and Planning Center's zone, when that module is on."""
    from ... import modules

    follower = modules.using("plan.follower")
    if follower is None:
        return [], None
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
            if not db.execute("insert into crew_calltime_runs (rule_id, occasion, fired_at) values (?, ?, ?)"
                              " on conflict do nothing", (rule["id"], occasion, db.iso(ts))):
                continue          # already fired for this occasion
            ids, _ = notifications.resolve([], [{"ref": rule.get("team_ref", ""), "name": rule["team"]}], tracks, time_id=time_id)
            page = None
            if ids:
                try:
                    page = notifications.create(rule["message"], None, ids, now=ts)
                    sent += 1
                except notifications.Invalid:
                    page = None
            db.execute("update crew_calltime_runs set notification_id = ?, people = ? where rule_id = ? and occasion = ?",
                       (page, len(ids) if page else 0, rule["id"], occasion))
            audit.record("crew.calltime", "Sent a scheduled call-time notification", target=rule["id"],
                         detail={"team": rule["team"], "people": len(ids) if page else 0})
    return sent
