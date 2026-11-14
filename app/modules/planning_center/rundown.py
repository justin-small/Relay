"""The rundown (#148): today's plan as a production team reads it, for /screen and the admin page.

Built from what the follower (pclive.py) already holds for a service type: the plan, its items, the LIVE state,
and who is scheduled. Nothing here calls Planning Center.

- Clock times. Each item's planned start is worked out from the chosen service time and the lengths before it
  (pre-service items count back from the start, post-service items run on from the end). Once LIVE is running,
  items already played show when they really started, and everything after the current item is re-projected from
  the current item's actual start, so the rest of the rundown says when things will really happen.
- People. Who is scheduled for that service time, on which team and position, and whether they confirmed.
- Mics. The operator maps positions to mics in admin. A position with several mics and several confirmed people
  gives each person one of its mics, drawn at random once per plan and service time and then kept: the draw only
  changes when someone confirms or drops out, or when the operator presses Redraw. People past the last mic get
  none.
- Song leader. From the item note the operator names (first line), else whoever holds the leader position.
- Sermon notes. The item note the operator names, from the current item or the next one that has it.

Names, photos and notes are personal data: the rundown goes to /screen (the production team's display, by design,
decided 2026-11-14) and the admin page, never to the panels' Service feed or the audience caption pages.
"""
from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import planningcenter as pc

MAX_MAPPINGS = 64
MAX_MICS = 8          # per position
MAX_DRAWS = 200       # stored draws kept, newest last
NOTE_MAX = 4000       # sermon notes sent, characters
LABEL_RE = re.compile(r"^[\w .#/+-]{1,12}$")
DEFAULTS = {"screen": False, "leader_note": "Leader", "leader_position": "Worship Leader",
            "sermon_note": "Sermon Notes", "mics": [], "draws": {}}


def _text(v: Any, n: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:n]


def clean_rundown(raw: Any) -> dict:
    """The "rundown" settings under "planning_center" in config.json."""
    raw = raw if isinstance(raw, dict) else {}
    mics: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for m in raw.get("mics") or []:
        if not isinstance(m, dict) or len(mics) >= MAX_MAPPINGS:
            continue
        team, pos = _text(m.get("team"), 80), _text(m.get("position"), 80)
        labels = []
        for x in m.get("mics") or []:
            x = _text(x, 12)
            if LABEL_RE.match(x) and x not in labels:
                labels.append(x)
        if not pos or (team, pos) in seen or not labels:
            continue
        seen.add((team, pos))
        mics.append({"team": team, "position": pos, "mics": labels[:MAX_MICS], "sings": m.get("sings") is not False,
                     "label": _text(m.get("label"), 24)})
    draws: dict[str, dict[str, str]] = {}
    src = raw.get("draws")
    for k, v in (list(src.items())[-MAX_DRAWS:] if isinstance(src, dict) else ()):
        if isinstance(k, str) and len(k) < 240 and isinstance(v, dict):
            draws[k] = {str(a)[:20]: _text(b, 12) for a, b in list(v.items())[:MAX_MICS] if str(a).isdigit()}
    return {
        "screen": raw.get("screen") is True,
        "leader_note": _text(raw.get("leader_note", DEFAULTS["leader_note"]), 60),
        "leader_position": _text(raw.get("leader_position", DEFAULTS["leader_position"]), 80),
        "sermon_note": _text(raw.get("sermon_note", DEFAULTS["sermon_note"]), 60),
        "mics": mics,
        "draws": draws,
    }


# -- service time ------------------------------------------------------------------------------------------------
def _hhmm(ts: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, zone).strftime("%H%M")


def service_times(plan: dict | None, zone: ZoneInfo) -> list[dict]:
    return [{"id": t["id"], "starts_at": t["starts_at"], "ends_at": t["ends_at"], "hhmm": _hhmm(t["starts_at"], zone),
             "name": t.get("name") or ""}
            for t in pc.service_times(plan or {})]


def pick_time(plan: dict | None, live: dict | None, now: float, zone: ZoneInfo, want: str = "") -> dict | None:
    """The service time the rundown is for: the one asked for (HHMM, local), else the one LIVE is running,
    else the first not yet over, else the last."""
    times = service_times(plan, zone)
    if not times:
        return None
    if want:
        hit = next((t for t in times if t["hhmm"] == want), None)
        if hit:
            return hit
    cur = (live or {}).get("current")
    if cur and pc.running(live, now):
        hit = next((t for t in times if t["id"] == cur["plan_time"]), None)
        if hit:
            return hit
    for t in times:
        end = t["ends_at"] if t["ends_at"] and t["ends_at"] > t["starts_at"] else t["starts_at"] + pc.SERVICE_DEFAULT_S
        if end > now:
            return t
    return times[-1]


# -- people and mics ---------------------------------------------------------------------------------------------
def _serves(member: dict, time_id: str) -> bool:
    return member["times"] is None or time_id in member["times"]


def _key(m: dict) -> str:
    return f"{m['team']}|{m['position']}"


def _matches(mapping: dict, member: dict) -> bool:
    return (member["position"].lower() == mapping["position"].lower()
            and (not mapping["team"] or member["team"].lower() == mapping["team"].lower()))


def draw_mics(mapping: dict, people: list[dict], kept: dict[str, str], rng: random.Random | None = None) -> dict:
    """Mic per confirmed person for one position. Keeps every earlier draw that still holds and draws only for
    people without a mic; extras get none. Returns {member id: mic}."""
    rng = rng or random.Random()
    ids = sorted(p["id"] for p in people if p["status"] == "confirmed")
    out = {i: m for i, m in kept.items() if i in ids and m in mapping["mics"]}
    if len(set(out.values())) != len(out):   # a stored draw gave one mic twice: start this position over
        out = {}
    free = [m for m in mapping["mics"] if m not in out.values()]
    rng.shuffle(free)
    for i in ids:
        if i not in out and free:
            out[i] = free.pop()
    return out


def draw_key(plan_id: str, time_id: str, mapping: dict) -> str:
    return f"{plan_id}:{time_id}:{_key(mapping)}"


# -- the rundown -------------------------------------------------------------------------------------------------
def _note(item: dict, category: str) -> str:
    if not category:
        return ""
    for n in item.get("notes") or []:
        if n["category"].strip().lower() == category.lower() and n["content"].strip():
            return n["content"].strip()
    return ""


def _item_time(item: dict, time_id: str) -> dict | None:
    return next((t for t in item.get("times") or [] if t and t["plan_time"] == time_id), None)


def build(track: Any, settings: dict, now: float, zone: ZoneInfo, want_time: str = "",
          rng: random.Random | None = None) -> tuple[dict, dict]:
    """The rundown for one followed service type, and the mic draws as they now stand (the caller saves them
    when they changed). `track` is pclive.Track."""
    rs = settings["rundown"]
    plan, live = track.plan, track.live
    out: dict[str, Any] = {"type": "rundown", "service_type": track.st, "now": now, "zone": zone.key,
                           "plan": None, "items": [],
                           "times": [], "time": None, "header": None, "people": [], "mics": [], "notes": None,
                           "live": bool((live or {}).get("controller")),
                           "controller": ((live or {}).get("controller") or {}).get("name")}
    draws = dict(rs["draws"])
    if plan is None:
        return out, draws
    out["plan"] = {"id": plan["id"], "title": plan["title"] or plan["series_title"] or "Today's plan",
                   "dates": plan["dates"]}
    out["times"] = [{"id": t["id"], "hhmm": t["hhmm"], "starts_at": t["starts_at"]}
                    for t in service_times(plan, zone)]
    pt = pick_time(plan, live, now, zone, want_time)
    if pt is None:
        return out, draws
    out["time"] = {"id": pt["id"], "hhmm": pt["hhmm"], "starts_at": pt["starts_at"], "chosen": bool(want_time)}
    tid = pt["id"]

    # Items for this service time, in order (an item excluded from this time is left out).
    items = [i for i in sorted(track.items, key=lambda i: i["sequence"])
             if not ((_item_time(i, tid) or {}).get("exclude"))]
    cur_live = (live or {}).get("current")
    running = bool(cur_live and pc.running(live, now) and cur_live["plan_time"] == tid)
    cur_id = cur_live["item"] if running else None

    # Planned starts.
    pre = [i for i in items if i["position"] == "pre"]
    during = [i for i in items if i["position"] == "during"]
    post = [i for i in items if i["position"] == "post"]
    planned: dict[str, float] = {}
    t = pt["starts_at"] - sum(i["length"] for i in pre)
    for i in pre:
        planned[i["id"]], t = t, t + i["length"]
    t = pt["starts_at"]
    for i in during:
        planned[i["id"]], t = t, t + i["length"]
    planned_end = t
    for i in post:
        planned[i["id"]], t = t, t + i["length"]
    order = pre + during + post

    # Actual starts so far, and the projection from the current item on.
    ix = next((n for n, i in enumerate(order) if i["id"] == cur_id), None)
    projected: dict[str, float] = {}
    if ix is not None:
        cur = order[ix]
        started = cur_live["live_start_at"]
        t = max(started + cur["length"], now)
        for i in order[ix + 1:]:
            projected[i["id"]], t = t, t + i["length"]
    proj_end = planned_end
    if ix is not None:
        last_during = next((i for i in reversed(order) if i["position"] == "during"), None)
        if last_during is not None and last_during["id"] in projected:
            proj_end = projected[last_during["id"]] + last_during["length"]
        elif last_during is not None and last_during["id"] == cur_id:
            proj_end = max(cur_live["live_start_at"] + last_during["length"], now)

    # People serving this time.
    members = [m for m in track.team if _serves(m, tid)]
    mic_of: dict[str, str] = {}
    mapping_of: dict[str, dict] = {}
    for mp in rs["mics"]:
        people = [m for m in members if _matches(mp, m)]
        key = draw_key(plan["id"], tid, mp)
        got = draw_mics(mp, people, draws.get(key, {}), rng)
        if got:
            draws[key] = got
        elif key in draws:
            del draws[key]
        for mid, mic in got.items():
            mic_of[mid] = mic
            mapping_of[mid] = mp
    # Keep only this plan's draws and the newest others.
    if len(draws) > MAX_DRAWS:
        draws = dict(list(draws.items())[-MAX_DRAWS:])

    leader_pos = rs["leader_position"].lower()
    leader_default = next((m for m in members if m["position"].lower() == leader_pos and m["status"] != "declined"),
                          None)

    def person(m: dict) -> dict:
        return {"id": m["id"], "name": m["name"], "status": m["status"], "photo": m["photo"],
                "team": m["team"], "position": m["position"], "mic": mic_of.get(m["id"])}

    singers = [person(m) for m in members if m["id"] in mic_of and mapping_of[m["id"]]["sings"]]
    singers.sort(key=lambda p: _mic_sort(p["mic"]))

    def song(item: dict) -> dict:
        lead = (_note(item, rs["leader_note"]).splitlines() or [""])[0].strip()[:60]
        if not lead and leader_default is not None:
            lead = leader_default["name"]
        return {"key": item["key"], "leader": lead, "mics": [{"mic": p["mic"], "name": p["name"]} for p in singers]}

    rows = []
    for n, i in enumerate(order):
        it = _item_time(i, tid) or {}
        if ix is not None and n < ix:
            state = "done"
        elif n == ix:
            state = "current"
        elif ix is not None and n == ix + 1:
            state = "next"
        elif ix is None and it.get("live_end_at"):
            state = "done"
        else:
            state = ""
        rows.append({
            "id": i["id"], "title": i["title"], "kind": i["type"], "section": i["position"],
            "length": i["length"], "planned": planned.get(i["id"]),
            "actual": it.get("live_start_at") if state in ("done", "current") else None,
            "projected": projected.get(i["id"]), "state": state,
            "song": song(i) if i["type"] == "song" else None,
        })
    out["items"] = rows

    # Header: what is up, how long it has left, how the service is running.
    cur_item = order[ix] if ix is not None else None
    nxt = order[ix + 1] if ix is not None and ix + 1 < len(order) else None
    timers = pc.timers(plan, track.items, live, now, track.service_start.get(tid)) if running else {}
    out["header"] = {
        "current": cur_item["title"] if cur_item else None,
        "kind": cur_item["type"] if cur_item else None,
        "song": song(cur_item) if cur_item and cur_item["type"] == "song" else None,
        "item_started_at": cur_live["live_start_at"] if running else None,
        "item_length": cur_item["length"] if cur_item else None,
        "next": nxt["title"] if nxt else None,
        "planned_end": planned_end,
        "projected_end": proj_end,
        "over_s": timers.get("service_over_s") if running else None,
    }

    # Mic channels, in mic order: who holds each mic, and whether it is in use for the item that is up.
    channels = []
    for mp in rs["mics"]:
        holders = {mic: mid for mid, mic in draws.get(draw_key(plan["id"], tid, mp), {}).items()}
        for mic in mp["mics"]:
            m = next((x for x in members if x["id"] == holders.get(mic)), None)
            on = bool(cur_item and m and ((cur_item["type"] == "song") == mp["sings"]))
            channels.append({"mic": mic, "label": mp["label"], "position": mp["position"], "team": mp["team"],
                             "person": person(m) if m else None, "on": on})
    channels.sort(key=lambda c: _mic_sort(c["mic"]))
    out["mics"] = channels

    # The roster, grouped by team in Planning Center's order, with positions still to fill.
    people = [person(m) for m in members]
    for need in track.needed:
        if need["time"] in (None, tid):
            for _ in range(need["quantity"]):
                people.append({"id": "", "name": "", "status": "unfilled", "photo": "", "team": need["team"],
                               "position": need["position"], "mic": None})
    singing = {p["id"] for p in singers} if cur_item and cur_item["type"] == "song" else set()
    for p in people:
        p["singing"] = p["id"] in singing
    out["people"] = people

    # Sermon notes: from the current item, else the next one that has them.
    start = ix if ix is not None else 0
    for i in order[start:]:
        text = _note(i, rs["sermon_note"])
        if text:
            out["notes"] = {"item": i["title"], "planned": planned.get(i["id"]),
                            "projected": projected.get(i["id"]), "text": text[:NOTE_MAX]}
            break
    return out, draws


def _mic_sort(mic: str | None) -> tuple:
    m = re.match(r"\D*(\d+)", mic or "")
    return (0, int(m.group(1)), mic) if m else (1, 0, mic or "")


def positions(tracks: list[Any]) -> list[dict]:
    """Every team and position on today's plans (scheduled or still to fill), for the mic map in admin."""
    seen: dict[tuple[str, str], int] = {}
    for tr in tracks:
        for m in list(tr.team) + list(tr.needed):
            k = (m["team"], m["position"])
            if m["position"]:
                seen[k] = seen.get(k, 0) + 1
    return [{"team": t, "position": p} for t, p in sorted(seen)]


def signature(msg: dict) -> str:
    """What changed, ignoring the clock."""
    return json.dumps({k: v for k, v in msg.items() if k != "now"}, sort_keys=True, default=str)


def utcnow() -> float:
    return datetime.now(timezone.utc).timestamp()
