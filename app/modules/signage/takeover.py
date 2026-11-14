"""Signage takeovers (#207): what is put over a screen's playlist, and Relay's alerts as a banner.

Three kinds, highest first:

    push    one image or video from the media library, sent to a screen, a group or all screens, to play once, for
            a set time, or until cleared.
    live    a Relay view in place of the playlist: the captions, the transcription or the service rundown.
    banner  Relay's alerts over whatever is playing. The playlist is not interrupted, so it is already where it
            was when the alert is cleared. Which screens show it is a setting (`alert_screens`, `alert_groups`).

Push and live live in the `signage_takeovers` table, one per target; a screen takes the most specific one that
applies (its own, then its group's, then all screens), a push before a live view. Alerts are read from Relay's own
alerts state, once a second at most, so core needs no hook into signage.

Other modules that want to switch a screen by hand-free means (ProPresenter slide-note tags, #208) call
`live_view_set`, `push_set` and `clear` below and name signage in their manifest's `uses`.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from ... import config, db, serving
from ...alerts import alerts
from . import live, sources, store

KINDS = ("push", "live")
MODES = ("once", "seconds", "cleared")
# view -> (label, path on the viewer port). /screen is the kiosk view: no chrome, nothing to press.
VIEWS = {
    "captions": ("Live captions (translation)", "/screen?stream=translation"),
    "transcription": ("Live captions (original language)", "/screen?stream=transcription"),
    "service": ("Live service rundown", "/screen?rundown=full"),
}
ALERT_MODES = ("all", "groups", "none")
SECONDS_MIN, SECONDS_MAX = 1, 86400
ONCE_PICTURE_S = 10                 # how long a picture shown "once" stays up
ONCE_FAILSAFE = timedelta(hours=1)  # a "once" push no screen reports done still ends
_ALERT_TTL_S = 0.5

_alert_cache: tuple[float, list[dict]] = (0.0, [])


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ rows

def rows() -> list[dict]:
    """The takeovers that have not run out, each with `media_name` for a push."""
    store.ready()
    out = db.query("select * from signage_takeovers where expires_at = '' or expires_at > ? order by id",
                   (db.now(),))
    for r in out:
        r["label"] = label(r)
    return out


def label(r: dict) -> str:
    if r["kind"] == "live":
        return VIEWS.get(r["view"], ("Live view", ""))[0]
    m = store.media_get(r["media_id"])
    return (m or {}).get("name") or "Deleted file"


def _check_target(target: str, target_id: int) -> int:
    target_id = 0 if target == "all" else int(target_id or 0)
    if target == "screen" and db.one("select id from signage_screens where id = ?", (target_id,)) is None:
        raise store.Invalid("That screen does not exist.", 404)
    if target == "group" and db.one("select id from signage_groups where id = ?", (target_id,)) is None:
        raise store.Invalid("That group does not exist.", 404)
    return target_id


def _save(kind: str, target: str, target_id: int, media_id: str, view: str, mode: str, seconds: int) -> dict:
    store.ready()
    if target not in ("screen", "group", "all"):
        raise store.Invalid("Choose a screen, a group or all screens.")
    target_id = _check_target(target, target_id)
    now = store.utc_now()
    expires = ""
    if kind == "push":
        if mode not in MODES:
            raise store.Invalid("A push plays once, for a set time, or until it is cleared.")
        if mode == "seconds":
            if not SECONDS_MIN <= seconds <= SECONDS_MAX:
                raise store.Invalid("Choose a time from 1 second to 24 hours.")
            expires = _iso(now + timedelta(seconds=seconds))
        elif mode == "once":
            seconds = 0
            expires = _iso(now + ONCE_FAILSAFE)
        else:
            seconds = 0
        m = store.media_get(media_id)
        if m is None:
            raise store.Invalid("That file is not in the media library.", 404)
    else:
        if view not in VIEWS:
            raise store.Invalid("Choose live captions or the service view.")
        mode, seconds, media_id = "cleared", 0, ""
    with db.transaction() as tx:
        tx.execute("delete from signage_takeovers where target = ? and target_id = ?", (target, target_id))
        tx.execute("insert into signage_takeovers (kind, target, target_id, media_id, view, mode, seconds, set_at,"
                   " expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (kind, target, target_id, media_id, view if kind == "live" else "", mode, seconds, db.now(),
                    expires))
        row = tx.one("select * from signage_takeovers where target = ? and target_id = ?", (target, target_id))
    live.bump()
    return row or {}


def push_set(target: str, target_id: int, media_id: str, mode: str = "cleared", seconds: int = 0) -> dict:
    """Send one image or video to a screen, a group or all screens."""
    return _save("push", target, target_id, str(media_id), "", mode, int(seconds or 0))


def live_view_set(target: str, target_id: int, view: str) -> dict:
    """Put live captions or the service view on a screen, a group or all screens until cleared."""
    return _save("live", target, target_id, "", view, "cleared", 0)


def clear(target: str, target_id: int) -> None:
    store.ready()
    target_id = 0 if target == "all" else int(target_id or 0)
    if not db.execute("delete from signage_takeovers where target = ? and target_id = ?", (target, target_id)):
        raise store.Invalid("Nothing is taken over there.", 404)
    live.bump()


def clear_all() -> int:
    """The "return to playlist" button: every push and live view ends. Returns how many were cleared."""
    store.ready()
    n = db.execute("delete from signage_takeovers")
    live.done_clear()
    live.bump()
    return int(n or 0)


def sweep() -> None:
    """Housekeeping: drop what ran out and what points at a screen or group that is gone."""
    store.ready()
    n = db.execute("delete from signage_takeovers where expires_at != '' and expires_at <= ?", (db.now(),))
    n += db.execute("delete from signage_takeovers where (target = 'screen' and target_id not in"
                    " (select id from signage_screens)) or (target = 'group' and target_id not in"
                    " (select id from signage_groups))")
    if n:
        live.bump()


# ------------------------------------------------------------------ what a screen shows

def _applies(r: dict, screen: dict) -> int | None:
    """How specifically takeover `r` names `screen` (0 its own, 1 its group, 2 all), None when it does not."""
    if r["target"] == "screen":
        return 0 if r["target_id"] == screen["id"] else None
    if r["target"] == "group":
        return 1 if screen.get("group_id") and r["target_id"] == screen["group_id"] else None
    return 2


def pick(screen: dict, taken: list[dict] | None = None) -> dict | None:
    """The takeover row `screen` shows now, or None. A push beats a live view; then the most specific target,
    then the newest. A "once" push the screen has already played is skipped."""
    best = None
    done = live.done_id(screen["slug"])
    for r in (taken if taken is not None else rows()):
        rank = _applies(r, screen)
        if rank is None or (r["kind"] == "push" and r["mode"] == "once" and r["id"] == done):
            continue
        key = (0 if r["kind"] == "push" else 1, rank, -r["id"])
        if best is None or key < best[0]:
            best = (key, r)
    return best[1] if best else None


def payload(screen: dict, taken: list[dict] | None = None) -> dict | None:
    """What the player gets for a takeover: a push to play or a live view to frame."""
    r = pick(screen, taken)
    if r is None:
        return None
    base = {"id": r["id"], "kind": r["kind"], "mode": r["mode"], "until": r["expires_at"], "from": r["target"]}
    if r["kind"] == "live":
        label_, path = VIEWS[r["view"]]
        port = serving.viewer_port(config.get()["port"])
        return {**base, "view": r["view"], "label": label_, "path": path, "port": port}
    m = store.media_get(r["media_id"])
    target = sources.resolve("media", r["media_id"]) if m else None
    if target is None:
        return None
    seconds = r["seconds"] or (ONCE_PICTURE_S if r["mode"] == "once" else 0)
    return {**base, "label": m["name"], "seconds": seconds,
            "item": {"id": -r["id"], "label": m["name"], "type": target["type"], "url": target["url"],
                     "fit": "fit", "transition": "cut", "duration_s": 0}}


# ------------------------------------------------------------------ alerts as a banner

def _active_alerts() -> list[dict]:
    global _alert_cache
    at, cached = _alert_cache
    if time.monotonic() - at < _ALERT_TTL_S:
        return cached
    try:
        buttons = alerts.state()["buttons"]
        out = [{"id": b["id"], "to": b.get("name", ""), "from": b.get("from", "")}
               for b in buttons if b.get("state") == "on"]
    except Exception:
        out = []
    _alert_cache = (time.monotonic(), out)
    return out


def alert_wanted(screen: dict, settings: dict) -> bool:
    mode = settings.get("alert_screens", "all")
    if mode == "all":
        return True
    return mode == "groups" and bool(screen.get("group_id")) and screen["group_id"] in (settings.get("alert_groups") or [])


def banner(screen: dict, settings: dict | None = None) -> dict | None:
    """The alert to show over `screen`, or None: {"text", "count"} for the first alert flashing now."""
    settings = settings if settings is not None else config.get().get("signage") or {}
    if not alert_wanted(screen, settings):
        return None
    flashing = _active_alerts()
    if not flashing:
        return None
    first = flashing[0]
    text = first["to"] or "Alert"
    if first["from"]:
        text += " (from " + first["from"] + ")"
    return {"text": text, "count": len(flashing)}


def alert_sig() -> tuple:
    """Changes whenever the set of alerts flashing now does (cheap: at most one look each half second)."""
    return tuple(a["id"] for a in _active_alerts())


def reset_cache() -> None:
    """Tests: look at the alerts again on next use."""
    global _alert_cache
    _alert_cache = (0.0, [])
