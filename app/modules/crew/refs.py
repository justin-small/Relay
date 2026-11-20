"""Saved teams and positions are a directory ref plus a name (#525).

Call times, checklists, leads and resources used to hold a typed Planning Center name. They now hold `team:pco:<id>` or
`position:pco:<team id>/<position id>` (app/directory.py) and the name the ref had when it was saved. `adopt` gives a
row from before a ref by matching its name (text.same_name); one that matches nothing stays a name and is flagged on
the admin page. `refresh` runs when Planning Center's teams may have changed: it adopts, then rewrites the saved names
of renamed teams and positions, so the saved name is right even with Planning Center off.
"""
from __future__ import annotations

from ... import db, directory
from . import people

# The four places that save a team or position, with the kinds of ref a typed name may match (a checklist "position" has
# meant a team name). Written out as plain statements, one set per table, so no SQL is built from names.
CALLS, CHECKLISTS, LEADS, RESOURCES = "calltimes", "checklists", "leads", "resources"
BOTH = (directory.TEAM, directory.POSITION)
TARGETS = {CALLS: (directory.TEAM,), CHECKLISTS: BOTH, LEADS: BOTH, RESOURCES: BOTH}
NAMES = {
    CALLS: "select distinct team as name from crew_calltimes where team_ref = '' and team != ''",
    CHECKLISTS: "select distinct position as name from crew_checklists where position_ref = '' and position != ''",
    LEADS: "select distinct position as name from crew_checklist_leads where position_ref = '' and position != ''",
    RESOURCES: "select distinct position as name from crew_resources where position_ref = '' and position != ''",
}
ADOPT = {
    CALLS: "update crew_calltimes set team_ref = ?, team = ? where team_ref = '' and team = ?",
    CHECKLISTS: "update crew_checklists set position_ref = ?, position = ? where position_ref = '' and position = ?",
    # a lead is keyed by (user, ref, name): one who already leads the matched one keeps that row
    LEADS: "update or ignore crew_checklist_leads set position_ref = ?, position = ? where position_ref = '' and position = ?",
    RESOURCES: "update crew_resources set position_ref = ?, position = ? where position_ref = '' and position = ?",
}
RENAME = {
    CALLS: "update crew_calltimes set team = ? where team_ref = ? and team != ?",
    CHECKLISTS: "update crew_checklists set position = ? where position_ref = ? and position != ?",
    LEADS: "update or ignore crew_checklist_leads set position = ? where position_ref = ? and position != ?",
    RESOURCES: "update crew_resources set position = ? where position_ref = ? and position != ?",
}


def adopt() -> int:
    """Fill in the ref of every saved name that matches exactly one team or position. Returns how many names."""
    people.ready()
    done = 0
    for key, kinds in TARGETS.items():
        for row in db.query(NAMES[key]):
            found = None
            for kind in kinds:
                found = found or directory.match_name(kind, row["name"])
            if found is None:
                continue
            db.execute(ADOPT[key], (found, directory.item(found)["name"], row["name"]))
            if key == LEADS:
                db.execute("delete from crew_checklist_leads where position_ref = '' and position = ?", (row["name"],))
            done += 1
    return done


def refresh() -> None:
    """Read Planning Center's teams, adopt names that now match, and bring saved names up to date: a team or position
    renamed there is renamed in every saved call time, checklist, lead and resource."""
    people.ready()
    directory.sync(directory.PCO)
    adopt()
    for found in db.query("select ref, name from directory_items where provider = ?", (directory.PCO,)):
        for key in TARGETS:
            db.execute(RENAME[key], (found["name"], found["ref"], found["name"]))


def flags(ref_text: str, name: str) -> dict:
    """What the admin page needs to show for one saved team or position: its current name, whether Planning Center no
    longer lists it ("missing") and whether it was never matched to one ("unmatched")."""
    got = directory.describe(ref_text, name)
    return {"name": got["name"], "missing": got["missing"], "unmatched": got["unmatched"]}


def clean_pick(raw_ref, raw_name, kinds, limit: int, what: str, *, required: bool = False) -> tuple[str, str]:
    """A team or position chosen on an admin page, checked: (ref, name). A ref must be one the directory lists (a
    made-up one is refused) and the name becomes the directory's own. With no ref, a typed name is matched to one by
    text.same_name and kept as a name only when nothing matches. ("", "") is "everyone" unless `required`."""
    if raw_ref not in (None, "") and not isinstance(raw_ref, str):
        raise people.Invalid(f"The {what.lower()} is chosen from the list.")
    if raw_name is not None and not isinstance(raw_name, str):
        raise people.Invalid(f"The {what.lower()} is text.")
    if raw_ref:
        parsed = directory.parse_item(raw_ref)
        got = directory.item(raw_ref) if parsed and parsed[0] in kinds else None
        if got is None:
            raise people.Invalid(f"Choose the {what.lower()} from the list.")
        return raw_ref, got["name"]
    name = " ".join((raw_name or "").split())
    if len(name) > limit:
        raise people.Invalid(f"The {what.lower()} is at most {limit} characters.")
    if not name:
        if required:
            raise people.Invalid(f"Choose the {what.lower()}.")
        return "", ""
    found = None
    for kind in kinds:
        found = found or directory.match_name(kind, name)
    return (found, directory.item(found)["name"]) if found else ("", name)
