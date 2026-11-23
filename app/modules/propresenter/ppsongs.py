"""Songs into ProPresenter (#497): build `.pro` files (ppfile.py) and hand them to the Relay Helper on the ProPresenter
computer, which opens them with ProPresenter so it imports them. ProPresenter's network API cannot create a song, so
this is the one way in; the helper (native/relay-helper, core's helperhub.py) is what makes it work when Relay runs on
another machine. Looks, timers, messages, props and playlists do not need the helper: they use the network API."""
from __future__ import annotations

import base64
from typing import Any

from ... import helperhub
from . import ppfile, ppsongparse

MAX_SONGS = 50
MAX_SLIDES = 300
MAX_TEXT = 4000
MAX_FILE = 16 * 1024 * 1024


class SongError(ValueError):
    """The request cannot be turned into songs. `message` is a fixed sentence for the operator."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _text(v: Any, limit: int = MAX_TEXT) -> str:
    return str(v or "")[:limit]


def _rgb(v: Any) -> tuple[float, float, float] | None:
    if v is None or v == "":
        return None
    try:
        r, g, b = (float(x) for x in v)
    except (TypeError, ValueError):
        raise SongError("A colour is three numbers from 0 to 1.") from None
    if not all(0.0 <= x <= 1.0 for x in (r, g, b)):
        raise SongError("A colour is three numbers from 0 to 1.")
    return r, g, b


def _num(v: Any, what: str, hi: float = 86400.0) -> float:
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        raise SongError(f"{what} must be a number.") from None
    if not 0 <= n <= hi:
        raise SongError(f"{what} is out of range.")
    return n


def _slide(d: Any) -> ppfile.Slide:
    if isinstance(d, str):
        return ppfile.Slide(text=_text(d))
    if not isinstance(d, dict):
        raise SongError("Each slide is text or an object.")
    actions = []
    for a in (d.get("actions") or [])[:20]:
        if not isinstance(a, dict):
            raise SongError("Each slide action is an object.")
        try:
            actions.append(ppfile.SlideAction(kind=str(a.get("kind") or ""), target=_text(a.get("target"), 200),
                                              uuid=_text(a.get("uuid"), 64), what=_text(a.get("what"), 30),
                                              amount=_num(a.get("amount"), "A timer amount", 86400),
                                              screen=_text(a.get("screen"), 200), screen_uuid=_text(a.get("screen_uuid"), 64)))
        except ppfile.FileError as exc:
            raise SongError(exc.message) from None
    # Chords typed inline as `[G]` in the text become positions in the text without them.
    text, inline = ppsongparse.split_chords(_text(d.get("text")))
    chords = [(pos, name[:20]) for pos, name in inline[:200]]
    for c in (d.get("chords") or [])[:200]:
        try:
            chords.append((int(c[0]), _text(c[1], 20)))
        except (TypeError, ValueError, IndexError, KeyError):
            raise SongError("A chord is a position and a name.") from None
    return ppfile.Slide(
        text=text, translation=_text(d.get("translation")), label=_text(d.get("label"), 80),
        label_colour=_rgb(d.get("label_colour")), notes=_text(d.get("notes")), chords=chords,
        hot_key=_text(d.get("hot_key"), 2), advance_after=_num(d.get("advance_after"), "The advance time"),
        completion=_text(d.get("completion"), 10), transition=_num(d.get("transition"), "The transition", 30),
        actions=actions, enabled=d.get("enabled") is not False)


def song_from_payload(d: Any) -> ppfile.Song:
    """A song the operator or a feed (Planning Center) described, as ppfile.Song. Limits keep one request from
    building a file ProPresenter chokes on; anything else Relay cannot set is refused with a fixed sentence."""
    if not isinstance(d, dict):
        raise SongError("A song is an object with a name and sections.")
    sections = []
    total = 0
    for s in (d.get("sections") or [])[:100]:
        if not isinstance(s, dict):
            raise SongError("Each section is an object with a name and slides.")
        slides = [_slide(x) for x in (s.get("slides") or [])]
        total += len(slides)
        if total > MAX_SLIDES:
            raise SongError(f"A song may have at most {MAX_SLIDES} slides.")
        sections.append(ppfile.Section(name=_text(s.get("name"), 80) or "Slides", slides=slides, colour=_rgb(s.get("colour")),
                                       hot_key=_text(s.get("hot_key"), 2)))
    arrangements = []
    for a in (d.get("arrangements") or [])[:20]:
        if not isinstance(a, dict) or not isinstance(a.get("sections"), list):
            raise SongError("An arrangement has a name and a list of section names.")
        arrangements.append((_text(a.get("name"), 80), [_text(x, 80) for x in a["sections"][:200]]))
    try:
        return ppfile.Song(
            name=_text(d.get("name"), 200), sections=sections, arrangements=arrangements,
            selected_arrangement=_text(d.get("selected_arrangement"), 80), ccli_number=int(_num(d.get("ccli_number"), "The CCLI number", 1e9)),
            author=_text(d.get("author"), 300), publisher=_text(d.get("publisher"), 300),
            copyright_year=int(_num(d.get("copyright_year"), "The copyright year", 3000)), album=_text(d.get("album"), 300),
            artist_credits=_text(d.get("artist_credits"), 300), show_ccli=bool(d.get("show_ccli")), notes=_text(d.get("notes")),
            category=_text(d.get("category"), 80), music_key=_text(d.get("music_key"), 6), original_key=_text(d.get("original_key"), 6),
            background=_rgb(d.get("background")), bible_reference=_text(d.get("bible_reference"), 100),
            slide_show_seconds=_num(d.get("slide_show_seconds"), "The slideshow time"), transition=_num(d.get("transition"), "The transition", 30),
            announcements=bool(d.get("announcements")))
    except ppfile.FileError:
        raise SongError("That song has a setting Relay cannot write.") from None


def build_files(songs: list[dict]) -> list[dict]:
    """`.pro` files for `songs`, as the helper's open_files wants them: [{name, data_b64}]. Nothing is sent."""
    if not isinstance(songs, list) or not songs:
        raise SongError("Send at least one song.")
    if len(songs) > MAX_SONGS:
        raise SongError(f"At most {MAX_SONGS} songs at a time.")
    files, seen = [], set()
    for raw in songs:
        song = song_from_payload(raw)
        try:
            data = ppfile.build_song(song)
        except ppfile.FileError as exc:
            raise SongError(exc.message) from None
        if len(data) > MAX_FILE:
            raise SongError("That song is too large to send.")
        name = ppfile_name(song.name, seen)
        files.append({"name": name, "data_b64": base64.b64encode(data).decode("ascii")})
    return files


def ppfile_name(title: str, seen: set[str]) -> str:
    """A file name for a song: its title with anything unsafe taken out, and a number added if two share one."""
    base = "".join(c for c in title.strip() if c.isalnum() or c in " -_().,'&") or "Song"
    base = base.strip(" .")[:120] or "Song"
    name, n = base, 2
    while name.lower() in seen:
        name, n = f"{base} {n}", n + 1
    seen.add(name.lower())
    return name + ".pro"


def helper_state() -> dict:
    """Which helper would carry songs to ProPresenter right now, for the admin page."""
    conn = helperhub.hub.find("propresenter", "open_files")
    return {"connected": conn is not None, "helper": conn.name if conn else "",
            "paired": len(helperhub.hub.stored())}


async def send(songs: list[dict]) -> dict:
    """Build the songs and have the helper open them in ProPresenter. Raises SongError (a bad request) or
    helperhub.HelperError (no helper, or it could not)."""
    files = build_files(songs)
    out = await helperhub.hub.call("propresenter", "open_files", {"files": files})
    return {"sent": len(files), "files": [f["name"] for f in files], "opened": (out or {}).get("opened", len(files))}


def parse_text(d: Any) -> dict:
    """A pasted lyric sheet, or a PDF (`pdf_b64`), read into sections of slides for the operator to review. Nothing is kept."""
    if not isinstance(d, dict):
        raise SongError("Send the text or a PDF to read.")
    text = d.get("text")
    if d.get("pdf_b64"):
        try:
            raw = base64.b64decode(str(d["pdf_b64"]), validate=True)
        except ValueError:
            raise SongError("The file is not valid base64.") from None
        if not raw or len(raw) > MAX_FILE:
            raise SongError("The file is empty or too large.")
        try:
            text = ppsongparse.pdf_text(raw)
        except ppsongparse.ParseError as exc:
            raise SongError(exc.message) from None
    try:
        lines = int(d.get("lines", 4))
    except (TypeError, ValueError):
        raise SongError("Lines per slide is a number.") from None
    if not 0 <= lines <= 12:
        raise SongError("Lines per slide is from 0 to 12.")
    flag = lambda k: d.get(k) is not False   # noqa: E731
    try:
        out = ppsongparse.parse_text(text, lines, flag("blank_splits"), flag("headers"), flag("translation"), flag("chords"))
    except ppsongparse.ParseError as exc:
        raise SongError(exc.message) from None
    return {**out, "text": text if d.get("pdf_b64") else None}


def split_song(d: Any) -> dict:
    """The sections cut again into slides of `lines` lines. Nothing is kept."""
    if not isinstance(d, dict) or not isinstance(d.get("sections"), list):
        raise SongError("Send the sections to split.")
    try:
        lines = int(d.get("lines", 4))
    except (TypeError, ValueError):
        raise SongError("Lines per slide is a number.") from None
    if not 0 <= lines <= 12:
        raise SongError("Lines per slide is from 0 to 12.")
    secs = [s for s in d["sections"][:100] if isinstance(s, dict)]
    return {"sections": ppsongparse.resplit(secs, lines)}


def read_song(data_b64: Any, inline: bool = False) -> dict:
    """What is in a `.pro` file (lyrics by section, arrangements, CCLI and the rest), for showing or reusing it."""
    try:
        raw = base64.b64decode(str(data_b64 or ""), validate=True)
    except ValueError:
        raise SongError("The file is not valid base64.") from None
    if not raw or len(raw) > MAX_FILE:
        raise SongError("The file is empty or too large.")
    try:
        song = ppfile.parse_song(raw)
    except ppfile.FileError as exc:
        raise SongError(exc.message) from None
    return song_to_dict(song, inline)


def song_to_dict(song: ppfile.Song, inline: bool = False) -> dict:
    from dataclasses import asdict

    d = asdict(song)
    if inline:   # the songs page edits chords as `[G]` in the text
        for sec in d["sections"]:
            for sl in sec["slides"]:
                sl["text"] = ppsongparse.with_chords(sl["text"], sl.get("chords") or [])
                sl.pop("chords", None)
    d["arrangements"] = [{"name": n, "sections": s} for n, s in song.arrangements]
    return d
