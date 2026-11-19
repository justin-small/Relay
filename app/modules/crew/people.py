"""Crew roles and names matched to Planning Center (#227).

A person's role is free text ("Camera 1"), set by an admin. Planning Center's spelling of their name is found by
matching the account's name against the people scheduled on the followed plans. A match is only made when it is
confident: one person is clearly the closest, and no other account already holds that person. The match stores the
Planning Center person id in core's directory (app/directory.py, so the link outlives Crew being off) and, when the
spelling differs, suggests the name; an admin accepts or dismisses it.
"""
from __future__ import annotations

import difflib
import logging

from ... import audit, db, directory, users
from ...text import plain

log = logging.getLogger("relay.crew")

ROLE_MAX = 40
CONFIDENT = 0.85      # a first and last name must each be at least this close
MARGIN = 0.05         # and the best person must beat the runner-up by this much
MIGRATIONS = __import__("pathlib").Path(__file__).resolve().parent / "migrations"

# Common short forms, so "Mike" finds "Michael". Both directions are looked up through one group.
_GROUPS = ("michael mike mick mickey", "william will bill billy", "robert rob bob bobby", "richard rick dick rich",
           "james jim jimmy", "john jon johnny", "joseph joe joey", "thomas tom tommy", "daniel dan danny",
           "matthew matt", "christopher chris", "nicholas nick", "anthony tony", "andrew andy drew",
           "elizabeth liz beth betty lizzie", "katherine catherine kate katie kathy cathy",
           "jennifer jen jenny", "jessica jess jessie", "samuel sam", "benjamin ben benny", "alexander alex",
           "stephen steven steve", "patricia pat patty trish", "margaret maggie meg peggy", "susan sue suzy",
           "deborah debbie deb", "rebecca becky becca", "abigail abby", "edward ed eddie ted", "charles charlie chuck",
           "david dave davey", "timothy tim timmy", "jonathan jon", "joshua josh", "zachary zach zack")
_NICK: dict[str, int] = {}
for _i, _g in enumerate(_GROUPS):
    for _n in _g.split():
        _NICK.setdefault(_n, _i)

_ready = False


def ready() -> None:
    """Create the table on first use (raises db.Unavailable while the database is down)."""
    global _ready
    if not _ready:
        db.migrate("crew", MIGRATIONS)
        _ready = True


def forget() -> None:
    """Tests: check the table again on next use."""
    global _ready
    _ready = False


class Invalid(Exception):
    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.message, self.status = text, status


def clean_role(raw) -> str:
    """A role as typed: one line, at most ROLE_MAX characters. Empty clears it."""
    if raw is None:
        return ""
    if not isinstance(raw, str) or any(ord(c) < 32 or ord(c) == 127 for c in raw):
        raise Invalid("A role is one line of text.")
    role = " ".join(raw.split())
    if len(role) > ROLE_MAX:
        raise Invalid(f"A role is at most {ROLE_MAX} characters.")
    return role


# -- matching -----------------------------------------------------------------------------------------------
def norm(name: str) -> str:
    """Lowercase, no accents or punctuation, single spaces."""
    return plain(name)


def _first_score(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a in _NICK and _NICK.get(a) == _NICK.get(b):
        return 0.95
    return difflib.SequenceMatcher(None, a, b).ratio()


def similarity(mine: str, theirs: str) -> float:
    """0..1. Exact once normalised is 1. Otherwise both names need a first and last name, and the lower of the
    two parts' closeness counts, so a close first name never rescues a different surname."""
    a, b = norm(mine), norm(theirs)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = a.split(), b.split()
    if len(ta) < 2 or len(tb) < 2:
        return 0.0
    last = difflib.SequenceMatcher(None, ta[-1], tb[-1]).ratio()
    return min(_first_score(ta[0], tb[0]), last)


def best_match(name: str, people: list[dict], taken: set[str]) -> dict | None:
    """The one person this name is confidently, and only, a match for; None otherwise."""
    scored: dict[str, tuple[float, dict]] = {}
    for p in people:
        s = similarity(name, p["name"])
        if s > scored.get(p["id"], (0.0, p))[0] or p["id"] not in scored:
            scored[p["id"]] = (s, p)
    ranked = sorted(scored.values(), key=lambda t: -t[0])
    if not ranked or ranked[0][0] < CONFIDENT:
        return None
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < MARGIN:
        return None
    person = ranked[0][1]
    return None if person["id"] in taken else person


# -- storage ------------------------------------------------------------------------------------------------
def people_list() -> list[dict]:
    ready()
    rows = db.query(
        "select u.id, u.username, u.name, u.role as account_role, u.disabled, p.role from users u"
        " left join crew_profiles p on p.user_id = u.id order by lower(u.name), u.id")
    linked = directory.link_rows(directory.PCO)
    return [{"id": r["id"], "username": r["username"], "name": r["name"], "account_role": r["account_role"],
             "disabled": bool(r["disabled"]), "role": r["role"] or "", "linked": r["id"] in linked,
             "pc_name": (linked.get(r["id"]) or {}).get("name", ""),
             "suggested_name": (linked.get(r["id"]) or {}).get("suggested_name", "")} for r in rows]


def profile(user_id: int) -> dict:
    ready()
    row = db.one("select role from crew_profiles where user_id = ?", (user_id,))
    found = directory.link_of(user_id, directory.PCO)
    return {"role": (row or {}).get("role") or "", "linked": found is not None,
            "pc_name": (found or {}).get("name", "")}


def set_role(user_id: int, raw) -> str:
    role = clean_role(raw)
    ready()
    if db.one("select id from users where id = ?", (user_id,)) is None:
        raise Invalid("No such person.", 404)
    db.execute("insert into crew_profiles (user_id, role, updated_at) values (?, ?, ?)"
               " on conflict (user_id) do update set role = excluded.role, updated_at = excluded.updated_at",
               (user_id, role, db.now()))
    return role


def accept_name(user_id: int, actor) -> str:
    name = directory.suggestion(user_id, directory.PCO)
    if not name:
        raise Invalid("There is no name to accept.", 404)
    try:
        users.update(user_id, name=name, actor=actor)
    except users.Refused as exc:
        raise Invalid("You cannot change that account's name.", 403) from exc
    directory.clear_suggestion(user_id, directory.PCO, confirmed_by=getattr(actor, "id", None))
    return name


def dismiss_name(user_id: int) -> None:
    directory.clear_suggestion(user_id, directory.PCO, dismiss=True)


def unlink(user_id: int) -> None:
    """Forget a Planning Center match (a wrong one), so the next scan may try again."""
    directory.unlink(user_id, directory.PCO)


def scan(people: list[dict]) -> int:
    """Match accounts with no link yet against `people` (dicts with "id" and "name", from Planning Center).
    Returns how many were linked. Never moves an existing link."""
    people = [p for p in people if p.get("id") and p.get("name")]
    if not people:
        return 0
    linked = 0
    held = directory.links(directory.PCO)
    taken = set(held)
    mine = set(held.values())
    for r in db.query("select id, name from users where disabled = 0"):
        if r["id"] in mine:
            continue
        person = best_match(r["name"], people, taken)
        if person is None:
            continue
        differs = person["name"].strip() != r["name"].strip() and person["name"] != directory.dismissed(
            r["id"], directory.PCO)
        if not directory.link(r["id"], directory.PCO, person["id"], person["name"], matched_by="name",
                              suggested_name=person["name"] if differs else None):
            continue
        taken.add(person["id"])
        linked += 1
        audit.record("crew.match", "Matched a crew account to Planning Center", target=r["id"],
                     detail={"suggested": bool(differs)})
    return linked
