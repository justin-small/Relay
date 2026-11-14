"""The web pages and YouTube links an operator saved for signage (#210), and what Relay can tell about each.

Links live in this module's own settings (config.json "signage_web"). A link's `ref` in a playlist is its id, so
editing a link's address changes every playlist that uses it.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...realtime import _SSL

log = logging.getLogger("relay.signage_web")

MAX_LINKS = 200
TIMEOUT_S = 6
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be",
                 "www.youtube-nocookie.com", "youtube-nocookie.com"}
EMBED_HOST = "https://www.youtube-nocookie.com"
_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
_LIST = re.compile(r"[A-Za-z0-9_-]{10,64}")


class Invalid(ValueError):
    """A link Relay refuses. `message` is a fixed sentence for the admin page."""

    def __init__(self, text: str):
        super().__init__(text)
        self.message = text


def _clean_url(raw: Any) -> str:
    url = str(raw or "").strip()
    if url and "://" not in url and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:(?!\d)", url):
        url = "https://" + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or len(url) > 500 or any(c.isspace() for c in url):
        raise Invalid("Enter a web address starting with http:// or https://.")
    if parts.username or parts.password:
        raise Invalid("A web address with a user name or password in it can't be used.")
    return url


def parse_youtube(url: str) -> dict | None:
    """{"video": id, "list": id} for a YouTube watch, short, embed or playlist link, else None."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    query = urllib.parse.parse_qs(parts.query)
    video = None
    path = [p for p in parts.path.split("/") if p]
    if host == "youtu.be" and path:
        video = path[0]
    elif path and path[0] in ("embed", "shorts", "live", "v") and len(path) > 1:
        video = path[1]
    elif path and path[0] == "watch":
        video = (query.get("v") or [""])[0]
    plist = (query.get("list") or [""])[0]
    video = video if video and _VIDEO.fullmatch(video) else ""
    plist = plist if plist and _LIST.fullmatch(plist) and not plist.startswith("RD") else ""
    if path and path[0] == "playlist":
        video = ""
    if not video and not plist:
        return None
    return {"video": video, "list": plist}


def clean_link(raw: Any, existing_ids: set[str] = frozenset()) -> dict:
    if not isinstance(raw, dict):
        raise Invalid("A link must be an object.")
    url = _clean_url(raw.get("url"))
    yt = parse_youtube(url)
    if urllib.parse.urlsplit(url).hostname.lower() in YOUTUBE_HOSTS and yt is None:
        raise Invalid("That YouTube address names no video or playlist.")
    lid = str(raw.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{8}", lid):
        lid = secrets.token_hex(4)
        while lid in existing_ids:
            lid = secrets.token_hex(4)
    name = " ".join(str(raw.get("name") or "").split())[:100]
    return {"id": lid, "kind": "youtube" if yt else "web", "url": url, "name": name,
            "check": raw.get("check") if isinstance(raw.get("check"), dict) else {}}


def clean_stored(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out, seen = [], set()
    for item in (raw.get("links") if isinstance(raw.get("links"), list) else [])[:MAX_LINKS]:
        try:
            link = clean_link(item, seen)
        except Invalid:
            continue
        if link["id"] in seen:
            continue
        seen.add(link["id"])
        link["name"] = link["name"] or link["url"]
        c = link["check"]
        link["check"] = {"state": c.get("state") if c.get("state") in ("ok", "blocked", "unsure", "gone", "offline")
                         else "", "note": str(c.get("note") or "")[:200], "at": str(c.get("at") or "")[:30]}
        out.append(link)
    return {"links": out}


# ---------------------------------------------------------------- what the player is told

def embed_url(link: dict, sound: bool) -> str:
    yt = parse_youtube(link["url"]) or {}
    q = {"autoplay": "1", "controls": "0", "rel": "0", "playsinline": "1", "enablejsapi": "1", "iv_load_policy": "3",
         "modestbranding": "1"}
    if not sound:
        q["mute"] = "1"
    if yt.get("video"):
        path = f"/embed/{yt['video']}"
        if yt.get("list"):
            q["list"] = yt["list"]
    else:
        path = "/embed/videoseries"
        q["list"] = yt["list"]
        q["loop"] = "1"
    return EMBED_HOST + path + "?" + urllib.parse.urlencode(q)


def resolve_web(link: dict) -> dict:
    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(link["url"]))
    return {"type": "page", "url": link["url"], "probe": origin + "/"}


def resolve_youtube(link: dict) -> dict:
    yt = parse_youtube(link["url"]) or {}
    out = {"type": "page", "url": embed_url(link, False), "url_sound": embed_url(link, True),
           "probe": EMBED_HOST + "/favicon.ico"}
    if yt.get("video") and not yt.get("list"):
        # A single video can end the item when it ends (a duration of 0); the player listens for YouTube saying so.
        out["post_message"] = {"event": "listening", "id": 1, "channel": "widget"}
        out["end_when"] = [{"event": "onStateChange", "info": 0}, {"event": "infoDelivery", "info.playerState": 0}]
    return out


# ---------------------------------------------------------------- checks (admin asks; never at play time)

def _fetch(url: str, headers: dict | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": "Relay-signage-check", **(headers or {})})
    kw = {"context": _SSL} if url.startswith("https:") else {}
    return urllib.request.urlopen(req, timeout=TIMEOUT_S, **kw)


def frame_verdict(headers) -> tuple[str, str]:
    """("ok" | "blocked" | "unsure", sentence) from a response's framing headers."""
    xfo = (headers.get("X-Frame-Options") or "").strip().lower()
    if xfo:
        return "blocked", "This site refuses to be shown inside another page (X-Frame-Options). Screens will stay blank."
    csp = " ".join(v for k, v in headers.items() if k.lower() == "content-security-policy")
    m = re.search(r"frame-ancestors([^;]*)", csp, re.I)
    if m:
        sources = m.group(1).split()
        if "*" in sources:
            return "ok", "The site allows being shown in a frame."
        if not sources or "'none'" in sources or sources == ["'self'"]:
            return "blocked", "This site refuses to be shown inside another page (frame-ancestors). Screens will stay blank."
        return "unsure", "The site only allows certain sites to frame it. It may stay blank on screens."
    return "ok", "The site allows being shown in a frame."


def check_web(url: str) -> dict:
    try:
        with _fetch(url) as resp:
            state, note = frame_verdict(resp.headers)
    except urllib.error.HTTPError as exc:
        state, note = frame_verdict(exc.headers)
        if state == "ok":
            note = f"The site answered with an error ({exc.code}), but allows framing."
    except Exception:
        return {"state": "offline", "note": "Relay couldn't reach this address to check it."}
    return {"state": state, "note": note}


def check_youtube(url: str) -> dict:
    """oEmbed answers 200 for a public embeddable video or playlist, 401/403 when embedding is off, 404 if gone."""
    api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    try:
        with _fetch(api) as resp:
            data = json.loads(resp.read(200_000))
        return {"state": "ok", "note": "YouTube allows this to be embedded.", "title": str(data.get("title") or "")}
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"state": "blocked", "note": "The owner has turned embedding off, so screens can't play it."}
        if exc.code in (400, 404):
            return {"state": "gone", "note": "YouTube has no such video or playlist, or it is private."}
        return {"state": "unsure", "note": f"YouTube answered with an error ({exc.code})."}
    except Exception:
        return {"state": "offline", "note": "Relay couldn't reach YouTube to check this."}


def check(link: dict) -> dict:
    return check_youtube(link["url"]) if link["kind"] == "youtube" else check_web(link["url"])
