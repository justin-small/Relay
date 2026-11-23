"""Reading a lyric sheet into a song (#633): paste or PDF text in, sections of slides out, and the same slides cut again
at N lines. Pure functions: the songs page (static/ppsongs.js) calls them through /api/admin/propresenter/songs/parse and
/songs/split, and the operator reviews the result before anything is kept. Chords stay inline as `[G]` in a slide's text;
ppsongs.py turns them into the file's chord positions when the song is sent."""
from __future__ import annotations

import io
import re

MAX_TEXT = 200_000
MAX_PAGES = 30
NAMES = ("verse", "chorus", "pre-chorus", "prechorus", "pre chorus", "bridge", "tag", "intro", "outro", "interlude", "instrumental",
         "refrain", "ending", "coda", "turnaround", "vamp")
SHORT = {"v": "Verse", "c": "Chorus", "b": "Bridge", "pc": "Pre-Chorus"}
CHORD = re.compile(r"^[A-G][#b]?(?:maj|min|m|M|dim|aug|sus|add|\+)?\d*(?:\([^)]*\))?(?:/[A-G][#b]?)?$")
INLINE = re.compile(r"\[([^\[\]\n]{1,20})\]")


class ParseError(ValueError):
    """The text cannot be read. `message` is a fixed sentence for the operator."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def split_chords(text: str) -> tuple[str, list[tuple[int, str]]]:
    """`I [G]love You` -> (`I love You`, [(2, 'G')]): the text without markers, and where each chord sits in it."""
    out, chords, last, removed = [], [], 0, 0
    for m in INLINE.finditer(text):
        out.append(text[last:m.start()])
        chords.append((m.start() - removed, m.group(1).strip()))
        removed += m.end() - m.start()
        last = m.end()
    out.append(text[last:])
    return "".join(out), chords


def strip_chords(text: str) -> str:
    return split_chords(text)[0]


def with_chords(text: str, chords: list) -> str:
    """The reverse of split_chords: markers put back into a file's text so the operator can edit them."""
    for pos, name in sorted(((int(c[0]), str(c[1])) for c in chords), reverse=True):
        pos = max(0, min(pos, len(text)))
        text = text[:pos] + f"[{name}]" + text[pos:]
    return text


def is_header(line: str) -> str:
    """The section name a line stands for ("Verse 1"), or "" when it is not a section heading. Plain string steps, not a
    pattern: a pasted sheet is untrusted text and a long line must not cost more than its length."""
    t = line.strip()
    if not t or len(t) > 24:
        return ""
    t = t.strip("[]():").strip().lower()
    word, num = t, ""
    cut = len(t.rstrip("0123456789"))
    if cut < len(t):
        word, num = t[:cut].strip(), t[cut:]
        if len(num) > 2:
            return ""
    if word in SHORT:
        return f"{SHORT[word]} {num}" if num else ""     # "v", "c", "b" and "pc" need a number: "[C]" is a chord
    if word not in NAMES:
        return ""
    base = "Pre-Chorus" if word.startswith("pre") else word.capitalize()
    return f"{base} {num}" if num else base


def _is_chord_line(line: str) -> bool:
    toks = line.split()
    return bool(toks) and all(CHORD.match(t) for t in toks)


def _merge_chords(chord_line: str, lyric: str) -> str:
    """Chords written above a lyric line, put into it at the same columns as `[G]`."""
    at = [(m.start(), m.group()) for m in re.finditer(r"\S+", chord_line)]
    lyric = lyric.ljust(max((p for p, _ in at), default=0))
    for pos, name in reversed(at):
        lyric = lyric[:pos] + f"[{name}]" + lyric[pos:]
    return lyric.rstrip()


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _chunk(pairs: list[tuple[str, str]], n: int) -> list[dict]:
    step = n if n > 0 else max(len(pairs), 1)
    out = []
    for i in range(0, len(pairs), step):
        part = pairs[i:i + step]
        out.append({"text": "\n".join(p[0] for p in part), "translation": "\n".join(p[1] for p in part if p[1])})
    return out


def parse_text(text: str, lines: int = 4, blank_splits: bool = True, headers: bool = True, translation: bool = True,
               chords: bool = True) -> dict:
    """A pasted lyric sheet as {sections: [{name, slides: [{text, translation}]}], arrangement: [names], warnings: [...]}.
    `lines` is how many lines go on a slide (0: no limit); a blank line ends a slide when `blank_splits`; a line that is
    just a section name starts a section when `headers`; a line starting with `/` is the translation of the line above
    it when `translation`; a row of chord names above a lyric line goes into the lyric as `[G]` when `chords`."""
    if not isinstance(text, str) or not text.strip():
        raise ParseError("Paste some lyrics first.")
    if len(text) > MAX_TEXT:
        raise ParseError("That text is too long.")
    raw = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ").split("\n")
    parts: list[tuple[str, list[str]]] = [("", [])]
    for ln in raw:
        name = is_header(ln) if headers else ""
        if name:
            parts.append((name, []))
        else:
            parts[-1][1].append(ln.rstrip())
    sections, order, warnings, seen = [], [], [], {}
    for name, body in parts:
        if not name and not any(x.strip() for x in body):
            continue
        slides = []
        blocks = _blocks(body) if blank_splits else [[x for x in body if x.strip()]]
        for block in blocks:
            pairs: list[tuple[str, str]] = []
            i = 0
            while i < len(block):
                ln = block[i]
                if chords and _is_chord_line(ln) and i + 1 < len(block) and not _is_chord_line(block[i + 1]) \
                        and not block[i + 1].lstrip().startswith("/"):
                    pairs.append((_merge_chords(ln, block[i + 1]), ""))
                    i += 2
                    continue
                if translation and ln.lstrip().startswith("/") and pairs:
                    prev = pairs[-1]
                    pairs[-1] = (prev[0], (prev[1] + "\n" if prev[1] else "") + ln.lstrip()[1:].strip())
                elif ln.strip():
                    pairs.append((ln.strip() if not chords else ln.rstrip().lstrip(), ""))
                i += 1
            slides.extend(_chunk(pairs, lines))
        title = name or "Verse 1"
        sig = [s["text"] for s in slides]
        if title in seen and seen[title] == sig:
            order.append(title)
            warnings.append(f"“{title}” appears again with the same words. Kept once; it repeats in the arrangement.")
            continue
        if title in seen:
            k = 2
            while f"{title} {k}" in seen:
                k += 1
            title = f"{title} {k}"
        seen[title] = sig
        order.append(title)
        if not slides:
            warnings.append(f"“{title}” has no lines.")
        sections.append({"name": title, "slides": slides or [{"text": "", "translation": ""}]})
    if not sections:
        raise ParseError("Nothing in that text looks like lyrics.")
    return {"sections": sections, "arrangement": order, "warnings": warnings}


def pdf_text(data: bytes) -> str:
    """The text of a PDF lyric or chord sheet, laid out as printed so chords above a line stay above it."""
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:
        raise ParseError("Reading PDFs is not available on this install.") from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("That PDF is locked with a password.")
        pages = reader.pages[:MAX_PAGES]
        text = "\n\n".join(p.extract_text(extraction_mode="layout") or "" for p in pages)
    except ParseError:
        raise
    except (PyPdfError, ValueError, KeyError, OSError, TypeError, RecursionError):
        raise ParseError("That PDF could not be read.") from None
    text = "\n".join(ln.rstrip() for ln in text.split("\n"))
    if not text.strip():
        raise ParseError("That PDF has no text in it (a scan or a picture). Paste the words instead.")
    margin = min((len(x) - len(x.lstrip()) for x in text.split("\n") if x.strip()), default=0)
    return "\n".join(x[margin:] for x in text.split("\n"))


def resplit(sections: list, lines: int) -> list[dict]:
    """The same sections with every slide's lines cut again into slides of `lines` (0: one slide per section). Each
    lyric line stays with its translation line; the first slide of a section keeps that section's first slide's label,
    hot key and timing."""
    out = []
    for s in sections:
        pairs, first = [], {}
        for k, sl in enumerate(s.get("slides") or []):
            if k == 0:
                first = {key: sl.get(key) for key in ("label", "hot_key", "advance_after", "completion", "notes") if sl.get(key)}
            L = str(sl.get("text") or "").split("\n")
            T = str(sl.get("translation") or "").split("\n") if sl.get("translation") else []
            for j, ln in enumerate(L):
                if ln.strip() or len(L) == 1:
                    pairs.append((ln, T[j] if j < len(T) else ""))
        slides = _chunk(pairs, int(lines)) or [{"text": "", "translation": ""}]
        slides[0].update(first)
        out.append({**{k: v for k, v in s.items() if k != "slides"}, "slides": slides})
    return out
