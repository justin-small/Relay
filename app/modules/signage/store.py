"""Signage data in relay.db (#205): media rows, playlists and items, screens and groups, and what a screen plays.

Every write bumps `live.version`, which the players' event streams watch, so a change reaches the screens with no
reload. Calls raise db.Unavailable while the database is down; routes turn that into a 503.
"""
from __future__ import annotations

import re
import secrets
import threading
from datetime import date, datetime
from pathlib import Path

from ... import db
from . import live, media, sources

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_MAX = 80
ITEMS_MAX = 500

_ready = False
_lock = threading.Lock()


class Invalid(Exception):
    """A request signage refuses. The text is a fixed sentence for the admin page."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.status = status


def ready() -> None:
    """Create signage's tables on first use (raises db.Unavailable while the database is down)."""
    global _ready
    if _ready:
        return
    with _lock:
        if not _ready:
            db.migrate("signage", MIGRATIONS)
            _ready = True


def forget() -> None:
    """Tests: check the tables again on next use."""
    global _ready
    _ready = False


def _name(v, what: str) -> str:
    text = " ".join(str(v or "").split())
    if not text:
        raise Invalid(f"Give the {what} a name.")
    return text[:NAME_MAX]


def _date(v) -> str:
    text = str(v or "").strip()
    if not text:
        return ""
    if not DATE_RE.match(text):
        raise Invalid("Dates are written YYYY-MM-DD.")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise Invalid("That date does not exist.") from None
    return text


def today() -> str:
    return datetime.now().date().isoformat()


# ------------------------------------------------------------------ media

def media_list() -> list[dict]:
    ready()
    rows = db.query("select * from signage_media order by created_at desc, name")
    used = {r["ref"]: r["n"] for r in db.query(
        "select ref, count(*) as n from signage_items where source = 'media' group by ref")}
    for r in rows:
        r["has_thumb"] = bool(r["has_thumb"])
        r["file"] = media.file_name(r["sha256"], r["ext"])
        r["uses"] = used.get(r["id"], 0)
    return rows


def media_get(mid: str) -> dict | None:
    ready()
    return db.one("select * from signage_media where id = ?", (mid,))


def media_add(name: str, sha: str, ext: str, size: int, width: int = 0, height: int = 0,
              duration_s: float = 0.0) -> dict:
    ready()
    kind, mime = media.TYPES[ext]
    mid = secrets.token_hex(8)
    db.execute(
        "insert into signage_media (id, name, kind, mime, ext, size, sha256, width, height, duration_s, has_thumb,"
        " created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mid, _name(name, "file"), kind, mime, ext, int(size), sha, max(0, int(width)), max(0, int(height)),
         max(0.0, float(duration_s)), 1 if media.thumb_path(sha) else 0, db.now()))
    live.bump()
    return media_get(mid) or {}


def media_rename(mid: str, name: str) -> None:
    ready()
    if not db.execute("update signage_media set name = ? where id = ?", (_name(name, "file"), mid)):
        raise Invalid("That file is not in the library.", 404)
    live.bump()


def media_thumb(mid: str, data: bytes) -> None:
    row = media_get(mid)
    if row is None:
        raise Invalid("That file is not in the library.", 404)
    media.save_thumb(row["sha256"], data)
    db.execute("update signage_media set has_thumb = 1 where sha256 = ?", (row["sha256"],))


def media_delete(mid: str) -> None:
    row = media_get(mid)
    if row is None:
        raise Invalid("That file is not in the library.", 404)
    names = [r["name"] for r in db.query(
        "select distinct p.name from signage_items i join signage_playlists p on p.id = i.playlist_id"
        " where i.source = 'media' and i.ref = ? order by p.name", (mid,))]
    if names:
        raise Invalid("That file is in use in " + ", ".join(names) + ". Take it out of those playlists first.", 409)
    db.execute("delete from signage_media where id = ?", (mid,))
    used = {r["sha256"] for r in db.query("select sha256 from signage_media where sha256 = ?", (row["sha256"],))}
    media.remove_if_unused(row["sha256"], row["ext"], used)
    live.bump()


def media_hashes() -> set[str]:
    ready()
    return {r["sha256"] for r in db.query("select sha256 from signage_media")}


def _media_source_resolve(ref: str) -> dict | None:
    row = db.one("select kind, sha256, ext from signage_media where id = ?", (ref,))
    if row is None:
        return None
    return {"type": row["kind"], "url": "/signage/_/media/" + media.file_name(row["sha256"], row["ext"])}


def _media_source_describe(ref: str) -> dict:
    row = db.one("select id, name, has_thumb from signage_media where id = ?", (ref,))
    if row is None:
        return {"name": "Deleted file", "thumb": "", "missing": True}
    return {"name": row["name"],
            "thumb": f"/api/admin/signage/media/{row['id']}/thumb" if row["has_thumb"] else ""}


MEDIA_SOURCE = sources.Source(kind="media", label="Uploaded image or video", resolve=_media_source_resolve,
                              describe=_media_source_describe)


# ------------------------------------------------------------------ playlists

def _clean_item(raw: dict, position: int) -> tuple:
    if not isinstance(raw, dict):
        raise Invalid("Each item must be an object.")
    source = str(raw.get("source") or "media")
    ref = str(raw.get("ref") or "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", source) or not ref or len(ref) > 500:
        raise Invalid("An item has no file or source.")
    if source == "media" and media_get(ref) is None:
        raise Invalid("An item names a file that is not in the library.")
    try:
        duration = float(raw.get("duration_s", 10) or 0)
    except (TypeError, ValueError):
        raise Invalid("A duration must be a number of seconds.") from None
    if not 0 <= duration <= 86400:
        raise Invalid("A duration must be between 0 seconds and a day.")
    start, end = _date(raw.get("start_date")), _date(raw.get("end_date"))
    if start and end and end < start:
        raise Invalid("An item ends before it starts.")
    transition = raw.get("transition") or "fade"
    fit = raw.get("fit") or "fit"
    if transition not in ("cut", "fade") or fit not in ("fit", "fill"):
        raise Invalid("Transition is cut or fade, and fit is fit or fill.")
    return position, source, ref, duration, start, end, transition, fit


def playlists() -> list[dict]:
    ready()
    lists = db.query("select * from signage_playlists order by name collate nocase, id")
    items = db.query("select * from signage_items order by playlist_id, position")
    by: dict[int, list] = {}
    for it in items:
        it.update(sources.describe(it["source"], it["ref"]))
        src = sources.get(it["source"])
        it["source_label"] = src.label if src else it["source"]
        by.setdefault(it["playlist_id"], []).append(it)
    for p in lists:
        p["items"] = by.get(p["id"], [])
    return lists


def playlist_get(pid: int) -> dict | None:
    ready()
    return db.one("select * from signage_playlists where id = ?", (pid,))


def playlist_create(name: str) -> dict:
    ready()
    now = db.now()
    with db.transaction() as tx:
        tx.execute("insert into signage_playlists (name, created_at, updated_at) values (?, ?, ?)",
                   (_name(name, "playlist"), now, now))
        row = tx.one("select * from signage_playlists where id = last_insert_rowid()")
    live.bump()
    return row or {}


def playlist_rename(pid: int, name: str) -> None:
    ready()
    if not db.execute("update signage_playlists set name = ?, updated_at = ? where id = ?",
                      (_name(name, "playlist"), db.now(), pid)):
        raise Invalid("That playlist does not exist.", 404)
    live.bump()


def playlist_duplicate(pid: int, name: str = "") -> dict:
    src = playlist_get(pid)
    if src is None:
        raise Invalid("That playlist does not exist.", 404)
    now = db.now()
    with db.transaction() as tx:
        tx.execute("insert into signage_playlists (name, created_at, updated_at) values (?, ?, ?)",
                   (_name(name or f"{src['name']} (copy)", "playlist"), now, now))
        new = tx.one("select * from signage_playlists where id = last_insert_rowid()")
        tx.execute("insert into signage_items (playlist_id, position, source, ref, duration_s, start_date, end_date,"
                   " transition, fit) select ?, position, source, ref, duration_s, start_date, end_date, transition,"
                   " fit from signage_items where playlist_id = ? order by position", (new["id"], pid))
    live.bump()
    return new or {}


def playlist_delete(pid: int) -> None:
    ready()
    if not db.execute("delete from signage_playlists where id = ?", (pid,)):
        raise Invalid("That playlist does not exist.", 404)
    live.bump()


def playlist_set_items(pid: int, items: list) -> None:
    """Replace a playlist's items with `items`, in order (add, reorder and remove are all this one call)."""
    if playlist_get(pid) is None:
        raise Invalid("That playlist does not exist.", 404)
    if not isinstance(items, list) or len(items) > ITEMS_MAX:
        raise Invalid(f"A playlist holds up to {ITEMS_MAX} items.")
    rows = [_clean_item(raw, i) for i, raw in enumerate(items)]
    with db.transaction() as tx:
        tx.execute("delete from signage_items where playlist_id = ?", (pid,))
        for r in rows:
            tx.execute("insert into signage_items (playlist_id, position, source, ref, duration_s, start_date,"
                       " end_date, transition, fit) values (?, ?, ?, ?, ?, ?, ?, ?, ?)", (pid, *r))
        tx.execute("update signage_playlists set updated_at = ? where id = ?", (db.now(), pid))
    live.bump()


def _playlist_ref(v) -> int | None:
    if v in (None, "", 0):
        return None
    try:
        pid = int(v)
    except (TypeError, ValueError):
        raise Invalid("Choose a playlist.") from None
    if playlist_get(pid) is None:
        raise Invalid("That playlist does not exist.", 404)
    return pid


# ------------------------------------------------------------------ groups

def groups() -> list[dict]:
    ready()
    return db.query("select * from signage_groups order by name collate nocase, id")


def group_save(gid: int | None, raw: dict) -> dict:
    ready()
    name = _name(raw.get("name"), "group")
    pid = _playlist_ref(raw.get("playlist_id"))
    with db.transaction() as tx:
        if gid is None:
            tx.execute("insert into signage_groups (name, playlist_id) values (?, ?)", (name, pid))
            row = tx.one("select * from signage_groups where id = last_insert_rowid()")
        else:
            if not tx.execute("update signage_groups set name = ?, playlist_id = ? where id = ?", (name, pid, gid)):
                raise Invalid("That group does not exist.", 404)
            row = tx.one("select * from signage_groups where id = ?", (gid,))
    live.bump()
    return row or {}


def group_delete(gid: int) -> None:
    ready()
    if not db.execute("delete from signage_groups where id = ?", (gid,)):
        raise Invalid("That group does not exist.", 404)
    live.bump()


# ------------------------------------------------------------------ screens

def screens() -> list[dict]:
    ready()
    rows = db.query("select * from signage_screens order by name collate nocase, id")
    for r in rows:
        r["sound"] = bool(r["sound"])
    return rows


def screen_by_slug(slug: str) -> dict | None:
    if not SLUG_RE.match(slug or ""):
        return None
    ready()
    row = db.one("select * from signage_screens where slug = ?", (slug,))
    if row:
        row["sound"] = bool(row["sound"])
    return row


def screen_save(sid: int | None, raw: dict) -> dict:
    ready()
    name = _name(raw.get("name"), "screen")
    slug = str(raw.get("slug") or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise Invalid("The address name uses lowercase letters, numbers and dashes, like lobby-left.")
    pid = _playlist_ref(raw.get("playlist_id"))
    gid = raw.get("group_id") or None
    if gid is not None:
        try:
            gid = int(gid)
        except (TypeError, ValueError):
            raise Invalid("Choose a group.") from None
        if db.one("select id from signage_groups where id = ?", (gid,)) is None:
            raise Invalid("That group does not exist.", 404)
    sound = 1 if raw.get("sound") else 0
    clash = db.one("select id from signage_screens where slug = ?", (slug,))
    if clash and clash["id"] != sid:
        raise Invalid("Another screen already uses that address.", 409)
    with db.transaction() as tx:
        if sid is None:
            tx.execute("insert into signage_screens (slug, name, sound, playlist_id, group_id, created_at)"
                       " values (?, ?, ?, ?, ?, ?)", (slug, name, sound, pid, gid, db.now()))
            row = tx.one("select * from signage_screens where id = last_insert_rowid()")
        else:
            if not tx.execute("update signage_screens set slug = ?, name = ?, sound = ?, playlist_id = ?,"
                              " group_id = ? where id = ?", (slug, name, sound, pid, gid, sid)):
                raise Invalid("That screen does not exist.", 404)
            row = tx.one("select * from signage_screens where id = ?", (sid,))
    live.bump()
    if row:
        row["sound"] = bool(row["sound"])
    return row or {}


def screen_delete(sid: int) -> None:
    ready()
    if not db.execute("delete from signage_screens where id = ?", (sid,)):
        raise Invalid("That screen does not exist.", 404)
    live.bump()


# ------------------------------------------------------------------ what a screen plays

def playing_playlist(screen: dict) -> tuple[int | None, str]:
    """(playlist id, why) for a screen: its group's playlist when the group has one, else its own default."""
    if screen.get("group_id"):
        g = db.one("select playlist_id from signage_groups where id = ?", (screen["group_id"],))
        if g and g["playlist_id"]:
            return g["playlist_id"], "group"
    if screen.get("playlist_id"):
        return screen["playlist_id"], "screen"
    return None, ""


def in_dates(item: dict, day: str) -> bool:
    return (not item["start_date"] or item["start_date"] <= day) and (not item["end_date"] or day <= item["end_date"])


def playable_items(pid: int, day: str) -> list[dict]:
    """The items of playlist `pid` that play on `day`, as the player wants them. Items whose source is off or
    whose file is gone are left out."""
    out = []
    for it in db.query("select * from signage_items where playlist_id = ? order by position", (pid,)):
        if not in_dates(it, day):
            continue
        target = sources.resolve(it["source"], it["ref"])
        if target is None:
            continue
        out.append({"id": it["id"], **target, "duration_s": it["duration_s"], "transition": it["transition"],
                    "fit": it["fit"], "start_date": it["start_date"], "end_date": it["end_date"]})
    return out


def player_state(screen: dict, day: str | None = None) -> dict:
    """What the player at /signage/<slug> needs: the screen, its playlist and each item it can play today."""
    day = day or today()
    pid, why = playing_playlist(screen)
    playlist = playlist_get(pid) if pid else None
    items = playable_items(pid, day) if playlist else []
    return {
        "screen": {"slug": screen["slug"], "name": screen["name"], "sound": bool(screen["sound"])},
        "playlist": {"id": playlist["id"], "name": playlist["name"], "from": why} if playlist else None,
        "items": items,
        "today": day,
    }
