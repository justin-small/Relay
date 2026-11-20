"""Screens and places (#527, part of the shared-concepts epic #521): one record per physical display.

A screen is a browser (a TV, a tablet) or a hardware panel with one name, one place (Foyer, Stage, Kids) and an
address. Boards, signage, alerts and panels attach to it instead of keeping ids of their own, so the lobby TV is one
row that every page lists under one name. Tables: app/migrations/0008_screens_places.sql.

A ref names one: `screen:relay:<id>` and `place:relay:<id>`.

Each older feature knew a display by its own id. `adopt(key, ...)` turns that id into the screen, creating it the first
time, so an address that already works (`/signage/<slug>`, a board link, `/screen?alert=<name>`) keeps resolving to
the same screen. Keys are "board:<id>", "signage:<slug>", "panel:<id>" and "alert:<name>". A display that two features
call by the same name is one screen: adopting a key whose name another screen already has joins that screen, and
renaming a screen to another's name merges the two.

A screen shows one board or one signage playlist (`normal_*`), or another while a named schedule is open
(`during_*`). `showing()` decides which, and falls back to the board while Signage is off, so a switched-off module
never leaves a TV blank. Signage itself stays a module: it offers playlists through `modules.provide("signage.content")`
and core never imports it.
"""
from __future__ import annotations

import logging

from . import db
from .text import same_name

log = logging.getLogger("relay.screens")

SCREEN = "screen"
PLACE = "place"
NAME_MAX = 40
MAX_SCREENS = 500
MAX_PLACES = 100
KINDS = ("browser", "panel")
SHOWS = ("board", "signage")
BROWSER_GONE_S = 20          # a browser that has not asked for this long is not on screen
ONLINE_AFTER_S = 60          # a screen heard from within this long is "now"

REFUSALS = {"name": "Give it a name.", "name_taken": "Another place already has that name.",
            "no_screen": "That screen does not exist.", "no_place": "That place does not exist.",
            "too_many": "That is as many as Relay keeps.", "shows": "Choose a board or a signage playlist.",
            "panel_name": "A panel is renamed on the Panels page."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key


# -- refs ---------------------------------------------------------------------------------------------------
def ref(screen_id: int) -> str:
    return f"{SCREEN}:relay:{screen_id}"


def place_ref(place_id: int) -> str:
    return f"{PLACE}:relay:{place_id}"


def _parse(text, head: str) -> int | None:
    if not isinstance(text, str):
        return None
    parts = text.split(":")
    if len(parts) != 3 or parts[0] != head or parts[1] != "relay" or not parts[2].isdigit():
        return None
    return int(parts[2])


def screen_id(text) -> int | None:
    """The id in "screen:relay:5", or None for anything else."""
    return _parse(text, SCREEN)


def place_id(text) -> int | None:
    return _parse(text, PLACE)


# -- places -------------------------------------------------------------------------------------------------
def _name(raw, limit: int = NAME_MAX) -> str:
    return " ".join(str(raw or "").split())[:limit].strip()


def places() -> list[dict]:
    """Every place with the number of screens in it: {"id", "ref", "name", "screens"}."""
    return [{"id": r["id"], "ref": place_ref(r["id"]), "name": r["name"], "screens": r["n"]}
            for r in db.query("select p.id, p.name, (select count(*) from screens s where s.place_id = p.id) n"
                              " from places p order by p.name collate nocase")]


def place_by_name(name) -> dict | None:
    row = db.one("select id, name from places where name = ? collate nocase", (_name(name),))
    return {"id": row["id"], "ref": place_ref(row["id"]), "name": row["name"]} if row else None


def place_add(name) -> dict:
    """A new place. A name that is already one returns that place."""
    text = _name(name)
    if not text:
        raise Refused("name")
    found = place_by_name(text)
    if found:
        return found
    if db.one("select count(*) n from places")["n"] >= MAX_PLACES:
        raise Refused("too_many")
    db.execute("insert into places (name, created_at) values (?, ?)", (text, db.now()))
    return place_by_name(text)  # type: ignore[return-value]


def place_rename(pid: int, name) -> None:
    text = _name(name)
    if not text:
        raise Refused("name")
    other = db.one("select id from places where name = ? collate nocase", (text,))
    if other and other["id"] != pid:
        raise Refused("name_taken")
    if not db.execute("update places set name = ? where id = ?", (text, pid)):
        raise Refused("no_place")


def place_delete(pid: int) -> None:
    """Remove a place; its screens stay, with no place."""
    if not db.execute("delete from places where id = ?", (pid,)):
        raise Refused("no_place")


# -- screens ------------------------------------------------------------------------------------------------
def _screen(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {**row, "ref": ref(row["id"]), "place_ref": place_ref(row["place_id"]) if row["place_id"] else ""}


def get(sid: int) -> dict | None:
    return _screen(db.one("select * from screens where id = ?", (sid,)))


def by_name(name) -> dict | None:
    text = _name(name)
    return _screen(db.one("select * from screens where name = ? collate nocase", (text,))) if text else None


def by_key(key: str) -> dict | None:
    return _screen(db.one("select s.* from screens s join screen_keys k on k.screen_id = s.id where k.key = ?",
                          (key,)))


def in_place(pid: int) -> list[dict]:
    """The screens in a place: {"id", "name", "kind", "panel_id"} ("panel_id" is "" for a browser)."""
    out = []
    for r in db.query("select s.id, s.name, s.kind, (select substr(k.key, 7) from screen_keys k"
                      " where k.screen_id = s.id and k.key like 'panel:%' limit 1) panel_id"
                      " from screens s where s.place_id = ? order by s.name collate nocase", (pid,)):
        out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "panel_id": r["panel_id"] or ""})
    return out


def keys(sid: int) -> list[str]:
    return [r["key"] for r in db.query("select key from screen_keys where screen_id = ? order by key", (sid,))]


def names_by_key(prefix: str) -> dict[str, str]:
    """{id after the prefix: the screen's name} for every key that starts with `prefix` ("signage:", "board:")."""
    return {r["key"][len(prefix):]: r["name"] for r in db.query(
        "select k.key, s.name from screen_keys k join screens s on s.id = k.screen_id where k.key like ? escape '\\'",
        (prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",))}


def listing() -> list[dict]:
    """Every screen, with its place name and the keys it is known by, by place then name."""
    keyed: dict[int, list[str]] = {}
    for r in db.query("select key, screen_id from screen_keys order by key"):
        keyed.setdefault(r["screen_id"], []).append(r["key"])
    rows = db.query("select s.*, p.name place from screens s left join places p on p.id = s.place_id"
                    " order by (p.name is null), p.name collate nocase, s.name collate nocase")
    return [{**_screen(r), "place": r["place"] or "", "keys": keyed.get(r["id"], [])} for r in rows]  # type: ignore[dict-item]


def _free_name(tx, base: str, own: int | None = None) -> str:
    """`base`, or `base 2`, `base 3`... when another screen has it."""
    text, n = base[:NAME_MAX], 2
    while True:
        row = tx.one("select id from screens where name = ? collate nocase", (text,))
        if row is None or row["id"] == own:
            return text
        suffix = f" {n}"
        text, n = base[:NAME_MAX - len(suffix)] + suffix, n + 1


def adopt(key: str, *, name: str = "", kind: str = "browser", address: str = "", board: str = "",
          heard: bool = True) -> dict:
    """The screen this older id stands for, made the first time it is seen and marked as heard from now.

    `name` is the name the feature knew it by (a board screen's saved name, a signage screen's, a panel's, an alert
    name): a new key whose name another screen already has joins that screen. `board` is the board it is showing.
    `heard=False` only records the key (a signage screen an operator added, not yet opened): "last seen" stays."""
    if kind not in KINDS:
        raise ValueError(f"not a screen kind: {kind!r}")
    text = _name(name)
    now = db.now()
    with db.transaction() as tx:
        row = tx.one("select s.* from screens s join screen_keys k on k.screen_id = s.id where k.key = ?", (key,))
        if row is None:
            row = tx.one("select * from screens where name = ? collate nocase", (text,)) if text else None
            if row is None:
                if tx.one("select count(*) n from screens")["n"] >= MAX_SCREENS:
                    raise Refused("too_many")
                shown = text or "screen " + key.rsplit(":", 1)[-1][:6]
                tx.execute("insert into screens (name, kind, created_at) values (?, ?, ?)",
                           (_free_name(tx, shown), kind, now))
                row = tx.one("select * from screens where id = last_insert_rowid()")
            tx.execute("insert or ignore into screen_keys (key, screen_id) values (?, ?)", (key, row["id"]))
        tx.execute("update screens set last_seen = case when ? then ? else last_seen end,"
                   " address = coalesce(nullif(?, ''), address), last_board = coalesce(nullif(?, ''), last_board),"
                   " kind = case when ? = 'panel' then 'panel' else kind end where id = ?",
                   (heard, now, address[:64], board, kind, row["id"]))
        return _screen(tx.one("select * from screens where id = ?", (row["id"],)))  # type: ignore[return-value]


def touch(sid: int, *, address: str = "", board: str = "") -> None:
    """The screen was heard from just now, at this address, showing this board."""
    db.execute("update screens set last_seen = ?, address = coalesce(nullif(?, ''), address),"
               " last_board = coalesce(nullif(?, ''), last_board) where id = ?", (db.now(), address[:64], board, sid))


def rename(sid: int, name, *, panel_ok: bool = False) -> int:
    """Name a screen. A name another screen has merges this one into it: that screen keeps the name and takes this
    one's keys, and its id is returned (the same id when nothing merged)."""
    text = _name(name)
    if not text:
        raise Refused("name")
    with db.transaction() as tx:
        row = tx.one("select * from screens where id = ?", (sid,))
        if row is None:
            raise Refused("no_screen")
        if row["kind"] == "panel" and not panel_ok:
            raise Refused("panel_name")
        other = tx.one("select * from screens where name = ? collate nocase and id != ?", (text, sid))
        if other is None:
            tx.execute("update screens set name = ? where id = ?", (text, sid))
            return sid
        tx.execute("update screen_keys set screen_id = ? where screen_id = ?", (other["id"], sid))
        tx.execute("update screens set place_id = coalesce(place_id, ?), last_board = case when last_board = ''"
                   " then ? else last_board end where id = ?", (row["place_id"], row["last_board"], other["id"]))
        tx.execute("delete from screens where id = ?", (sid,))
        return other["id"]


def sync_panel_name(sid: int, name) -> None:
    """A panel's own name (Panels page) is the screen's name, kept even if that makes it differ from what an older
    screen was called."""
    text = _name(name)
    row = get(sid)
    if not text or row is None or same_name(row["name"], text):
        return
    with db.transaction() as tx:
        tx.execute("update screens set name = ? where id = ?", (_free_name(tx, text, sid), sid))


def set_place(sid: int, place) -> None:
    """Put the screen in a place (a place id), or in none with None."""
    if place is not None and db.one("select id from places where id = ?", (place,)) is None:
        raise Refused("no_place")
    if not db.execute("update screens set place_id = ? where id = ?", (place, sid)):
        raise Refused("no_screen")


def _clean_shows(kind, ref_) -> tuple[str, str]:
    if not kind:
        return "", ""
    text = str(ref_ or "").strip()[:80]
    if kind not in SHOWS or not text:
        raise Refused("shows")
    return kind, text


def set_shows(sid: int, normal: tuple | None = None, during: tuple | None = None, schedule: str | None = None) -> None:
    """What the screen shows: `normal` and `during` are (kind, ref) ("", "" for nothing), `schedule` is the ref of the
    named schedule that switches to `during`. A part left None is kept."""
    row = get(sid)
    if row is None:
        raise Refused("no_screen")
    n_kind, n_ref = _clean_shows(*normal) if normal is not None else (row["normal_kind"], row["normal_ref"])
    d_kind, d_ref = _clean_shows(*during) if during is not None else (row["during_kind"], row["during_ref"])
    sched = (row["during_schedule"] if schedule is None else str(schedule)[:80])
    if not d_kind:
        sched = ""
    db.execute("update screens set normal_kind = ?, normal_ref = ?, during_kind = ?, during_ref = ?,"
               " during_schedule = ? where id = ?", (n_kind, n_ref, d_kind, d_ref, sched, sid))


def set_hold(sid: int, kind: str = "", ref_: str = "") -> None:
    """Put a board or playlist on the screen by hand, over its schedule, until `set_hold(sid)` clears it."""
    kind, ref_ = _clean_shows(kind, ref_)
    if not db.execute("update screens set hold_kind = ?, hold_ref = ? where id = ?", (kind, ref_, sid)):
        raise Refused("no_screen")


def delete(sid: int) -> None:
    if not db.execute("delete from screens where id = ?", (sid,)):
        raise Refused("no_screen")


def forget_key(key: str) -> None:
    """An older feature dropped this id (a signage screen was deleted); the screen itself stays."""
    db.execute("delete from screen_keys where key = ?", (key,))


def move_key(old: str, new: str) -> None:
    """An older feature changed this id (a signage screen's address): the screen follows."""
    if old != new:
        db.execute("update or ignore screen_keys set key = ? where key = ?", (new, old))


def forget_board(board_id: str) -> None:
    """A board was deleted: no screen shows it any more."""
    db.execute("update screens set normal_kind = '', normal_ref = '' where normal_kind = 'board' and normal_ref = ?",
               (board_id,))
    db.execute("update screens set during_kind = '', during_ref = '', during_schedule = ''"
               " where during_kind = 'board' and during_ref = ?", (board_id,))
    db.execute("update screens set hold_kind = '', hold_ref = '' where hold_kind = 'board' and hold_ref = ?",
               (board_id,))
    db.execute("update screens set last_board = '' where last_board = ?", (board_id,))


# -- what a screen shows ------------------------------------------------------------------------------------
def showing(screen: dict, schedule_open, *, signage_on: bool) -> dict:
    """What the screen shows now: {"kind": "board"|"signage"|"", "ref", "why"}. `schedule_open(ref)` says whether a
    named schedule is open. While Signage is off a signage choice falls back to the board the screen last showed."""
    kind, ref_, why = screen["normal_kind"], screen["normal_ref"], "all the time"
    if screen["during_kind"] and screen["during_schedule"] and schedule_open(screen["during_schedule"]):
        kind, ref_, why = screen["during_kind"], screen["during_ref"], "while its schedule is open"
    if screen["hold_kind"]:
        kind, ref_, why = screen["hold_kind"], screen["hold_ref"], "held by hand"
    if kind == "signage" and not signage_on:
        board = screen["last_board"] or (screen["during_ref"] if screen["during_kind"] == "board" else "")
        if screen["normal_kind"] == "board" and not board:
            board = screen["normal_ref"]
        return {"kind": "board", "ref": board, "why": "Signage is off"} if board else {"kind": "", "ref": "", "why": ""}
    return {"kind": kind, "ref": ref_, "why": why if kind else ""}


def showing_now(screen: dict, *, signage_on: bool) -> dict:
    """`showing`, by the one Relay clock and the named schedules (Schedules page)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from . import config, schedules
    from .scheduler import plan_source

    zone = config.timezone()
    now = datetime.now(ZoneInfo(zone)).replace(tzinfo=None)

    def is_open(ref: str) -> bool:
        sched = schedules.find(config.get()["schedules"], ref)
        return bool(sched and sched["enabled"] and schedules.is_open_local(sched, now, zone, plan_source()))

    return showing(screen, is_open, signage_on=signage_on)


def online(screen: dict, now: float) -> bool:
    """Whether the screen was heard from lately (`now` is epoch seconds)."""
    from datetime import datetime, timezone

    try:
        seen_at = datetime.fromisoformat(screen["last_seen"].rstrip("Z")).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return False
    return 0 <= now - seen_at <= ONLINE_AFTER_S
