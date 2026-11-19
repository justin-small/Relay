"""Crew checklists per position (#232).

An operator, or a team lead for their own position, writes a checklist template for a Planning Center team (the
"position") or for everyone serving. Each person sees the templates for the teams they serve on today plus the
all-crew ones, ticks items on their phone, and the operator watches the progress. A tick belongs to the plan it was
for (the key the arrival board uses), so every service starts with a fresh list and the earlier ones stay readable
for KEEP_DAYS.
"""
from __future__ import annotations

import time

from ... import db, directory
from ...text import same_name
from . import checkin, paging, people, refs

TITLE_MAX, ITEM_MAX, POSITION_MAX = 60, 140, 80
ITEMS_MAX, LISTS_MAX = 40, 100
KEEP_DAYS = 180
PICK = (directory.TEAM, directory.POSITION)    # a checklist is for a team or for one position of it
Invalid = people.Invalid


def _text(raw, limit: int, what: str, required: bool = True) -> str:
    if raw is not None and not isinstance(raw, str):
        raise Invalid(f"{what} is text.")
    text = " ".join((raw or "").split())
    if required and not text:
        raise Invalid(f"Give the {what.lower()}.")
    if len(text) > limit:
        raise Invalid(f"{what} is at most {limit} characters.")
    return text


def clean(payload) -> dict:
    if not isinstance(payload, dict):
        raise Invalid("Send the checklist as an object.")
    items = payload.get("items")
    if not isinstance(items, list):
        raise Invalid("Give the checklist's items as a list.")
    items = [_text(i, ITEM_MAX, "An item") for i in items if not (isinstance(i, str) and not i.strip())]
    if not items:
        raise Invalid("A checklist needs at least one item.")
    if len(items) > ITEMS_MAX:
        raise Invalid(f"A checklist has at most {ITEMS_MAX} items.")
    ref, name = refs.clean_pick(payload.get("position_ref"), payload.get("position"), PICK, POSITION_MAX, "Position")
    return {"title": _text(payload.get("title"), TITLE_MAX, "Title"), "position": name, "position_ref": ref,
            "items": items}


def same(a: str, b: str) -> bool:
    return same_name(a, b)


# -- who may edit ------------------------------------------------------------------------------------------------
def lead_rows(user_id: int) -> list[dict]:
    """The teams and positions this person leads: {"ref", "name"} (the name is Planning Center's current one)."""
    people.ready()
    return [{"ref": r["position_ref"], "name": directory.describe(r["position_ref"], r["position"])["name"]}
            for r in db.query("select position, position_ref from crew_checklist_leads where user_id = ?", (user_id,))]


def leads_of(user_id: int) -> list[str]:
    return sorted((r["name"] for r in lead_rows(user_id)), key=str.lower)


def can_edit(user, position: str, perm: str = "crew.checklist_edit", position_ref: str = "") -> bool:
    """Operators (`perm`) edit everything; a team lead edits only their own positions. Leads are named once and
    lead a position's checklists and resources (#233) alike."""
    if user.can(perm):
        return True
    return bool(position or position_ref) and any(
        directory.same_item(position_ref, position, l["ref"], l["name"]) for l in lead_rows(user.id))


def list_leads() -> list[dict]:
    people.ready()
    return [{"user_id": r["user_id"], "name": r["name"], "position": f["name"], "position_ref": r["position_ref"],
             "missing": f["missing"], "unmatched": f["unmatched"]}
            for r in db.query(
                "select l.user_id, u.name, l.position, l.position_ref from crew_checklist_leads l"
                " join users u on u.id = l.user_id order by lower(l.position), lower(u.name)")
            for f in [refs.flags(r["position_ref"], r["position"])]]


def add_lead(user_id, position, position_ref="") -> None:
    ref, name = refs.clean_pick(position_ref, position, PICK, POSITION_MAX, "Position", required=True)
    people.ready()
    if not isinstance(user_id, int) or isinstance(user_id, bool) or db.one(
            "select id from users where id = ?", (user_id,)) is None:
        raise Invalid("No such person.", 404)
    db.execute("insert into crew_checklist_leads (user_id, position, position_ref) values (?, ?, ?)"
               " on conflict do nothing", (user_id, name, ref))


def remove_lead(user_id: int, position: str, position_ref: str = "") -> None:
    people.ready()
    db.execute("delete from crew_checklist_leads where user_id = ? and position_ref = ? and (? != '' or position = ?)",
               (user_id, position_ref, position_ref, position))


# -- templates ---------------------------------------------------------------------------------------------------
def templates() -> list[dict]:
    people.ready()
    lists = db.query("select id, title, position, position_ref from crew_checklists order by position = '' desc,"
                     " lower(position), lower(title), id")
    items: dict[int, list[dict]] = {}
    for r in db.query("select id, checklist_id, text from crew_checklist_items order by checklist_id, sort, id"):
        items.setdefault(r["checklist_id"], []).append({"id": r["id"], "text": r["text"]})
    return [{"id": c["id"], "title": c["title"], "items": items.get(c["id"], []), "position_ref": c["position_ref"],
             "pick": {"ref": c["position_ref"], "name": f["name"]},
             **{"position": f["name"], "missing": f["missing"], "unmatched": f["unmatched"]}}
            for c in lists for f in [refs.flags(c["position_ref"], c["position"])]]


def get(list_id: int) -> dict | None:
    return next((t for t in templates() if t["id"] == list_id), None)


def save(data: dict, list_id: int | None = None) -> int:
    """Create a template, or replace one. An item whose text is unchanged keeps its id, so a person's tick for this
    service survives an edit."""
    people.ready()
    with db.transaction() as tx:
        if list_id is None:
            if tx.one("select count(*) as n from crew_checklists")["n"] >= LISTS_MAX:
                raise Invalid(f"There can be at most {LISTS_MAX} checklists.")
            list_id = tx.one("insert into crew_checklists (title, position, position_ref, updated_at)"
                             " values (?, ?, ?, ?) returning id",
                             (data["title"], data["position"], data["position_ref"], db.now()))["id"]
            old: list[dict] = []
        else:
            if tx.one("select id from crew_checklists where id = ?", (list_id,)) is None:
                raise Invalid("No such checklist.", 404)
            tx.execute("update crew_checklists set title = ?, position = ?, position_ref = ?, updated_at = ? where id = ?",
                       (data["title"], data["position"], data["position_ref"], db.now(), list_id))
            old = tx.query("select id, text from crew_checklist_items where checklist_id = ? order by sort, id",
                           (list_id,))
        keep: list[int] = []
        for n, text in enumerate(data["items"]):
            hit = next((o for o in old if o["text"] == text and o["id"] not in keep), None)
            if hit:
                keep.append(hit["id"])
                tx.execute("update crew_checklist_items set sort = ? where id = ?", (n, hit["id"]))
            else:
                keep.append(tx.one("insert into crew_checklist_items (checklist_id, sort, text) values (?, ?, ?)"
                                   " returning id", (list_id, n, text))["id"])
        for o in old:
            if o["id"] not in keep:
                tx.execute("delete from crew_checklist_items where id = ?", (o["id"],))
    return list_id


def delete(list_id: int) -> None:
    people.ready()
    db.execute("delete from crew_checklists where id = ?", (list_id,))


# -- what a person sees ------------------------------------------------------------------------------------------
def held(user_id: int, tracks) -> list[dict]:
    """The plan rows (team and position, with their ids) this person is scheduled in today; none when they are not
    matched to Planning Center."""
    people.ready()
    pid = directory.external_id(user_id, directory.PCO)
    if not pid:
        return []
    return [m for m in paging.active_members(tracks) if m["person"] == pid]


def teams_of(user_id: int, tracks) -> list[str]:
    """The Planning Center teams this person serves on today, by name, for showing."""
    return sorted({m["team"] for m in held(user_id, tracks)})


def applies(template: dict, mine_today: list[dict]) -> bool:
    """A template (or resource) is for everyone, or for a team or position this person holds today (`held`)."""
    if not template["position"] and not template.get("position_ref"):
        return True
    return any(paging.holds(m, template.get("position_ref", ""), template["position"]) for m in mine_today)


def _forget_old(now: float) -> None:
    db.execute("delete from crew_checklist_done where done_ts < ?", (now - KEEP_DAYS * 86400,))


def mine(user_id: int, key: str, tracks) -> list[dict]:
    """This person's checklists for the plan, each item with when they ticked it (None while open)."""
    people.ready()
    teams = held(user_id, tracks)
    done = {r["item_id"]: r["done_ts"] for r in db.query(
        "select item_id, done_ts from crew_checklist_done where plan_id = ? and user_id = ?", (key, user_id))}
    return [{"id": t["id"], "title": t["title"], "position": t["position"],
             "items": [{"id": i["id"], "text": i["text"], "done_ts": done.get(i["id"])} for i in t["items"]]}
            for t in templates() if applies(t, teams)]


def tick(user_id: int, item_id: int, on, key: str, tracks, now: float | None = None) -> None:
    """Tick or untick one item, only if it is on one of this person's lists."""
    if not isinstance(on, bool):
        raise Invalid("Say whether the item is done.")
    people.ready()
    ts = time.time() if now is None else now
    if not any(i["id"] == item_id for t in mine(user_id, key, tracks) for i in t["items"]):
        raise Invalid("That item is not on your checklists.", 404)
    _forget_old(ts)
    if on:
        db.execute("insert into crew_checklist_done (plan_id, item_id, user_id, done_ts) values (?, ?, ?, ?)"
                   " on conflict do nothing", (key, item_id, user_id, ts))
    else:
        db.execute("delete from crew_checklist_done where plan_id = ? and item_id = ? and user_id = ?",
                   (key, item_id, user_id))


# -- the operator's board ----------------------------------------------------------------------------------------
def plans() -> list[dict]:
    """Services that have ticks kept, newest first: {plan, ts}."""
    people.ready()
    return [{"plan": r["plan_id"], "ts": r["ts"]} for r in db.query(
        "select plan_id, max(done_ts) as ts from crew_checklist_done group by plan_id order by ts desc limit 30")]


def board(tracks, zone, plan: str | None = None, now: float | None = None) -> dict:
    """Progress per person for a plan (today's by default): everyone on today's plan with a crew account, or, for a
    past plan, whoever ticked something. Each row carries one entry per checklist that applies to the person."""
    people.ready()
    ts = time.time() if now is None else now
    today = checkin.plan_key(tracks, zone, ts)
    key = plan or today
    tpls = templates()
    done: dict[int, dict[int, float]] = {}
    for r in db.query("select user_id, item_id, done_ts from crew_checklist_done where plan_id = ?", (key,)):
        done.setdefault(r["user_id"], {})[r["item_id"]] = r["done_ts"]
    names = {r["id"]: r["name"] for r in db.query("select id, name from users")}
    who: dict[int, list[dict]] = {}
    if key == today:
        linked = directory.links(directory.PCO)
        for m in paging.active_members(tracks):
            uid = linked.get(m["person"])
            if uid:
                who.setdefault(uid, []).append(m)
    for uid in done:
        who.setdefault(uid, held(uid, tracks) if key == today else [])
    out = []
    for uid, teams in who.items():
        lists = []
        for t in tpls:
            ids = [i["id"] for i in t["items"]]
            if applies(t, teams) or any(i in done.get(uid, {}) for i in ids):
                ticked = [done[uid][i] for i in ids if i in done.get(uid, {})]
                lists.append({"id": t["id"], "title": t["title"], "total": len(ids), "done": len(ticked),
                              "last_ts": max(ticked) if ticked else None,
                              "open": [i["text"] for i in t["items"] if i["id"] not in done.get(uid, {})]})
        if lists:
            out.append({"user_id": uid, "name": names.get(uid, "Someone"), "teams": sorted({m["team"] for m in teams}), "lists": lists})
    out.sort(key=lambda r: r["name"].lower())
    return {"plan": key, "today": key == today, "people": out, "plans": plans(),
            "done": sum(l["done"] for r in out for l in r["lists"]), "total": sum(l["total"] for r in out for l in r["lists"])}
