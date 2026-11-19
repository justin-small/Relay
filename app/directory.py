"""Who a Relay user is in another product (#524, part of the shared-concepts epic #521).

`users` is the one person record. `person_links` (app/migrations/0006_person_links.sql) says which person in a
provider's system ("pco" is Planning Center) a user is, one link per user and provider, and one user per provider id.
It lives in core so a module that wants "the Relay user for this Planning Center person" (intercom, the rundown,
parent paging, crew) asks here and keeps working whichever other modules are on. Matching names is a module's job
(crew's fuzzy match); this stores the result, plus the spelling the provider suggests and what an admin refused.

A person is named by a ref, `person:relay:<user id>` or `person:pco:<Planning Center id>`, for anything that stores
a reference to someone.

A provider module offers the people it knows with `modules.provide("directory.<provider>", fn)` (fn returns dicts
with "id" and "name"); `people(provider)` asks for them and gives [] while that module is off.
"""
from __future__ import annotations

from . import db

PCO = "pco"
REF = "person"


def ref(source: str, ident) -> str:
    """The ref for a person in `source` ("relay" or a provider): ref("pco", "123") is "person:pco:123"."""
    if not source.isidentifier():
        raise ValueError(f"not a person source: {source!r}")
    return f"{REF}:{source}:{ident}"


def parse_ref(text) -> tuple[str, str] | None:
    """("pco", "123") from "person:pco:123"; None for anything else."""
    if not isinstance(text, str):
        return None
    head, _, rest = text.partition(":")
    source, _, ident = rest.partition(":")
    return (source, ident) if head == REF and source and ident else None


def ref_for_user(user_id: int) -> str:
    return ref("relay", user_id)


def user_from_ref(text) -> int | None:
    """The Relay user a ref names: "person:relay:5" is 5 and "person:pco:123" is whoever is linked to it."""
    parsed = parse_ref(text)
    if parsed is None:
        return None
    source, ident = parsed
    if source == "relay":
        return int(ident) if ident.isdigit() and db.one("select id from users where id = ?", (int(ident),)) else None
    return user_for(source, ident)


# -- reading ------------------------------------------------------------------------------------------------
def user_for(provider: str, external_id) -> int | None:
    """The Relay user linked to this person in `provider`, or None."""
    row = db.one("select user_id from person_links where provider = ? and external_id = ?",
                 (provider, str(external_id)))
    return row["user_id"] if row else None


def links(provider: str) -> dict[str, int]:
    """Every link in `provider`, as {external id: Relay user id}."""
    return {r["external_id"]: r["user_id"]
            for r in db.query("select user_id, external_id from person_links where provider = ?", (provider,))}


def external_id(user_id: int, provider: str) -> str | None:
    """This user's id in `provider`, or None while they are not linked."""
    row = db.one("select external_id from person_links where user_id = ? and provider = ?", (user_id, provider))
    return row["external_id"] if row else None


def link_of(user_id: int, provider: str) -> dict | None:
    """The whole link: id, saved name, suggested name, how it was made and who confirmed it."""
    row = db.one("select external_id, name, suggested_name, matched_by, confirmed_by, at from person_links"
                 " where user_id = ? and provider = ?", (user_id, provider))
    if row is None:
        return None
    return {"id": row["external_id"], "name": row["name"], "ref": ref(provider, row["external_id"]),
            "suggested_name": row["suggested_name"] or "", "matched_by": row["matched_by"],
            "confirmed_by": row["confirmed_by"], "at": row["at"]}


def link_rows(provider: str) -> dict[int, dict]:
    """Every link in `provider`, as {Relay user id: link_of(...)} (one query, for a list of people)."""
    return {r["user_id"]: {"id": r["external_id"], "name": r["name"], "ref": ref(provider, r["external_id"]),
                           "suggested_name": r["suggested_name"] or "", "matched_by": r["matched_by"],
                           "confirmed_by": r["confirmed_by"], "at": r["at"]}
            for r in db.query("select user_id, external_id, name, suggested_name, matched_by, confirmed_by, at"
                              " from person_links where provider = ?", (provider,))}


def person(user_id: int) -> dict | None:
    """A user and every link they have: {"ref", "id", "name", "links": {provider: link_of(...)}}; None if no such user."""
    user = db.one("select id, name from users where id = ?", (user_id,))
    if user is None:
        return None
    mine = {r["provider"]: link_of(user_id, r["provider"])
            for r in db.query("select provider from person_links where user_id = ?", (user_id,))}
    return {"ref": ref_for_user(user_id), "id": user_id, "name": user["name"], "links": mine}


def dismissed(user_id: int, provider: str) -> str | None:
    """The spelling an admin refused for this user, if any."""
    row = db.one("select name from person_link_dismissed where user_id = ? and provider = ?", (user_id, provider))
    return row["name"] if row else None


def people(provider: str) -> list[dict]:
    """The people the provider module knows ({"id", "name"} each), or [] while that module is off."""
    from . import modules

    fn = modules.using("directory." + provider)
    if fn is None:
        return []
    return [{"id": str(p["id"]), "name": p["name"]} for p in fn() if p.get("id") and p.get("name")]


# -- writing ------------------------------------------------------------------------------------------------
def link(user_id: int, provider: str, external: str, name: str, *, matched_by: str = "name",
         confirmed_by: int | None = None, suggested_name: str | None = None) -> bool:
    """Link this user to the person `external` in `provider`. False, and nothing changed, when another user already
    holds that person (a link is never taken from someone). Replaces this user's earlier link in the provider."""
    held = user_for(provider, external)
    if held is not None and held != user_id:
        return False
    db.execute(
        "insert into person_links (user_id, provider, external_id, name, matched_by, confirmed_by, suggested_name, at)"
        " values (?, ?, ?, ?, ?, ?, ?, ?) on conflict (user_id, provider) do update set"
        " external_id = excluded.external_id, name = excluded.name, matched_by = excluded.matched_by,"
        " confirmed_by = excluded.confirmed_by, suggested_name = excluded.suggested_name, at = excluded.at",
        (user_id, provider, str(external), name, matched_by, confirmed_by, suggested_name, db.now()))
    return True


def unlink(user_id: int, provider: str) -> None:
    """Forget a link (a wrong one), remembering the spelling so it is not suggested again."""
    row = db.one("select name from person_links where user_id = ? and provider = ?", (user_id, provider))
    if row is None:
        return
    with db.transaction() as tx:
        tx.execute("delete from person_links where user_id = ? and provider = ?", (user_id, provider))
        tx.execute("insert into person_link_dismissed (user_id, provider, name) values (?, ?, ?)"
                   " on conflict (user_id, provider) do update set name = excluded.name",
                   (user_id, provider, row["name"]))


def suggestion(user_id: int, provider: str) -> str | None:
    row = db.one("select suggested_name from person_links where user_id = ? and provider = ?", (user_id, provider))
    return (row or {}).get("suggested_name") or None


def clear_suggestion(user_id: int, provider: str, *, dismiss: bool = False, confirmed_by: int | None = None) -> None:
    """The suggested spelling was accepted (the caller renamed the account) or, with `dismiss`, refused."""
    name = suggestion(user_id, provider)
    if name is None:
        return
    with db.transaction() as tx:
        tx.execute("update person_links set suggested_name = null, confirmed_by = coalesce(?, confirmed_by), at = ?"
                   " where user_id = ? and provider = ?", (confirmed_by, db.now(), user_id, provider))
        if dismiss:
            tx.execute("insert into person_link_dismissed (user_id, provider, name) values (?, ?, ?)"
                       " on conflict (user_id, provider) do update set name = excluded.name", (user_id, provider, name))
