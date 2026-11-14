"""Uploaded media files on disk (#205).

Files live in the state folder at signage/media/<sha256>.<ext>, named by their content so a URL never changes
meaning and the player can cache it for good. Thumbnails, made by the admin browser at upload, are
signage/thumbs/<sha256>.webp. Each file is also copied once into backups/signage-media/, beside the nightly
relay.db snapshots that hold the playlists, and a copy is pruned once neither the database nor any kept snapshot
names it.

The type is decided from the file's first bytes, never from its name or the browser's Content-Type.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from ... import db, paths

log = logging.getLogger("relay.signage")

MB = 1024 * 1024
IMAGE_LIMIT = 25 * MB
VIDEO_LIMIT = 500 * MB
THUMB_LIMIT = 512 * 1024
LOW_DISK = 2 * 1024 * MB

# ext -> (kind, mime)
TYPES = {
    "jpg": ("image", "image/jpeg"),
    "png": ("image", "image/png"),
    "webp": ("image", "image/webp"),
    "gif": ("image", "image/gif"),
    "mp4": ("video", "video/mp4"),
    "webm": ("video", "video/webm"),
}
FILE_RE = re.compile(r"^([0-9a-f]{64})\.(jpg|png|webp|gif|mp4|webm)$")


class Refused(Exception):
    """An upload Relay will not keep. The text is a fixed sentence for the admin page."""

    def __init__(self, text: str, status: int = 400):
        super().__init__(text)
        self.message = text
        self.status = status


def sniff(head: bytes) -> str | None:
    """The extension for a file starting with `head`, or None when it is not a type signage plays."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    return None


def root() -> Path:
    return paths.state_dir() / "signage"


def media_dir() -> Path:
    return root() / "media"


def thumbs_dir() -> Path:
    return root() / "thumbs"


def mirror_dir() -> Path:
    return db.backups_dir() / "signage-media"


def _ensure(p: Path) -> Path:
    paths.make_private_dir(p)
    return p


def file_name(sha: str, ext: str) -> str:
    return f"{sha}.{ext}"


def _inside(folder: Path, name: str) -> Path | None:
    """`folder/name` when it is a file directly inside `folder` once links and dots are resolved, else None."""
    base = os.path.realpath(folder)
    full = os.path.realpath(os.path.join(base, name))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        return None
    return Path(full)


def file_path(name: str) -> Path | None:
    """The stored file for a public file name, or None when the name is not one of ours or the file is gone."""
    if not FILE_RE.match(name):
        return None
    return _inside(media_dir(), name)


def thumb_path(sha: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        return None
    return _inside(thumbs_dir(), f"{sha}.webp")


async def receive(chunks) -> tuple[str, str, int]:
    """Store an upload arriving as an async iterator of byte chunks. Returns (sha256, ext, size).

    Refuses a file of a type signage does not play, an image over 25 MB and a video over 500 MB, as soon as it
    passes the limit, without keeping the rest. The file is written beside its final place and moved in once whole.
    """
    folder = _ensure(media_dir())
    fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=folder)
    digest = hashlib.sha256()
    size = 0
    head = b""
    ext: str | None = None
    limit = VIDEO_LIMIT
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                if ext is None:
                    head += chunk[: 32 - len(head)] if len(head) < 32 else b""
                    if len(head) >= 12:
                        ext = sniff(head)
                        if ext is None:
                            raise Refused("That file is not an image or video signage can play. Use JPEG, PNG, "
                                          "WebP or GIF images, or MP4 or WebM video.", 415)
                        limit = IMAGE_LIMIT if TYPES[ext][0] == "image" else VIDEO_LIMIT
                size += len(chunk)
                if size > limit:
                    raise Refused("That file is too large. Images can be up to 25 MB and videos up to 500 MB.",
                                  413)
                digest.update(chunk)
                out.write(chunk)
        if ext is None:
            ext = sniff(head)
            if ext is None or size == 0:
                raise Refused("That file is not an image or video signage can play. Use JPEG, PNG, WebP or GIF "
                              "images, or MP4 or WebM video.", 415)
        sha = digest.hexdigest()
        final = folder / file_name(sha, ext)
        if final.exists():
            os.unlink(tmp)
        else:
            os.replace(tmp, final)
        mirror(sha, ext)
        return sha, ext, size
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_thumb(sha: str, data: bytes) -> None:
    if not data or len(data) > THUMB_LIMIT:
        raise Refused("The thumbnail is too large.", 413)
    if sniff(data[:32]) not in ("webp", "jpg", "png"):
        raise Refused("The thumbnail is not an image.", 415)
    folder = _ensure(thumbs_dir())
    fd, tmp = tempfile.mkstemp(prefix=".thumb-", dir=folder)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    os.replace(tmp, folder / f"{sha}.webp")


def mirror(sha: str, ext: str) -> None:
    """Copy a stored file into the backups folder once. A failure is logged; the upload still stands."""
    src = media_dir() / file_name(sha, ext)
    try:
        folder = _ensure(mirror_dir())
        dst = folder / src.name
        if dst.exists():
            return
        tmp = folder / f".{src.name}.part"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
    except OSError as e:
        log.warning("signage: could not copy %s into the backups folder: %s", src.name, e)


def remove_if_unused(sha: str, ext: str, used: set[str]) -> None:
    """Delete the stored file and thumbnail when no media row names `sha` any more (the backup copy stays until
    `prune_mirror` finds no kept snapshot naming it)."""
    if sha in used:
        return
    for p in (media_dir() / file_name(sha, ext), thumbs_dir() / f"{sha}.webp"):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.warning("signage: could not delete %s: %s", p.name, e)


def _snapshot_hashes(p: Path) -> set[str] | None:
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            return {r[0] for r in conn.execute("select sha256 from signage_media")}
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return set()
        log.warning("signage: could not read backup %s: %s", p.name, e)
        return None
    except sqlite3.Error as e:
        log.warning("signage: could not read backup %s: %s", p.name, e)
        return None


def prune_mirror(current: set[str]) -> int:
    """Delete backup copies that neither the database (`current`) nor any kept relay-*.db snapshot names.
    Keeps everything when a snapshot cannot be read. Returns how many were deleted."""
    folder = mirror_dir()
    if not folder.is_dir():
        return 0
    keep = set(current)
    for snap in sorted(db.backups_dir().glob("relay-*.db")):
        hashes = _snapshot_hashes(snap)
        if hashes is None:
            return 0
        keep |= hashes
    gone = 0
    for p in folder.iterdir():
        m = FILE_RE.match(p.name)
        if m and m.group(1) not in keep:
            try:
                p.unlink()
                gone += 1
            except OSError as e:
                log.warning("signage: could not prune backup copy %s: %s", p.name, e)
    return gone


def mirror_missing(rows: list[dict]) -> None:
    """Copy any stored file the backups folder lacks (an install from before mirroring, a failed copy)."""
    for r in rows:
        if not (mirror_dir() / file_name(r["sha256"], r["ext"])).exists() and \
                (media_dir() / file_name(r["sha256"], r["ext"])).is_file():
            mirror(r["sha256"], r["ext"])


def disk() -> dict:
    """Bytes used by stored media and thumbnails, and free space on that disk."""
    used = 0
    for d in (media_dir(), thumbs_dir()):
        if d.is_dir():
            used += sum(p.stat().st_size for p in d.iterdir() if p.is_file() and not p.name.startswith("."))
    try:
        target = media_dir() if media_dir().is_dir() else paths.state_dir()
        free = shutil.disk_usage(target).free
    except OSError:
        free = -1
    return {"used": used, "free": free, "low": 0 <= free < LOW_DISK}
