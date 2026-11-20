"""Crew resources per position (#233): files, links and embedded pages for a Planning Center team or for everyone.

A person sees the resources for the teams they serve on today plus the all-crew ones (the same rule as checklists),
so volunteers need no Planning Center login. Operators, and team leads for their own position, add and remove them.

Files are kept on the Relay host under random names; the type is decided from the file's first bytes (and, for an
Office file, from the entries inside it), never from its name; the size is capped. An embedded page is only shown
for a host on the allow-list in the crew settings; any other address is a plain link. Plan media copied by tag
rules belongs to the Planning Center module and is a separate piece of work.
"""
from __future__ import annotations

import io
import os
import re
import secrets
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ... import db, paths
from . import checklists, people, refs

MB = 1024 * 1024
FILE_LIMIT = 25 * MB
TITLE_MAX, URL_MAX = 80, 500
PER_POSITION_MAX, TOTAL_MAX = 100, 400
DEFAULT_EMBED_HOSTS = ["youtube-nocookie.com", "youtube.com", "docs.google.com", "drive.google.com", "player.vimeo.com"]
HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
STORED_RE = re.compile(r"^[0-9a-f]{32}\.(pdf|png|jpg|webp|gif|txt|docx|xlsx|pptx)$")
Invalid = people.Invalid

# ext -> (mime, shown in the browser?)
TYPES = {
    "pdf": ("application/pdf", True), "png": ("image/png", True), "jpg": ("image/jpeg", True),
    "webp": ("image/webp", True), "gif": ("image/gif", True), "txt": ("text/plain; charset=utf-8", True),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", False),
}
NOT_ALLOWED = "That file type is not allowed. Use PDF, PNG, JPEG, WebP, GIF, plain text, or Word, Excel or PowerPoint files."


def folder() -> Path:
    return paths.state_dir() / "crew" / "resources"


def classify(path: Path, head: bytes) -> str | None:
    """The extension for a stored file, from its content, or None when it is not an allowed type."""
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
        except (zipfile.BadZipFile, OSError):
            return None
        for prefix, ext in (("word/", "docx"), ("xl/", "xlsx"), ("ppt/", "pptx")):
            if "[Content_Types].xml" in names and any(n.startswith(prefix) for n in names):
                return ext
        return None
    if head and b"\x00" not in head:
        try:
            head.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(head) - 4:          # a multi-byte character cut by the 8 KB read is fine
                return None
        return "txt"
    return None


def clean_title(raw) -> str:
    if raw is not None and not isinstance(raw, str):
        raise Invalid("The title is text.")
    text = " ".join((raw or "").split())
    if not text:
        raise Invalid("Give the resource a title.")
    if len(text) > TITLE_MAX:
        raise Invalid(f"The title is at most {TITLE_MAX} characters.")
    return text


def clean_position(raw, raw_ref=None) -> tuple[str, str]:
    """(ref, name) for the team or position a resource is for; ("", "") is everyone serving."""
    return refs.clean_pick(raw_ref, raw, checklists.PICK, checklists.POSITION_MAX, "Position")


def clean_hosts(raw) -> list[str]:
    """The embed allow-list from settings: lower-case host names, anything else dropped."""
    if not isinstance(raw, list):
        return list(DEFAULT_EMBED_HOSTS)
    out: list[str] = []
    for h in raw:
        if isinstance(h, str):
            h = h.strip().lower().rstrip(".")
            if HOST_RE.match(h) and h not in out:
                out.append(h)
    return out[:50]


def host_allowed(host: str, allowed: list[str]) -> bool:
    return any(host == a or host.endswith("." + a) for a in allowed)


def clean_url(raw, kind: str, allowed: list[str]) -> str:
    """A web address for a link (http or https) or an embed (https, on the allow-list). A YouTube watch address is
    turned into its embeddable form."""
    if not isinstance(raw, str) or not raw.strip():
        raise Invalid("Give the web address.")
    text = raw.strip()
    if len(text) > URL_MAX or any(c.isspace() or ord(c) < 32 for c in text):
        raise Invalid("That is not a web address Relay can keep.")
    try:
        u = urlsplit(text)
        host = (u.hostname or "").lower().rstrip(".")
        u.port
    except ValueError:
        raise Invalid("That is not a web address Relay can keep.")
    if u.scheme not in ("http", "https") or not host or u.username or u.password or not HOST_RE.match(host):
        raise Invalid("Use a web address that starts with https:// and names a site.")
    if kind == "embed":
        if host in ("youtu.be", "www.youtu.be") and u.path.strip("/"):
            vid = u.path.strip("/").split("/")[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com") and u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
        else:
            vid = ""
        if vid:
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
                raise Invalid("That video address is not one Relay can embed.")
            return f"https://www.youtube-nocookie.com/embed/{vid}"
        if u.scheme != "https":
            raise Invalid("An embedded page must use https.")
        if not host_allowed(host, allowed):
            raise Invalid("Relay only embeds pages from the sites the operator allows. Add it as a link instead.")
    return text


# -- rows --------------------------------------------------------------------------------------------------------
def _row(r: dict) -> dict:
    f = refs.flags(r["position_ref"], r["position"])
    return {"id": r["id"], "position": f["name"], "position_ref": r["position_ref"], "pick": {"ref": r["position_ref"], "name": f["name"]}, "missing": f["missing"],
            "unmatched": f["unmatched"], "kind": r["kind"], "title": r["title"], "url": r["url"],
            "ext": r["ext"], "size": r["size"], "inline": bool(r["ext"] and TYPES[r["ext"]][1])}


def all_rows() -> list[dict]:
    people.ready()
    return [_row(r) for r in db.query(
        "select id, position, position_ref, kind, title, url, ext, size from crew_resources"
        " order by position = '' desc, lower(position), lower(title), id")]


def get(rid: int) -> dict | None:
    people.ready()
    return db.one("select * from crew_resources where id = ?", (rid,))


def mine(user_id: int, tracks) -> list[dict]:
    today = checklists.held(user_id, tracks)
    return [r for r in all_rows() if checklists.applies(r, today)]


def sees(user_id: int, rid: int, tracks) -> bool:
    return any(r["id"] == rid for r in mine(user_id, tracks))


def _room(position: str, position_ref: str = "") -> None:
    n = db.one("select count(*) as n from crew_resources")["n"]
    if n >= TOTAL_MAX:
        raise Invalid(f"There can be at most {TOTAL_MAX} resources.")
    if db.one("select count(*) as n from crew_resources where position_ref = ? and (? != '' or lower(position) = lower(?))",
              (position_ref, position_ref, position))["n"] >= PER_POSITION_MAX:
        raise Invalid(f"A position can have at most {PER_POSITION_MAX} resources.")


def add_url(user_id: int, kind: str, title, position, url, allowed: list[str], position_ref=None) -> int:
    if kind not in ("link", "embed"):
        raise Invalid("Choose a link or an embedded page.")
    t, (pr, p), u = clean_title(title), clean_position(position, position_ref), clean_url(url, kind, allowed)
    people.ready()
    _room(p, pr)
    return db.one("insert into crew_resources (position, position_ref, kind, title, url, created_by, created_at)"
                  " values (?, ?, ?, ?, ?, ?, ?) returning id", (p, pr, kind, t, u, user_id, db.now()))["id"]


async def add_file(user_id: int, title, position, chunks, position_ref=None) -> int:
    """Keep an upload arriving as an async iterator of byte chunks. Refused past the size cap as soon as it is
    passed, without keeping the rest; written beside its place and moved in once checked."""
    t, (pr, p) = clean_title(title), clean_position(position, position_ref)
    people.ready()
    _room(p, pr)
    base = folder()
    paths.make_private_dir(base)
    fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=base)
    size, head = 0, b""
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > FILE_LIMIT:
                    raise Invalid("That file is too large. Files can be up to 25 MB.", 413)
                if len(head) < 8192:
                    head += chunk[: 8192 - len(head)]
                out.write(chunk)
        ext = classify(Path(tmp), head) if size else None
        if ext is None:
            raise Invalid(NOT_ALLOWED, 415)
        stored = f"{secrets.token_hex(16)}.{ext}"
        os.replace(tmp, base / stored)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return db.one("insert into crew_resources (position, position_ref, kind, title, stored, ext, size, created_by, created_at)"
                  " values (?, ?, 'file', ?, ?, ?, ?, ?, ?) returning id", (p, pr, t, stored, ext, size, user_id, db.now()))["id"]


def change(rid: int, title, position, url, allowed: list[str], position_ref=None) -> None:
    row = get(rid)
    if row is None:
        raise Invalid("No such resource.", 404)
    t, (pr, p) = clean_title(title), clean_position(position, position_ref)
    u = clean_url(url, row["kind"], allowed) if row["kind"] != "file" else None
    db.execute("update crew_resources set title = ?, position = ?, position_ref = ?, url = ? where id = ?",
               (t, p, pr, u if row["kind"] != "file" else row["url"], rid))


def delete(rid: int) -> None:
    row = get(rid)
    if row is None:
        return
    db.execute("delete from crew_resources where id = ?", (rid,))
    if row["stored"] and STORED_RE.match(row["stored"]):
        try:
            (folder() / row["stored"]).unlink()
        except OSError:
            pass


def file_path(row: dict) -> Path | None:
    """The stored file for a row, or None when the name is not one of ours or the file is gone."""
    name = row.get("stored") or ""
    if not STORED_RE.match(name):
        return None
    base = os.path.realpath(folder())
    full = os.path.realpath(os.path.join(base, name))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return None
    return Path(full)
