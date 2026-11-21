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
    """A ProPresenter file Relay cannot read, or a song it cannot write. `message` is a fixed sentence, safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


# ---------- the song model ----------

# What a slide can do besides show words: ProPresenter's action kinds, by the name Relay uses.
ACTION_KINDS = {  # kind -> (ActionType enum name, the Action.<Type> message that carries it)
    "macro": ("ACTION_TYPE_MACRO", "MacroType"),
    "look": ("ACTION_TYPE_AUDIENCE_LOOK", "AudienceLookType"),
    "prop": ("ACTION_TYPE_PROP", "PropType"),
    "message": ("ACTION_TYPE_MESSAGE", "MessageType"),
    "timer": ("ACTION_TYPE_TIMER", "TimerType"),
    "clear": ("ACTION_TYPE_CLEAR", "ClearType"),
    "clear_group": ("ACTION_TYPE_CLEAR_GROUP", "ClearGroupType"),
    "stage": ("ACTION_TYPE_STAGE_LAYOUT", "StageLayoutType"),
}
TIMER_ACTIONS = ("start", "stop", "reset", "reset_and_start", "stop_and_reset", "increment")
CLEAR_LAYERS = ("all", "audio", "background", "live_video", "prop", "slide", "logo", "messages", "audio_effects")
KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
COMPLETION = ("", "next", "first", "random")


@dataclass
class SlideAction:
    """Something a slide does when it is shown: fire a macro or look, start a timer, clear a layer, show a prop or
    message, or pick a stage layout. `target` is the thing's name; `uuid` its id in the workspace when known (a
    macro, look, prop, message or timer is found by id, so read the library, or the API's lists, for it)."""
    kind: str
    target: str = ""
    uuid: str = ""
    what: str = ""          # timer: one of TIMER_ACTIONS; clear: one of CLEAR_LAYERS
    amount: float = 0.0     # timer: seconds to add for "increment"
    screen: str = ""        # stage: the stage screen's name; `target` is then the layout
    screen_uuid: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise FileError(f"Unknown slide action '{self.kind}'.")
        if self.kind == "timer" and self.what and self.what not in TIMER_ACTIONS:
            raise FileError(f"Unknown timer action '{self.what}'.")
        if self.kind == "clear" and self.what and self.what not in CLEAR_LAYERS:
            raise FileError(f"Unknown clear layer '{self.what}'.")


@dataclass
class Slide:
    text: str = ""
    translation: str = ""                 # a second line of text under the first, smaller (another language)
    label: str = ""                       # the name under the slide's thumbnail
    label_colour: tuple[float, float, float] | None = None
    notes: str = ""                       # the slide's notes
    chords: list[tuple[int, str]] = field(default_factory=list)   # (character offset into `text`, chord)
    hot_key: str = ""                     # one of KEYS
    advance_after: float = 0.0            # seconds, then go on as `completion` says
    completion: str = ""                  # "" none, "next", "first" (loop) or "random"
    transition: float = 0.0               # seconds; 0 keeps the song's own
    actions: list[SlideAction] = field(default_factory=list)
    enabled: bool = True


@dataclass
class Section:
    name: str
    slides: list[Slide] = field(default_factory=list)
    colour: tuple[float, float, float] | None = None
    hot_key: str = ""

    def __post_init__(self) -> None:
        self.slides = [Slide(text=x) if isinstance(x, str) else x for x in self.slides]


@dataclass
class Song:
    name: str = ""
    uuid: str = ""
    sections: list[Section] = field(default_factory=list)
    arrangements: list[tuple[str, list[str]]] = field(default_factory=list)  # (name, section names in order)
    selected_arrangement: str = ""
    ccli_number: int = 0
    author: str = ""
    publisher: str = ""
    copyright_year: int = 0
    album: str = ""
    artist_credits: str = ""
    show_ccli: bool = False               # show the CCLI details on the song's first or last slide
    notes: str = ""
    category: str = ""
    music_key: str = ""                   # e.g. "G" or "F#m"
    original_key: str = ""                # the key the song was written in
    background: tuple[float, float, float] | None = None
    bible_reference: str = ""             # e.g. "John 3:16" for a scripture presentation (display text)
    slide_show_seconds: float = 0.0       # play the slides on their own, this long each
    transition: float = 0.0               # seconds, for the whole song
    announcements: bool = False           # show in the announcements layer, not the global one

    def lyrics(self) -> str:
        return "\n\n".join(f"{s.name}\n" + "\n\n".join(x.text for x in s.slides) for s in self.sections)


def _key_to_proto(key: str) -> tuple[int, int] | None:
    """`G`, `F#m`, `Bb` as (MusicKey, MusicScale) numbers, or None for a key Relay does not know."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(m|min|minor)?", key.strip())
    if not m:
        return None
    name = m.group(1).upper() + m.group(2)
    try:
        return _KEY_NAMES.index(name), 1 if m.group(3) else 0
    except ValueError:
        return None


_KEY_NAMES = ["Ab", "A", "A#", "Bb", "B", "B#", "Cb", "C", "C#", "Db", "D", "D#", "Eb", "E", "E#", "Fb", "F", "F#",
              "Gb", "G", "G#"]


def _key_from_proto(key_num: int, scale: int) -> str:
    return _KEY_NAMES[key_num] + ("m" if scale else "") if 0 <= key_num < len(_KEY_NAMES) else ""


def _colour(c: Any) -> tuple[float, float, float]:
    return (round(c.red, 3), round(c.green, 3), round(c.blue, 3))


def _set_colour(dst: Any, rgb: tuple[float, float, float]) -> None:
    dst.red, dst.green, dst.blue, dst.alpha = rgb[0], rgb[1], rgb[2], 1.0


def _hot_key_name(msg: Any) -> str:
    from google.protobuf.internal import enum_type_wrapper  # noqa: F401  (the enum lives on the descriptor)

    code = msg.code
    if not code:
        return ""
    name = msg.DESCRIPTOR.fields_by_name["code"].enum_type.values_by_number[code].name
    return name.replace("KEY_CODE_ANSI_", "") if name.startswith("KEY_CODE_ANSI_") and len(name) == 15 else ""


def _set_hot_key(msg: Any, key: str) -> None:
    key = key.strip().upper()
    if not key:
        return
    if key not in KEYS or len(key) != 1:
        raise FileError(f"'{key}' is not a hot key Relay can set (use a letter or digit).")
    msg.code = msg.DESCRIPTOR.fields_by_name["code"].enum_type.values_by_name["KEY_CODE_ANSI_" + key].number


def _action_message_field(action: Any, type_name: str) -> str:
    for fd in action.DESCRIPTOR.fields:
        if fd.message_type is not None and fd.message_type.full_name == "rv.data.Action." + type_name:
            return fd.name
    raise FileError("ProPresenter's format has no such action.")  # pragma: no cover  (the descriptor has them all)


def _enum_number(msg: Any, field_name: str, value_name: str) -> int:
    return msg.DESCRIPTOR.fields_by_name[field_name].enum_type.values_by_name[value_name].number


def _enum_name(msg: Any, field_name: str, number: int) -> str:
    return msg.DESCRIPTOR.fields_by_name[field_name].enum_type.values_by_number[number].name


# ---------- reading ----------

def _slide_elements(cue: Any) -> list[Any]:
    for action in cue.actions:
        if action.HasField("slide") and action.slide.HasField("presentation"):
            return [e.element for e in action.slide.presentation.base_slide.elements if e.element.HasField("text")
                    and e.element.text.rtf_data]
    return []


def _read_action(action: Any) -> SlideAction | None:
    for kind, (type_name, msg_name) in ACTION_KINDS.items():
        if _enum_name(action, "type", action.type) != type_name:
            continue
        body = getattr(action, _action_message_field(action, msg_name))
        out = SlideAction(kind=kind)
        for ident in ("identification", "message_identificaton", "timer_identification"):
            if ident in body.DESCRIPTOR.fields_by_name and body.HasField(ident):
                out.uuid = getattr(body, ident).parameter_uuid.string
                out.target = getattr(body, ident).parameter_name
        if kind == "stage" and body.stage_screen_assignments:
            asg = body.stage_screen_assignments[0]
            out.screen, out.screen_uuid = asg.screen.parameter_name, asg.screen.parameter_uuid.string
            out.target, out.uuid = asg.layout.parameter_name, asg.layout.parameter_uuid.string
        if kind == "timer":
            out.what = _enum_name(body, "action_type", body.action_type).replace("ACTION_", "").lower()
            out.amount = body.increment_amount
        if kind == "clear":
            out.what = _enum_name(body, "target_layer", body.target_layer).replace("CLEAR_TARGET_LAYER_", "").lower()
        return out
    return None


def _read_slide(cue: Any) -> Slide:
    els = _slide_elements(cue)
    slide = Slide(enabled=cue.isEnabled)
    if els:
        slide.text = rtf_to_text(els[0].text.rtf_data)
        slide.chords = [(a.range.start, a.chord) for a in els[0].text.attributes.custom_attributes
                        if a.WhichOneof("Attribute") == "chord"]
        if len(els) > 1:
            slide.translation = rtf_to_text(els[1].text.rtf_data)
    slide.hot_key = _hot_key_name(cue.hot_key)
    kind = cue.completion_action_type
    if kind == 3:  # after time
        slide.advance_after = cue.completion_time
        slide.completion = {1: "next", 4: "first", 2: "random"}.get(cue.completion_target_type, "")
    for action in cue.actions:
        t = _enum_name(action, "type", action.type)
        if t == "ACTION_TYPE_PRESENTATION_SLIDE":
            if action.label.text:
                slide.label = action.label.text
                if action.label.HasField("color"):
                    slide.label_colour = _colour(action.label.color)
            pres = action.slide.presentation
            if pres.HasField("notes") and pres.notes.rtf_data:
                slide.notes = rtf_to_text(pres.notes.rtf_data)
            if pres.HasField("transition"):
                slide.transition = pres.transition.duration
        else:
            sa = _read_action(action)
            if sa is not None:
                slide.actions.append(sa)
    return slide


def parse_song(data: bytes) -> Song:
    """A `.pro` file as a Song: sections with their slides (text, chords, notes, labels, actions), arrangements, CCLI
    and the song's own settings. A file that is not a presentation raises FileError."""
    C = _classes()
    pres = C.Presentation()
    try:
        pres.ParseFromString(data)
    except Exception as exc:  # protobuf raises its own DecodeError
        raise FileError("This is not a ProPresenter song file.") from exc
    if not pres.name and not pres.cue_groups and not pres.cues:
        raise FileError("This is not a ProPresenter song file.")
    cues = {c.uuid.string: c for c in pres.cues}
    song = Song(name=pres.name, uuid=pres.uuid.string, notes=pres.notes, category=pres.category)
    by_id: dict[str, str] = {}
    for cg in pres.cue_groups:
        name = cg.group.name or "Slides"
        sec = Section(name=name)
        if cg.group.HasField("color"):
            sec.colour = _colour(cg.group.color)
        if cg.group.HasField("hotKey"):
            sec.hot_key = _hot_key_name(cg.group.hotKey)
        for ident in cg.cue_identifiers:
            cue = cues.get(ident.string)
            if cue is not None:
                sec.slides.append(_read_slide(cue))
        song.sections.append(sec)
        by_id[cg.group.uuid.string] = name
    for arr in pres.arrangements:
        song.arrangements.append((arr.name, [by_id.get(g.string, "") for g in arr.group_identifiers]))
        if arr.uuid.string == pres.selected_arrangement.string:
            song.selected_arrangement = arr.name
    cc = pres.ccli
    song.ccli_number, song.author, song.publisher = cc.song_number, cc.author, cc.publisher
    song.copyright_year, song.album, song.artist_credits, song.show_ccli = cc.copyright_year, cc.album, cc.artist_credits, cc.display
    if pres.HasField("music"):
        song.original_key = _key_from_proto(pres.music.original.music_key, pres.music.original.music_scale) \
            if pres.music.HasField("original") else ""
        song.music_key = _key_from_proto(pres.music.user.music_key, pres.music.user.music_scale) \
            if pres.music.HasField("user") else ""
    song.music_key = song.music_key or pres.music_key
    if pres.HasField("background") and pres.background.is_enabled and pres.background.HasField("color"):
        song.background = _colour(pres.background.color)
    br = pres.bible_reference
    if br.book_name:
        ch, vs = br.chapter_range, br.verse_range
        song.bible_reference = f"{br.book_name} {ch.start}" + (f":{vs.start}" + (f"-{vs.end}" if vs.end > vs.start else "") if vs.start else "")
    song.slide_show_seconds = pres.slide_show_duration if pres.WhichOneof("SlideShow") else 0.0
    if pres.HasField("transition"):
        song.transition = pres.transition.duration
    song.announcements = pres.content_destination == 1
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


_DEFAULT_RTF = (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n{\\fonttbl\\f0\\fswiss\\fcharset0 Arial-Black;}\n"
                b"{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\qc\n\\f0\\b\\fs120 \\cf1 \\strokec0 ")


def _plain_rtf(text: str) -> bytes:
    body = "\\\n".join(_rtf_escape(line) for line in text.split("\n"))
    return (b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n"
            b"{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\pardirnatural\\partightenfactor0\n\n\\f0\\fs48 \\cf1 "
            + body.encode("latin-1") + b"}")


def _slide_action(action: SlideAction) -> Any:
    C = _classes()
    Cue = C.Cue
    a = Cue().actions.add()
    type_name, msg_name = ACTION_KINDS[action.kind]
    a.uuid.string = _new_uuid()
    a.isEnabled = True
    a.type = _enum_number(a, "type", type_name)
    body = getattr(a, _action_message_field(a, msg_name))
    body.SetInParent()
    if action.kind == "stage":
        asg = body.stage_screen_assignments.add()
        asg.screen.parameter_uuid.string = action.screen_uuid or _new_uuid()
        asg.screen.parameter_name = action.screen
        asg.layout.parameter_uuid.string = action.uuid or _new_uuid()
        asg.layout.parameter_name = action.target
    for ident in ("identification", "message_identificaton", "timer_identification"):
        if ident in body.DESCRIPTOR.fields_by_name:
            el = getattr(body, ident)
            el.parameter_uuid.string = action.uuid or _new_uuid()
            el.parameter_name = action.target
            break
    if action.kind == "timer":
        body.action_type = _enum_number(body, "action_type", "ACTION_" + (action.what or "start").upper())
        body.increment_amount = action.amount
    if action.kind == "clear":
        body.target_layer = _enum_number(body, "target_layer", "CLEAR_TARGET_LAYER_" + (action.what or "all").upper())
    if action.kind == "prop":
        body.trigger.SetInParent()
    out = C.Cue().actions.add()
    out.CopyFrom(a)
    return out


def _blank_cue(slide: Slide) -> Any:
    C = _classes()
    cue = C.Cue()
    cue.ParseFromString((_DIR / "lyric_slide.bin").read_bytes())
    _fresh_uuids(cue)
    cue.isEnabled = slide.enabled
    if slide.hot_key:
        _set_hot_key(cue.hot_key, slide.hot_key)
    if slide.advance_after > 0:
        cue.completion_action_type = 3
        cue.completion_time = slide.advance_after
        cue.completion_target_type = {"first": 4, "random": 2}.get(slide.completion, 1)
    elif slide.completion:
        raise FileError("A slide needs a time to go on to the next one.")
    elems = []
    for action in cue.actions:
        if action.HasField("slide") and action.slide.HasField("presentation"):
            pres = action.slide.presentation
            if slide.label:
                action.label.text = slide.label
                if slide.label_colour:
                    _set_colour(action.label.color, slide.label_colour)
            if slide.notes:
                pres.notes.rtf_data = _plain_rtf(slide.notes)
            if slide.transition > 0:
                pres.transition.duration = slide.transition
            elems = [e.element for e in pres.base_slide.elements if e.element.HasField("text")]
    if elems:
        main = elems[0]
        main.text.rtf_data = text_to_rtf(main.text.rtf_data or _DEFAULT_RTF, slide.text)
        for pos, chord in slide.chords:
            if not 0 <= pos <= len(slide.text):
                raise FileError("A chord sits outside the slide's text.")
            attr = main.text.attributes.custom_attributes.add()
            attr.range.start, attr.range.end = pos, min(pos + 1, len(slide.text))
            attr.chord = chord
        if slide.chords:
            main.text.chord_pro.enabled = True
        if slide.translation:
            second = type(main)()
            second.CopyFrom(main)
            second.uuid.string = _new_uuid()
            del second.text.attributes.custom_attributes[:]
            second.text.chord_pro.enabled = False
            second.text.rtf_data = text_to_rtf(main.text.rtf_data.replace(b"\\fs120", b"\\fs72"), slide.translation)
            second.text.attributes.font.size = max(1.0, main.text.attributes.font.size * 0.6)
            h = main.bounds.size.height
            main.bounds.size.height = h * 0.58
            second.bounds.origin.y = main.bounds.origin.y + h * 0.6
            second.bounds.size.height = h * 0.4
            cue.actions[0].slide.presentation.base_slide.elements.add().element.CopyFrom(second)
    for act in slide.actions:
        cue.actions.add().CopyFrom(_slide_action(act))
    return cue


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
    pres.category = song.category
    group_ids: dict[str, str] = {}
    for sec in song.sections:
        cg = pres.cue_groups.add()
        cg.group.uuid.string = group_ids.setdefault(sec.name, _new_uuid())
        cg.group.name = sec.name
        rgb = sec.colour or SECTION_COLOURS.get(re.sub(r"[^a-z-]", "", sec.name.lower()), (0.5, 0.5, 0.5))
        _set_colour(cg.group.color, rgb)
        if sec.hot_key:
            _set_hot_key(cg.group.hotKey, sec.hot_key)
        for slide in sec.slides:
            cue = _blank_cue(slide)
            pres.cues.append(cue)
            cg.cue_identifiers.add().string = cue.uuid.string
    arr_ids: dict[str, str] = {}
    for name, order in song.arrangements:
        arr = pres.arrangements.add()
        arr.uuid.string = arr_ids.setdefault(name, _new_uuid())
        arr.name = name
        for sec_name in order:
            if sec_name not in group_ids:
                raise FileError(f"The arrangement '{name}' names a section the song does not have: {sec_name}.")
            arr.group_identifiers.add().string = group_ids[sec_name]
    if song.selected_arrangement:
        if song.selected_arrangement not in arr_ids:
            raise FileError("The selected arrangement is not one of the song's arrangements.")
        pres.selected_arrangement.string = arr_ids[song.selected_arrangement]
    if any((song.ccli_number, song.author, song.publisher, song.copyright_year, song.album, song.artist_credits)):
        pres.ccli.song_title = song.name.strip()
        pres.ccli.song_number = max(0, int(song.ccli_number))
        pres.ccli.author, pres.ccli.publisher = song.author, song.publisher
        pres.ccli.copyright_year = max(0, int(song.copyright_year))
        pres.ccli.album, pres.ccli.artist_credits = song.album, song.artist_credits
        pres.ccli.display = song.show_ccli
    if song.notes:
        pres.notes = song.notes
    for label, attr in ((song.music_key, "user"), (song.original_key, "original")):
        if label:
            parsed = _key_to_proto(label)
            if parsed is None:
                raise FileError(f"'{label}' is not a musical key Relay knows (try G, F#m or Bb).")
            getattr(pres.music, attr).music_key, getattr(pres.music, attr).music_scale = parsed
    if song.music_key:
        pres.music_key = song.music_key
    if song.background:
        pres.background.is_enabled = True
        _set_colour(pres.background.color, song.background)
    if song.bible_reference:
        m = re.fullmatch(r"\s*(.+?)\s+(\d+)(?::(\d+)(?:-(\d+))?)?\s*", song.bible_reference)
        if not m:
            raise FileError("A scripture reference looks like John 3:16 or Psalm 23.")
        br = pres.bible_reference
        br.book_name = m.group(1)
        br.chapter_range.start = br.chapter_range.end = int(m.group(2))
        if m.group(3):
            br.verse_range.start = int(m.group(3))
            br.verse_range.end = int(m.group(4) or m.group(3))
    if song.slide_show_seconds > 0:
        pres.slide_show_duration = song.slide_show_seconds
    if song.transition > 0:
        pres.transition.duration = song.transition
    if song.announcements:
        pres.content_destination = 1
    return pres.SerializeToString()
