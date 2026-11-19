"""Scheduled YouTube live streams and their links in Planning Center Publishing (#411).

Relay creates the church's upcoming broadcasts ahead of time, from weekly rules set here and from the plans of
Planning Center service types the operator picks, and binds each to a stream key so the encoder's stream lands on it.
Then it writes each broadcast's watch link into the Church Center episodes the operator linked: by the episode's day
and time, the next broadcast, or one the operator picked. A link goes out as soon as the broadcast is scheduled, and
again whenever the operator presses Send.

YouTube quota (one daily allowance every Relay shares): a check is one `liveBroadcasts.list` (1 unit); creating a
broadcast is `insert` (50) plus `bind` (50), once per service. Planning Center is asked only when a link changes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import time
import uuid
from typing import Any
from zoneinfo import ZoneInfo

from ... import config, netsock, oauth, schedules, timewin
from .service import ACCOUNT, API, KEY, PROVIDER, SCHEDULE, _quota_reset, _reasons, _when, can_write, service

log = logging.getLogger("relay.youtube")

EVERY_S = 1800                  # between checks of upcoming broadcasts while something is set up
DAYS = (1, 14, 7)               # (least, most, default) days ahead to create broadcasts
PRIVACY = ("public", "unlisted", "private")
MATCHES = ("date", "next", "manual")
TITLE = SCHEDULE["title"]
SAME_SLOT_S = 2 * 3600          # a broadcast this close to an episode's time of day is that episode's
ALREADY_S = 45 * 60             # a broadcast already scheduled this close to a service is the service's
LEAD_S = 15 * 60                # never create one that starts sooner than this
STALE_S = 86400                 # an "upcoming" broadcast this far in the past was abandoned; ignore it
MAX_RULES, MAX_LINKS, MAX_TYPES = 20, 10, 10

NO_PC = "Planning Center is off or not connected, so Relay cannot use its plans or Publishing."
# What the operator reads when YouTube says no: looked up by key, never an exception's text (CodeQL).
WORDS = {
    "unreachable": "Relay could not reach YouTube or got no answer. It will try again.",
    "sign_in": "YouTube needs signing in again. Press Connect.",
    "refused": "YouTube refused Relay's sign-in. Relay will try again.",
    "quota": "YouTube's daily allowance for Relay is used up. Scheduling comes back after midnight Pacific time.",
    "permission": "YouTube did not give Relay permission to schedule. Press Connect again and allow it.",
    "not_enabled": "This channel cannot go live yet (live streaming is not switched on in YouTube Studio).",
    "no_id": "YouTube did not say which broadcast it created.",
    "other": "YouTube did not answer as expected. Relay will try again.",
}


# ---------------------------------------------------------------- settings
def _text(v: Any, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def _pid(v: Any) -> str:
    return v if isinstance(v, str) and re.fullmatch(r"\d{1,20}", v) else ""


def _vid(v: Any) -> str:
    return v if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", v) else ""


def _rid(v: Any) -> str:
    return v if isinstance(v, str) and re.fullmatch(r"[a-z0-9]{1,16}", v) else uuid.uuid4().hex[:8]


# Rule days count from Sunday = 0 (#522, app/timewin.py). Settings saved before that counted from Monday = 0 and have
# no DAYS_FROM key, so their days are renumbered as they are read; the next save stores them renumbered with the key.
DAYS_FROM = "sunday"


def clean_schedule(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    monday_first = raw.get("days_from") != DAYS_FROM
    try:
        days = max(DAYS[0], min(DAYS[1], int(raw.get("days_ahead"))))
    except (TypeError, ValueError):
        days = DAYS[2]
    rules = []
    for r in (raw.get("rules") if isinstance(raw.get("rules"), list) else [])[:MAX_RULES]:
        if not isinstance(r, dict):
            continue
        clock = timewin.clean_clock(r.get("time"), loose=True)
        try:
            day = int(r.get("day"))
        except (TypeError, ValueError):
            continue
        if not clock or not 0 <= day <= 6:
            continue
        rules.append({"id": _rid(r.get("id")), "name": _text(r.get("name"), 80) or "Service",
                      "day": timewin.from_monday_first(day) if monday_first else day, "time": clock})
    stream = raw.get("stream_id")
    return {
        "on": raw.get("on") is True,
        "privacy": raw.get("privacy") if raw.get("privacy") in PRIVACY else "public",
        "stream_id": stream.strip()[:80] if isinstance(stream, str) and re.fullmatch(r"[\w-]{0,80}", stream.strip())
        else "",
        "stream_name": _text(raw.get("stream_name"), 120),
        "title": _text(raw.get("title"), 100) or TITLE,
        "description": _text(raw.get("description"), 1000),
        "days_ahead": days,
        "plan_types": [p for p in (raw.get("plan_types") if isinstance(raw.get("plan_types"), list) else [])
                       if _pid(p)][:MAX_TYPES],
        "rules": rules,
        "days_from": DAYS_FROM,
    }


def clean_links(raw: Any) -> list[dict]:
    out = []
    for r in (raw if isinstance(raw, list) else [])[:MAX_LINKS]:
        if not isinstance(r, dict) or not _pid(r.get("channel_id")) or not _pid(r.get("episode_id")):
            continue
        slot = r.get("slot")
        out.append({
            "id": _rid(r.get("id")),
            "channel_id": r["channel_id"], "channel": _text(r.get("channel"), 120),
            "episode_id": r["episode_id"], "episode": _text(r.get("episode"), 200),
            "slot": float(slot) if isinstance(slot, (int, float)) and not isinstance(slot, bool) and slot > 0 else 0.0,
            "match": r.get("match") if r.get("match") in MATCHES else "date",
            "video": _vid(r.get("video")),
            "auto": r.get("auto") is not False,
            "pushed": _vid(r.get("pushed")),
            "pushed_at": float(r["pushed_at"]) if isinstance(r.get("pushed_at"), (int, float))
            and not isinstance(r.get("pushed_at"), bool) else 0.0,
        })
    return out


def clean_created(raw: Any) -> dict:
    """What Relay created or found for each service: {"rule:<id>:<date>" or "plan:<id>": {"id", "at"}}."""
    out = {}
    for k, v in (raw.items() if isinstance(raw, dict) else []):
        if isinstance(k, str) and re.fullmatch(r"(rule:[a-z0-9]{1,16}:\d{4}-\d\d-\d\d|plan:\d{1,20})", k) \
                and isinstance(v, dict) and _vid(v.get("id")) and isinstance(v.get("at"), (int, float)):
            out[k] = {"id": v["id"], "at": float(v["at"]), "made": v.get("made") is True}
    return out


def stored() -> dict:
    s = config.get().get(KEY) or {}
    return {"schedule": clean_schedule(s.get("schedule")), "links": clean_links(s.get("links")),
            "created": clean_created(s.get("created"))}


def zone() -> ZoneInfo:
    return ZoneInfo(schedules.default_timezone(os.environ.get("TZ")))


def watch(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _pc_on() -> bool:
    from .. import modules as registry
    from ..planning_center import planningcenter as pcm

    return registry.running("planning_center") and pcm.connected(config.get())


def pc_client():
    """Planning Center's client while that module is on and connected, else None."""
    from ..planning_center import planningcenter as pcm

    return pcm.client_from(config.get()) if _pc_on() else None


# ---------------------------------------------------------------- when services are
def rule_times(rules: list[dict], now: float, days: int, tz: ZoneInfo) -> list[dict]:
    """Each rule's services from now to `days` ahead, as {"key", "name", "start"}."""
    out = []
    today = dt.datetime.fromtimestamp(now, tz).date()
    for r in rules:
        mins = timewin.minutes(r["time"])
        h, m = mins // 60, mins % 60
        for i in range(days + 1):
            day = today + dt.timedelta(days=i)
            if timewin.weekday(day) != r["day"]:
                continue
            start = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz).timestamp()
            if now < start <= now + days * 86400:
                out.append({"key": f"rule:{r['id']}:{day.isoformat()}", "name": r["name"], "start": start, "title": ""})
    return out


def plan_start(plan: dict) -> float | None:
    times = [t for t in plan.get("times") or [] if t.get("starts_at")]
    service_times = [t for t in times if t.get("type") == "service"] or times
    return service_times[0]["starts_at"] if service_times else plan.get("sort_date")


def title_for(template: str, svc: dict, tz: ZoneInfo) -> str:
    when = dt.datetime.fromtimestamp(svc["start"], tz)
    words = {"name": svc["name"], "title": svc.get("title") or svc["name"],
             "date": f"{when:%B} {when.day}, {when.year}", "time": when.strftime("%I:%M %p").lstrip("0")}
    out = re.sub(r"\{(name|title|date|time)\}", lambda m: words[m.group(1)], template).strip()
    return (out or svc["name"])[:100]      # YouTube refuses a title over 100 characters


def same_slot(start: float, slot: float, tz: ZoneInfo, now: float) -> bool:
    """Whether a broadcast at `start` belongs to an episode time at `slot`. A time still ahead must be that very
    day; a past one (Church Center's recurring episodes keep last week's time) matches the same weekday and time."""
    if slot > now - 3600:
        return abs(start - slot) <= SAME_SLOT_S
    a, b = dt.datetime.fromtimestamp(start, tz), dt.datetime.fromtimestamp(slot, tz)
    if timewin.weekday(a) != timewin.weekday(b):
        return False
    mins = abs((a.hour * 60 + a.minute) - (b.hour * 60 + b.minute))
    return min(mins, 1440 - mins) * 60 <= SAME_SLOT_S


def pick(link: dict, candidates: list[dict], tz: ZoneInfo, now: float) -> dict | None:
    """The broadcast a link points at, from live and upcoming ones (soonest first)."""
    if link["match"] == "manual":
        return next((b for b in candidates if b["id"] == link["video"]), None) or \
            ({"id": link["video"], "title": "", "start": None} if link["video"] else None)
    if link["match"] == "next":
        return candidates[0] if candidates else None
    if not link["slot"]:
        return None
    return next((b for b in candidates if b["start"] and same_slot(b["start"], link["slot"], tz, now)), None)


# ---------------------------------------------------------------- the planner
class Problem(Exception):
    """YouTube said no; `key` names the sentence in WORDS."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key if key in WORDS else "other"

    @property
    def words(self) -> str:
        return WORDS[self.key]


def words(exc: Exception) -> str:
    """The operator's sentence for a failed YouTube call (oauth.Problem: the sign-in, not YouTube's answer)."""
    return exc.words if isinstance(exc, Problem) else WORDS["sign_in" if isinstance(exc, oauth.NeedsSignIn) else "unreachable"]


class Planner:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.wake: asyncio.Event | None = None
        self.lock: asyncio.Lock | None = None
        self.upcoming: list[dict] = []
        self.streams: list[dict] | None = None
        self.checked_at = 0.0
        self.problem = ""
        self.link_errors: dict[str, str] = {}
        self.made: list[str] = []           # what the last run created, for the page

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.wake, self.lock = asyncio.Event(), asyncio.Lock()
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.upcoming, self.streams, self.problem, self.link_errors = [], None, "", {}

    def poke(self) -> None:
        if self.wake:
            self.wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("youtube: schedule check failed")
                self.problem = "Relay could not check the YouTube schedule. It will try again."
            assert self.wake is not None
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=EVERY_S)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------ YouTube calls
    async def api(self, method: str, path: str, body: Any = None) -> dict:
        """One YouTube Data API call; the answer's JSON, or Problem with a fixed sentence."""
        token = await ACCOUNT.token()
        headers = {"User-Agent": "Relay", "Authorization": f"Bearer {token}"}
        try:
            status, raw = await asyncio.to_thread(netsock.fetch, PROVIDER.traffic, f"{API}/{path}",
                                                  json_body=body, method=method, headers=headers)
        except OSError:
            raise Problem("unreachable") from None
        try:
            d = json.loads(raw or b"{}")
        except ValueError:
            d = {}
        d = d if isinstance(d, dict) else {}
        if status in (200, 204):
            return d
        reasons = _reasons(d)
        if status == 401:
            ACCOUNT.refused()
            raise Problem("refused")
        if status == 403 and reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            service.quota_until = _quota_reset(time.time())
            raise Problem("quota")
        if status == 403 and reasons & {"insufficientPermissions", "forbidden"}:
            raise Problem("permission")
        if status == 403 and "liveStreamingNotEnabled" in reasons:
            raise Problem("not_enabled")
        log.warning("youtube: %s %s answered HTTP %s %s", method, path.split("?")[0], status, sorted(reasons)[:3])
        raise Problem("other")

    async def list_upcoming(self) -> list[dict]:
        d = await self.api("GET", "liveBroadcasts?part=id,snippet,status,contentDetails&broadcastStatus=upcoming"
                                  "&broadcastType=all&maxResults=50")
        now, out = time.time(), []
        for it in d.get("items") or []:
            if not isinstance(it, dict) or not _vid(it.get("id")):
                continue
            sn, st, cd = it.get("snippet") or {}, it.get("status") or {}, it.get("contentDetails") or {}
            start = _when(sn.get("scheduledStartTime"))
            if start is not None and start < now - STALE_S:
                continue
            out.append({"id": it["id"], "title": str(sn.get("title") or "")[:200], "start": start,
                        "privacy": str(st.get("privacyStatus") or "")[:20], "watch": watch(it["id"]),
                        "bound": bool(cd.get("boundStreamId")), "live": False})
        out.sort(key=lambda b: b["start"] or float("inf"))
        return out

    async def list_streams(self) -> list[dict]:
        # Never the `cdn` part: it holds the stream key itself, which Relay has no use for.
        d = await self.api("GET", "liveStreams?part=id,snippet,status&mine=true&maxResults=50")
        out = []
        for it in d.get("items") or []:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                continue
            sn = it.get("snippet") or {}
            out.append({"id": it["id"][:80], "title": str(sn.get("title") or "")[:120],
                        "status": str((it.get("status") or {}).get("streamStatus") or "")[:20]})
        self.streams = out
        return out

    async def create(self, svc: dict, sched: dict, tz: ZoneInfo) -> dict:
        start = dt.datetime.fromtimestamp(svc["start"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bound = bool(sched["stream_id"])
        body = {"snippet": {"title": title_for(sched["title"], svc, tz), "scheduledStartTime": start,
                            "description": sched["description"]},
                "status": {"privacyStatus": sched["privacy"], "selfDeclaredMadeForKids": False},
                # With a stream key bound, the broadcast goes live when the encoder starts and ends when it stops.
                "contentDetails": {"enableAutoStart": bound, "enableAutoStop": bound}}
        d = await self.api("POST", "liveBroadcasts?part=id,snippet,status,contentDetails", body)
        vid = _vid(d.get("id"))
        if not vid:
            raise Problem("no_id")
        if bound:
            try:
                await self.api("POST", f"liveBroadcasts/bind?id={vid}&streamId={sched['stream_id']}&part=id")
            except Problem:
                log.warning("youtube: created %s but could not bind it to the stream key", vid)
        return {"id": vid, "title": body["snippet"]["title"], "start": svc["start"], "privacy": sched["privacy"],
                "watch": watch(vid), "bound": bound, "live": False}

    # ------------------------------------------------------------ one run
    def services(self, sched: dict, now: float, tz: ZoneInfo) -> list[dict]:
        out = rule_times(sched["rules"], now, sched["days_ahead"], tz)
        client = pc_client() if sched["plan_types"] else None
        if client is not None:
            names = {t["id"]: t["name"] for t in client.service_types()}
            after = dt.datetime.fromtimestamp(now, dt.timezone.utc)
            for st in sched["plan_types"]:
                for plan in client.plans_from(st, after):
                    start = plan_start(plan)
                    if start and now < start <= now + sched["days_ahead"] * 86400:
                        out.append({"key": f"plan:{plan['id']}", "name": names.get(st, "Service"),
                                    "title": plan["title"] or plan["series_title"], "start": start})
        return sorted(out, key=lambda s: s["start"])

    async def run(self, push: bool = True, asked: bool = False) -> None:
        """One check. In the background only while a schedule or a link is set up (quota); `asked` (Check now)
        lists the upcoming broadcasts regardless."""
        assert self.lock is not None
        async with self.lock:
            await self._run_once(push, asked)

    async def _run_once(self, push: bool, asked: bool = False) -> None:
        now = time.time()
        st = stored()
        if not ACCOUNT.connected() or (not asked and not st["schedule"]["on"] and not st["links"]):
            self.upcoming, self.problem = [], ""
            return
        if service.quota_until > now:
            return
        try:
            self.upcoming = await self.list_upcoming()
            self.checked_at, self.problem = now, ""
            sched, tz = st["schedule"], zone()
            created = {k: v for k, v in st["created"].items() if v["at"] > now - 2 * 86400}
            self.made = []
            if sched["on"] and can_write():
                try:
                    wanted = await asyncio.to_thread(self.services, sched, now, tz)
                except Exception:
                    log.warning("youtube: reading Planning Center plans failed", exc_info=True)
                    wanted = rule_times(sched["rules"], now, sched["days_ahead"], tz)
                    self.problem = "Relay could not read Planning Center's plans; it scheduled from its own rules."
                for svc in wanted:
                    if svc["key"] in created or svc["start"] < now + LEAD_S:
                        continue
                    have = next((b for b in self.upcoming if b["start"] and abs(b["start"] - svc["start"]) <= ALREADY_S
                                 and b["id"] not in {v["id"] for v in created.values()}), None)
                    made = have is None
                    if have is None:
                        have = await self.create(svc, sched, tz)
                        self.upcoming.append(have)
                        self.upcoming.sort(key=lambda b: b["start"] or float("inf"))
                        self.made.append(have["title"])
                        log.info("youtube: scheduled %s for %s", have["id"], svc["key"])
                    created[svc["key"]] = {"id": have["id"], "at": svc["start"], "made": made}
                    config.save({KEY: {"created": created}})
            elif sched["on"]:
                self.problem = "Press Connect again and allow Relay to schedule live streams."
            if created != st["created"]:
                config.save({KEY: {"created": created}})
        except (Problem, oauth.Problem) as exc:
            self.problem = words(exc)
            return
        if push:
            await self.push_all()

    def candidates(self) -> list[dict]:
        live = [{"id": b["id"], "title": b["title"], "start": b.get("started"), "live": True,
                 "watch": b["watch"], "privacy": b.get("privacy", ""), "bound": True}
                for b in service.broadcasts if b.get("live") or b.get("state") in ("testing", "liveStarting")]
        ids = {b["id"] for b in live}
        return live + [b for b in self.upcoming if b["id"] not in ids]

    async def push_all(self) -> None:
        for link in stored()["links"]:
            if link["auto"]:
                await self.push(link["id"], force=False)

    async def push(self, link_id: str, force: bool = True) -> dict:
        """Write the link's broadcast into its episode. Not forced, only when it changed since the last push."""
        links = stored()["links"]
        link = next((x for x in links if x["id"] == link_id), None)
        if link is None:
            return {"ok": False, "error": "That link is gone. Reload the page."}
        b = pick(link, self.candidates(), zone(), time.time())
        if b is None:
            msg = "No upcoming YouTube broadcast matches this episode yet."
            self.link_errors[link_id] = msg
            return {"ok": False, "error": msg}
        if not force and b["id"] == link["pushed"]:
            return {"ok": True, "video": b["id"], "unchanged": True}
        client = pc_client()
        if client is None:
            self.link_errors[link_id] = NO_PC
            return {"ok": False, "error": NO_PC}
        from ..planning_center import planningcenter as pcm

        try:
            await asyncio.to_thread(client.set_episode_video, link["episode_id"], watch(b["id"]))
        except pcm.PlanningCenterError as exc:
            msg = pcm.operator_message(exc)
            self.link_errors[link_id] = msg
            return {"ok": False, "error": msg}
        self.link_errors.pop(link_id, None)
        for x in links:
            if x["id"] == link_id:
                x["pushed"], x["pushed_at"] = b["id"], time.time()
        config.save({KEY: {"links": links}})
        log.info("youtube: sent %s to Planning Center episode %s", b["id"], link["episode_id"])
        return {"ok": True, "video": b["id"], "title": b.get("title") or ""}

    def snapshot(self) -> dict:
        st, tz, now = stored(), zone(), time.time()
        cands = self.candidates()
        links = []
        for x in st["links"]:
            b = pick(x, cands, tz, now)
            links.append(dict(x, target=({"id": b["id"], "title": b.get("title") or "", "start": b.get("start"),
                                          "watch": watch(b["id"])} if b else None),
                              error=self.link_errors.get(x["id"], "")))
        made = {v["id"] for v in st["created"].values() if v.get("made")}
        return {"schedule": st["schedule"], "links": links, "can_write": can_write(), "pc": _pc_on(),
                "zone": str(tz), "problem": self.problem, "checked_at": self.checked_at or None,
                "upcoming": [dict(b, relay=b["id"] in made) for b in self.upcoming],
                "streams": self.streams, "made": self.made}


planner = Planner()
