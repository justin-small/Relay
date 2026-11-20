"""Signage data in relay.db (#205): media rows, playlists and items, screens and groups, and what a screen plays.

Every write bumps `live.version`, which the players' event streams watch, so a change reaches the screens with no
reload. Calls raise db.Unavailable while the database is down; routes turn that into a 503.
"""
from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ... import config, db, schedules
from ... import screens as core_screens
from ...text import same_name, slug as text_slug
from . import live, media, schedule, sources

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
        self.message = text
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


def tz_name() -> str:
    """The Relay time zone: signage has no zone of its own since #526."""
    return config.timezone()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def today(now: datetime | None = None) -> str:
    """Today's date in the signage time zone, which is what an item's start and end dates are read in."""
    return schedule.local(now or utc_now(), tz_name()).date().isoformat()


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

SCREEN_KEY = "signage:"


def _core_screen(row: dict) -> dict:
    """A signage screen is a core screen (app/screens.py, #527): the display's name is the core one, and a screen
    that has none yet is linked here, joining the core screen of the same name when there is one."""
    key = SCREEN_KEY + row["slug"]
    core = core_screens.by_key(key) or core_screens.adopt(key, name=row["name"], heard=False)
    return {**row, "sound": bool(row["sound"]), "name": core["name"], "core_id": core["id"]}


def screens() -> list[dict]:
    ready()
    rows = [_core_screen(r) for r in db.query("select * from signage_screens order by name collate nocase, id")]
    return sorted(rows, key=lambda r: (r["name"].lower(), r["id"]))


def screen_by_slug(slug: str) -> dict | None:
    if not SLUG_RE.match(slug or ""):
        return None
    ready()
    row = db.one("select * from signage_screens where slug = ?", (slug,))
    return _core_screen(row) if row else None


def player_path(slug: str) -> str:
    """The path a TV opens for this screen (on the signage port)."""
    return f"/signage/{slug}"


def player_address(host: str, slug: str) -> str:
    """The address a TV opens for this screen, on the signage port of `host`."""
    from ... import serving

    return serving.link("http", f"[{host}]" if ":" in host else host, config.get()["signage"]["port"],
                        player_path(slug))


def core_screen_slug(core_id: int) -> str:
    """The signage address of a core screen, or "" when none of its signage screens exists yet."""
    return next((k[len(SCREEN_KEY):] for k in core_screens.keys(core_id) if k.startswith(SCREEN_KEY)), "")


def ensure_screen(core: dict) -> dict:
    """The signage screen for a core screen, made (with an address from its name) when it has none: what the Screens
    page needs before it can set a playlist on a display."""
    ready()
    slug = core_screen_slug(core["id"])
    if slug:
        return screen_by_slug(slug) or {}
    taken = {r["slug"] for r in db.query("select slug from signage_screens")}
    new = text_slug(core["name"], taken, fallback="screen")
    db.execute("insert into signage_screens (slug, name, sound, created_at) values (?, ?, 0, ?)",
               (new, core["name"], db.now()))
    core_screens.adopt(SCREEN_KEY + new, name=core["name"], heard=False)
    row = db.one("select * from signage_screens where slug = ?", (new,))
    return _core_screen(row)


def set_default_playlist(core_id: int, playlist_id: int | None) -> None:
    """The playlist a core screen plays when nothing above it in the schedule picks one."""
    slug = core_screen_slug(core_id)
    if slug and db.execute("update signage_screens set playlist_id = ? where slug = ?",
                           (playlist_id, slug)):
        live.bump()


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
    old = db.one("select slug from signage_screens where id = ?", (sid,)) if sid is not None else None
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
    if old:
        core_screens.move_key(SCREEN_KEY + old["slug"], SCREEN_KEY + slug)
    core = core_screens.by_key(SCREEN_KEY + slug) or core_screens.adopt(SCREEN_KEY + slug, name=name, heard=False)
    if not same_name(core["name"], name):
        try:
            core_screens.rename(core["id"], name, panel_ok=True)
        except core_screens.Refused as exc:
            raise Invalid(core_screens.REFUSALS[exc.key]) from None
    live.bump()
    return _core_screen(row) if row else {}


def screen_delete(sid: int) -> None:
    ready()
    row = db.one("select slug from signage_screens where id = ?", (sid,))
    if not db.execute("delete from signage_screens where id = ?", (sid,)):
        raise Invalid("That screen does not exist.", 404)
    if row:
        core_screens.forget_key(SCREEN_KEY + row["slug"])
    live.bump()


# ------------------------------------------------------------------ what a screen plays

def default_playlist(screen: dict) -> tuple[int | None, str]:
    """(playlist id, why) for a screen with nothing else claiming it: its group's playlist when the group has one,
    else its own default."""
    if screen.get("group_id"):
        g = db.one("select playlist_id from signage_groups where id = ?", (screen["group_id"],))
        if g and g["playlist_id"]:
            return g["playlist_id"], "group"
    if screen.get("playlist_id"):
        return screen["playlist_id"], "screen"
    return None, ""


# ------------------------------------------------------------------ schedule slots and overrides (#206)

def slots() -> list[dict]:
    ready()
    rows = db.query("select * from signage_slots order by id")
    names = {schedules.ref(s["id"]): s["name"] for s in config.get()["schedules"]}
    for r in rows:
        r["summary"] = schedule.describe_slot(r, names)
    return rows


def schedule_users(ref: str) -> list[dict]:
    """The slots that pick named schedule `ref`, for the Schedules page's "used by"."""
    ready()
    return [{"where": "Signage", "what": f"Slot on {_target_label(r)}"} for r in slots() if r["schedule_ref"] == ref]


def _target_label(slot: dict) -> str:
    if slot["target"] == "all":
        return "all screens"
    if slot["target"] == "screen":
        row = db.one("select name from signage_screens where id = ?", (slot["target_id"],))
    else:
        row = db.one("select name from signage_groups where id = ?", (slot["target_id"],))
    return f"{slot['target']} {row['name']}" if row else slot["target"]


_ref_cache: dict[str, tuple[float, str, list[tuple[datetime, datetime]]]] = {}


def open_ref(ref: str, local_now: datetime) -> bool:
    """Whether named schedule `ref` is enabled and open at naive Relay-zone wall-clock `local_now`.

    Resolving a screen steps minute by minute across a week (`next_change`), so a schedule's windows are worked out
    once for a few weeks around now and kept for a minute, rather than on every step."""
    from ... import modules

    sched = schedules.find(config.get()["schedules"], ref)
    if sched is None or not sched["enabled"]:
        return False
    tz, now = config.timezone(), utc_now()
    stamp = json.dumps([sched, tz], sort_keys=True)
    held = _ref_cache.get(ref)
    if held is None or held[1] != stamp or now.timestamp() - held[0] > 60:
        span = timedelta(days=schedule.HORIZON_DAYS + 3)
        wins = schedules.windows([sched], now - span, now + span, tz, modules.using("schedules.plan_times"))
        held = _ref_cache[ref] = (now.timestamp(), stamp, [(w.start, w.stop) for w in wins])
    at = schedules.localize(local_now.date(), local_now.time().replace(second=0, microsecond=0), ZoneInfo(tz))
    return any(a <= at < b for a, b in held[2])


def _check_target(row: dict) -> None:
    if row["target"] == "screen" and db.one("select id from signage_screens where id = ?", (row["target_id"],)) is None:
        raise Invalid("That screen does not exist.", 404)
    if row["target"] == "group" and db.one("select id from signage_groups where id = ?", (row["target_id"],)) is None:
        raise Invalid("That group does not exist.", 404)
    if db.one("select id from signage_playlists where id = ?", (row["playlist_id"],)) is None:
        raise Invalid("That playlist does not exist.", 404)


def slot_save(sid: int | None, raw: dict) -> dict:
    ready()
    try:
        row = schedule.clean_slot(raw)
    except schedule.Refused as exc:
        raise Invalid(str(exc)) from None
    _check_target(row)
    if row["schedule_ref"] and schedules.find(config.get()["schedules"], row["schedule_ref"]) is None:
        raise Invalid("That schedule does not exist.", 404)
    cols = ("target", "target_id", "playlist_id", "days", "start_time", "end_time", "start_date", "end_date",
            "schedule_ref")
    with db.transaction() as tx:
        if sid is None:
            tx.execute(f"insert into signage_slots ({', '.join(cols)}) values ({', '.join('?' * len(cols))})",
                       tuple(row[c] for c in cols))
            out = tx.one("select * from signage_slots where id = last_insert_rowid()")
        else:
            if not tx.execute(f"update signage_slots set {', '.join(c + ' = ?' for c in cols)} where id = ?",
                              (*[row[c] for c in cols], sid)):
                raise Invalid("That slot does not exist.", 404)
            out = tx.one("select * from signage_slots where id = ?", (sid,))
    live.bump()
    return out or {}


def slot_delete(sid: int) -> None:
    ready()
    if not db.execute("delete from signage_slots where id = ?", (sid,)):
        raise Invalid("That slot does not exist.", 404)
    live.bump()


def overrides() -> list[dict]:
    ready()
    return db.query("select * from signage_overrides where expires_at = '' or expires_at > ?", (db.now(),))


def _all_screens_for(target: str, target_id: int) -> list[dict]:
    return [s for s in screens() if target == "all" or (target == "screen" and s["id"] == target_id)
            or (target == "group" and s["group_id"] == target_id)]


def override_set(target: str, target_id: int, playlist_id: int, until: str) -> dict:
    """Put `playlist_id` on a screen, a group or all screens. `until` is "next_change" (ends when the schedule next
    changes what any of those screens plays) or "cleared" (stays until an operator clears it)."""
    ready()
    if target not in schedule.TARGETS:
        raise Invalid("Choose a screen, a group or all screens.")
    target_id = 0 if target == "all" else int(target_id or 0)
    _check_target({"target": target, "target_id": target_id, "playlist_id": playlist_id})
    now = utc_now()
    expires = ""
    if until == "next_change":
        rows, tz = slots(), tz_name()
        targets = _all_screens_for(target, target_id)

        def key(local_now):
            return tuple(((schedule.matching(rows, s, local_now, open_ref) or [{"id": 0}])[0]["id"]) for s in targets)

        at = schedule.next_change(key, now, tz) if targets else None
        expires = at.strftime("%Y-%m-%dT%H:%M:%SZ") if at else ""
    with db.transaction() as tx:
        tx.execute("delete from signage_overrides where target = ? and target_id = ?", (target, target_id))
        tx.execute("insert into signage_overrides (target, target_id, playlist_id, set_at, expires_at)"
                   " values (?, ?, ?, ?, ?)", (target, target_id, playlist_id, db.now(), expires))
    live.bump()
    return {"target": target, "target_id": target_id, "playlist_id": playlist_id, "expires_at": expires}


def override_clear(target: str, target_id: int) -> None:
    ready()
    target_id = 0 if target == "all" else int(target_id or 0)
    if not db.execute("delete from signage_overrides where target = ? and target_id = ?", (target, target_id)):
        raise Invalid("There is no override to clear there.", 404)
    live.bump()


def sweep_overrides() -> None:
    """Delete overrides that ran out (housekeeping; resolving already ignores them)."""
    ready()
    if db.execute("delete from signage_overrides where expires_at != '' and expires_at <= ?", (db.now(),)):
        live.bump()


def resolve(screen: dict, now: datetime | None = None, _slots=None, _overrides=None) -> dict:
    """What a screen plays and why: {"playlist_id", "layer", "why", "until"} (playlist_id None when nothing is
    assigned). layer is takeover, tag, override, schedule, group or screen; "until" is when an override ends."""
    now = now or utc_now()
    got = schedule.layer_result(screen, now)
    if got:
        return {**got, "until": ""}
    iso = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    best = None
    for o in (_overrides if _overrides is not None else overrides()):
        if o["expires_at"] and o["expires_at"] <= iso:
            continue
        rank = {"screen": 0, "group": 1, "all": 2}[o["target"]]
        applies = (o["target"] == "all" or (o["target"] == "screen" and o["target_id"] == screen["id"])
                   or (o["target"] == "group" and screen.get("group_id") == o["target_id"]))
        if applies and (best is None or rank < best[0]):
            best = (rank, o)
    if best:
        o = best[1]
        what = {"screen": "this screen", "group": "its group", "all": "all screens"}[o["target"]]
        return {"playlist_id": o["playlist_id"], "layer": "override", "until": o["expires_at"],
                "why": f"Put on by hand for {what}"}
    rows = _slots if _slots is not None else slots()
    hit = schedule.matching(rows, screen, schedule.local(now, tz_name()), open_ref)
    if hit:
        more = len(hit) - 1
        return {"playlist_id": hit[0]["playlist_id"], "layer": "schedule", "until": "",
                "why": "Schedule: " + hit[0]["summary"] + (f" ({more} other slot{'s' if more > 1 else ''} also match)"
                                                          if more else "")}
    pid, why = default_playlist(screen)
    label = {"group": "No schedule matches now; its group's playlist",
             "screen": "No schedule matches now; the screen's default playlist", "": "Nothing assigned"}[why]
    return {"playlist_id": pid, "layer": why or "none", "why": label, "until": ""}


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


def player_state(screen: dict, day: str | None = None, now: datetime | None = None) -> dict:
    """What the player at /signage/<slug> needs: the screen, its playlist and each item it can play today."""
    now = now or utc_now()
    day = day or today(now)
    plan = resolve(screen, now)
    pid = plan["playlist_id"]
    playlist = playlist_get(pid) if pid else None
    items = playable_items(pid, day) if playlist else []
    return {
        "screen": {"slug": screen["slug"], "name": screen["name"], "sound": bool(screen["sound"])},
        "playlist": {"id": playlist["id"], "name": playlist["name"], "from": plan["layer"]} if playlist else None,
        "items": items,
        "today": day,
    }
