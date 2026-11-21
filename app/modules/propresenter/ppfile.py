"""Read and write ProPresenter's own files (#497): songs (`.pro`), later playlists, from the community Pro7 definitions.

The message classes are built at run time from `pro7/pro7.desc` (a descriptor set compiled from greyshirtguy's MIT
`.proto` files), so no generated code is tied to a protobuf version. Nothing here touches the disk by itself: bytes in,
bytes out; `ppdeliver.py` decides where a written file goes. Writing ProPresenter's files is unsupported by Renewed
Vision, so a song is built by cloning one slide ProPresenter itself exported (`pro7/lyric_slide.bin`).
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).parent / "pro7"

# ProPresenter's group colours (red, green, blue), by the names a song's sections usually carry.
SECTION_COLOURS = {
    "verse": (0.0, 0.46, 0.89), "chorus": (0.89, 0.15, 0.25), "bridge": (0.55, 0.15, 0.82),
    "pre-chorus": (0.95, 0.55, 0.1), "prechorus": (0.95, 0.55, 0.1), "tag": (0.1, 0.65, 0.35),
    "intro": (0.5, 0.5, 0.5), "outro": (0.5, 0.5, 0.5), "ending": (0.5, 0.5, 0.5), "interlude": (0.5, 0.5, 0.5),
}


class FileError(ValueError):
    """A ProPresenter file Relay cannot read, or a song it cannot write. The message is safe to show."""


@lru_cache(maxsize=1)
def _classes() -> Any:
    """The message classes: `rv.data.Presentation` and friends, from the descriptor set."""
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    fds = descriptor_pb2.FileDescriptorSet.FromString((_DIR / "pro7.desc").read_bytes())
    pool = descriptor_pool.DescriptorPool()
    for f in fds.file:
        pool.Add(f)

    def cls(name: str) -> Any:
        return message_factory.GetMessageClass(pool.FindMessageTypeByName(name))

    class _C:
        Presentation = cls("rv.data.Presentation")
        Cue = cls("rv.data.Cue")
        UUID = cls("rv.data.UUID")

    return _C


def _new_uuid() -> str:
    return str(uuid.uuid4()).upper()


# ---------- slide text: ProPresenter keeps it as RTF ----------

def rtf_to_text(rtf: bytes | str) -> str:
    """The plain text of a slide's RTF: groups and control words dropped, `\\par`/`\\` as line breaks, `\\'xx` and
    `\\uN` decoded."""
    s = rtf.decode("latin-1") if isinstance(rtf, bytes) else rtf
    out: list[str] = []
    depth = 0
    skip_at: int | None = None
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "{":
            depth += 1
            if s.startswith("{\\*", i) or re.match(r"\{\\(fonttbl|colortbl|stylesheet|info)", s[i:i + 12]):
                skip_at = skip_at if skip_at is not None else depth
        elif ch == "}":
            if skip_at is not None and depth == skip_at:
                skip_at = None
            depth -= 1
        elif ch == "\\":
            m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", s[i:])
            if m:
                word, arg = m.group(1), m.group(2)
                if skip_at is None:
                    if word in ("par", "line"):
                        out.append("\n")
                    elif word == "tab":
                        out.append("\t")
                    elif word == "u" and arg is not None:
                        code = int(arg)
                        out.append(chr(code if code >= 0 else code + 65536))
                        # a `\uN` is followed by one fallback character that is not text
                        if i + m.end() < n:
                            nxt = s[i + m.end()]
                            if nxt not in "\\{}":
                                i += 1
                i += m.end()
                continue
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt == "'" and i + 3 < n + 1:
                if skip_at is None:
                    out.append(bytes([int(s[i + 2:i + 4], 16)]).decode("cp1252", "replace"))
                i += 4
                continue
            if nxt in ("\n", "\r"):
                if skip_at is None:
                    out.append("\n")
            elif nxt in "\\{}" and skip_at is None:
                out.append(nxt)
            i += 2
            continue
        elif ch in "\r\n":
            pass
        elif skip_at is None:
            out.append(ch)
        i += 1
    text = "".join(out)
    return "\n".join(line.strip() for line in text.strip("\n ").split("\n")).strip()


def _rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            code = ord(ch)
            out.append("\\u%d?" % (code if code < 32768 else code - 65536))
    return "".join(out)


def text_to_rtf(template: bytes, text: str) -> bytes:
    """`template`'s RTF with its text replaced: the formatting stays ProPresenter's own, the lines are ours."""
    marker = b"\\strokec0 "
    head = template.split(marker)[0] + marker if marker in template else template
    body = "\\\n".join(_rtf_escape(line) for line in text.split("\n"))
    return head + body.encode("latin-1") + b"}"


# ---------- reading ----------

@dataclass
class Section:
    name: str
    slides: list[str] = field(default_factory=list)
    colour: tuple[float, float, float] | None = None


@dataclass
class Song:
    name: str = ""
    uuid: str = ""
    sections: list[Section] = field(default_factory=list)
    arrangements: list[tuple[str, list[str]]] = field(default_factory=list)  # (name, section names in order)
    ccli_number: int = 0
    author: str = ""
    publisher: str = ""
    copyright_year: int = 0
    notes: str = ""

    def lyrics(self) -> str:
        return "\n\n".join(f"{s.name}\n" + "\n\n".join(s.slides) for s in self.sections)


def _slide_text(cue: Any) -> str:
    for action in cue.actions:
        if not action.HasField("slide"):
            continue
        for el in action.slide.presentation.base_slide.elements:
            if el.element.HasField("text") and el.element.text.rtf_data:
                return rtf_to_text(el.element.text.rtf_data)
    return ""


def parse_song(data: bytes) -> Song:
    """A `.pro` file as a Song: sections with their slide texts, arrangements and CCLI. A file that is not a
    presentation raises FileError."""
    C = _classes()
    pres = C.Presentation()
    try:
        pres.ParseFromString(data)
    except Exception as exc:  # protobuf raises its own DecodeError
        raise FileError("This is not a ProPresenter song file.") from exc
    if not pres.name and not pres.cue_groups and not pres.cues:
        raise FileError("This is not a ProPresenter song file.")
    cues = {c.uuid.string: c for c in pres.cues}
    song = Song(name=pres.name, uuid=pres.uuid.string, notes=pres.notes)
    by_id: dict[str, str] = {}
    for cg in pres.cue_groups:
        name = cg.group.name or "Slides"
        sec = Section(name=name)
        if cg.group.HasField("color"):
            c = cg.group.color
            sec.colour = (round(c.red, 3), round(c.green, 3), round(c.blue, 3))
        for ident in cg.cue_identifiers:
            cue = cues.get(ident.string)
            if cue is not None:
                sec.slides.append(_slide_text(cue))
        song.sections.append(sec)
        by_id[cg.group.uuid.string] = name
    for arr in pres.arrangements:
        song.arrangements.append((arr.name, [by_id.get(g.string, "") for g in arr.group_identifiers]))
    cc = pres.ccli
    song.ccli_number = cc.song_number
    song.author, song.publisher, song.copyright_year = cc.author, cc.publisher, cc.copyright_year
    return song


# ---------- writing ----------

def _fresh_uuids(msg: Any) -> None:
    """Give every `uuid` in a cloned message a new value, so two slides never share an identity."""
    for fd, val in msg.ListFields():
        if fd.message_type is None:
            continue
        subs = [val] if hasattr(val, "ListFields") else list(val)
        for sub in subs:
            if fd.name in ("uuid", "id") and fd.message_type.full_name == "rv.data.UUID":
                sub.string = _new_uuid()
            else:
                _fresh_uuids(sub)


def _blank_cue(text: str) -> Any:
    C = _classes()
    cue = C.Cue()
    cue.ParseFromString((_DIR / "lyric_slide.bin").read_bytes())
    _fresh_uuids(cue)
    for action in cue.actions:
        if action.HasField("slide"):
            for el in action.slide.presentation.base_slide.elements:
                if el.element.HasField("text"):
                    el.element.text.rtf_data = text_to_rtf(el.element.text.rtf_data or _DEFAULT_RTF, text)
    return cue


_DEFAULT_RTF = (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n{\\fonttbl\\f0\\fswiss\\fcharset0 Arial-Black;}\n"
                b"{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\qc\n\\f0\\b\\fs120 \\cf1 \\strokec0 ")


def build_song(song: Song) -> bytes:
    """A `.pro` file for `song` that ProPresenter opens. Needs a name and at least one slide."""
    if not song.name.strip():
        raise FileError("A song needs a name.")
    if not any(sec.slides for sec in song.sections):
        raise FileError("A song needs at least one slide.")
    C = _classes()
    pres = C.Presentation()
    pres.uuid.string = song.uuid or _new_uuid()
    pres.name = song.name.strip()
    app = pres.application_info
    app.platform = 1
    app.application = 1
    app.application_version.major_version = 22
    pres.chord_chart.platform = 1
    group_ids: dict[str, str] = {}
    for sec in song.sections:
        cg = pres.cue_groups.add()
        cg.group.uuid.string = group_ids.setdefault(sec.name, _new_uuid())
        cg.group.name = sec.name
        rgb = sec.colour or SECTION_COLOURS.get(re.sub(r"[^a-z-]", "", sec.name.lower()), (0.5, 0.5, 0.5))
        cg.group.color.red, cg.group.color.green, cg.group.color.blue = rgb
        cg.group.color.alpha = 1.0
        for text in sec.slides:
            cue = _blank_cue(text)
            pres.cues.append(cue)
            cg.cue_identifiers.add().string = cue.uuid.string
    for name, order in song.arrangements:
        arr = pres.arrangements.add()
        arr.uuid.string = _new_uuid()
        arr.name = name
        for sec_name in order:
            if sec_name in group_ids:
                arr.group_identifiers.add().string = group_ids[sec_name]
    if song.ccli_number or song.author or song.publisher or song.copyright_year:
        pres.ccli.song_title = song.name.strip()
        pres.ccli.song_number = max(0, int(song.ccli_number))
        pres.ccli.author = song.author
        pres.ccli.publisher = song.publisher
        pres.ccli.copyright_year = max(0, int(song.copyright_year))
        pres.ccli.display = False
    if song.notes:
        pres.notes = song.notes
    return pres.SerializeToString()
