"""Blocklist filtering for caption text.

Words on the list are *removed*, not masked -- no asterisks, no placeholder,
nothing on screen to notice.

The hard part is that captions arrive as incremental deltas, so a banned word
can be split across them ("dam" then "n"). Filtering each delta on its own
would let the word render for a frame before it completed. So the redactor
holds back any trailing text that could still grow into a banned term, and
releases everything else immediately.

That hold is narrow on purpose: text is held only when the trailing fragment
is a live prefix of something on the list. With a typical list, virtually
every word is emitted the instant it arrives, so the latency budget is
untouched. An empty list is a straight pass-through.

Matching is case-insensitive and diacritic-insensitive ("niño" == "nino"),
matches whole words only ("ass" does not hit "class"), and supports
multi-word phrases.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

MAX_TERMS = 1000

# Spaces and hyphens are interchangeable inside a term: "bad-word" and
# "bad word" are one entry, and each matches either spelling.
_TERM_SEP = re.compile(r"[\s\-]+")

# Punctuation that must not be left orphaned behind a space after a removal.
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?…)\]}»”’%])")
_WHITESPACE_RUN = re.compile(r"[ \t]{2,}")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def fold(text: str) -> str:
    """Lowercase and strip diacritics, preserving length 1:1.

    The 1:1 property matters: match offsets found in the folded string are
    used to slice the *original*, so the text a viewer sees keeps its accents
    and capitalisation.
    """
    out = []
    for ch in text:
        base = unicodedata.normalize("NFD", ch)
        out.append((base[0] if base else ch).lower())
    return "".join(out)


def term_words(term: str) -> list[str]:
    """The folded words a term matches on, split at spaces and hyphens."""
    return [w for w in _TERM_SEP.split(fold(term)) if w]


class ParsedTerms(NamedTuple):
    terms: list[str]
    duplicates: int  # entries collapsed into an earlier one
    dropped: int     # distinct terms past MAX_TERMS, not applied


def parse_terms_report(raw) -> ParsedTerms:
    """Like `parse_terms`, but also says what was collapsed or cut off."""
    if isinstance(raw, str):
        parts = re.split(r"[\n,]", raw)
    else:
        parts = list(raw or [])
    seen: set[tuple[str, ...]] = set()
    terms: list[str] = []
    duplicates = dropped = 0
    for part in parts:
        term = " ".join(str(part).split()).strip()
        key = tuple(term_words(term))
        if not key:
            continue
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        if len(terms) >= MAX_TERMS:
            dropped += 1
            continue
        terms.append(term)
    return ParsedTerms(terms, duplicates, dropped)


def parse_terms(raw) -> list[str]:
    """Accept a list or a newline/comma-separated string; dedupe, preserve order."""
    return parse_terms_report(raw).terms


class Redactor:
    """Streaming blocklist filter. One per caption channel."""

    def __init__(self, terms=()):
        self.set_terms(terms)
        self.reset()

    # -- configuration -------------------------------------------------
    def set_terms(self, terms) -> None:
        self.terms = parse_terms(terms)
        self._folded = [term_words(t) for t in self.terms]
        self.max_words = max((len(w) for w in self._folded), default=0)
        if self._folded:
            self._pattern = re.compile(
                "|".join(
                    r"(?<!\w)" + r"[\s\-]+".join(re.escape(w) for w in words) + r"(?!\w)"
                    for words in self._folded
                )
            )
        else:
            self._pattern = None

    @property
    def active(self) -> bool:
        return self._pattern is not None

    def reset(self) -> None:
        """Clear buffered text at a segment boundary."""
        self._buf = ""
        self._prev_char = ""

    # -- streaming -----------------------------------------------------
    def feed(self, delta: str) -> str:
        """Take a caption delta, return the text that is safe to display now."""
        if not self.active:
            return delta
        self._buf += delta
        return self._drain(final=False)

    def flush(self) -> str:
        """Release everything held back (segment ended, nothing more coming)."""
        if not self.active:
            return ""
        return self._drain(final=True)

    def whole(self, text: str) -> str:
        """Redact a complete string in one pass, independent of stream state."""
        if not self.active or not text:
            return text
        cleaned = self._remove(text, allow_match_at_start=True)
        return _tidy(cleaned, "").strip()

    # -- internals -----------------------------------------------------
    def _drain(self, final: bool) -> str:
        buf = self._buf
        if not buf:
            return ""

        hold_at = len(buf) if final else self._hold_offset(buf)
        emit, rest = buf[:hold_at], buf[hold_at:]

        # Never emit trailing whitespace: a removal in the next fragment could
        # otherwise strand a space in front of punctuation.
        if not final:
            stripped = emit.rstrip(" \t")
            rest = emit[len(stripped):] + rest
            emit = stripped

        self._buf = rest
        if not emit:
            return ""

        cleaned = self._remove(emit, allow_match_at_start=not _is_word_char(self._prev_char))
        cleaned = _tidy(cleaned, self._prev_char)
        if final:
            # Nothing follows, so a space left by a trailing removal is dead.
            cleaned = cleaned.rstrip(" \t")
        if cleaned:
            self._prev_char = cleaned[-1]
        return cleaned

    def _hold_offset(self, buf: str) -> int:
        """First index of the tail that must be held back."""
        words = [(m.start(), m.end(), m.group()) for m in _WORD.finditer(buf)]
        if not words:
            return len(buf)
        trailing_partial = words[-1][1] == len(buf)

        # Try the longest tail first: the largest run of trailing words that
        # could still be extended into a term.
        for count in range(min(self.max_words, len(words)), 0, -1):
            tail = words[-count:]
            folded = [fold(w[2]) for w in tail]
            if self._could_extend(folded, trailing_partial):
                return tail[0][0]
        return len(buf)

    def _could_extend(self, seq: list[str], last_is_partial: bool) -> bool:
        """True if `seq` might be the opening of a term once more text lands."""
        for words in self._folded:
            if len(words) < len(seq):
                continue
            head, last = words[: len(seq) - 1], words[len(seq) - 1]
            if head != seq[:-1]:
                continue
            if last_is_partial:
                if last.startswith(seq[-1]):
                    return True
            elif last == seq[-1] and len(words) > len(seq):
                return True
        return False

    def _remove(self, text: str, allow_match_at_start: bool) -> str:
        folded = fold(text)
        out, cursor = [], 0
        for m in self._pattern.finditer(folded):
            if m.start() == 0 and not allow_match_at_start:
                # This fragment continues a word already on screen, so a match
                # anchored at its first character is not a real word start.
                continue
            out.append(text[cursor:m.start()])
            cursor = m.end()
        out.append(text[cursor:])
        return "".join(out)


def _is_word_char(ch: str) -> bool:
    return bool(ch) and (ch.isalnum() or ch in "'’")


def _tidy(fragment: str, prev_char: str) -> str:
    """Close the gap a removal leaves, so the line still reads naturally."""
    fragment = _WHITESPACE_RUN.sub(" ", fragment)
    fragment = _SPACE_BEFORE_PUNCT.sub(r"\1", fragment)
    if not prev_char or prev_char.isspace():
        fragment = fragment.lstrip(" \t")
    return fragment


# ---------------------------------------------------------------- file I/O
_COMMENT = re.compile(r"#.*$", re.MULTILINE)

FILE_HEADER = (
    "# Blocked words — removed from both the English and the Spanish captions.\n"
    "# One word or phrase per line. Lines starting with # are ignored.\n"
    "# Saving this file takes effect within a couple of seconds; no restart needed.\n"
)


def format_terms(terms) -> str:
    """One term per line -- the layout the file is written in."""
    return "\n".join(terms)


def read_blocklist_file(path) -> list[str]:
    """Read a comma-separated blocklist file. Missing file == empty list.

    Liberal about layout: one per line, comma-separated, or both work; extra
    spaces are fine, and `#` starts a comment.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    report = parse_terms_report(_COMMENT.sub("", raw))
    if report.dropped:
        log.warning(
            "%s: %d term(s) past the %d-term limit are not applied",
            path, report.dropped, MAX_TERMS,
        )
    return report.terms


def write_blocklist_file(path, terms) -> list[str]:
    """Write terms back in the documented format.

    Any comment lines already in the file are kept: an operator may have used
    them to label sections, and a save from the admin panel must not silently
    throw that away.
    """
    terms = parse_terms(terms)
    path = Path(path)
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    comments = [ln for ln in existing.splitlines() if ln.lstrip().startswith("#")]
    header = ("\n".join(comments) + "\n") if comments else FILE_HEADER
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(header + format_terms(terms) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return terms


def file_mtime(path) -> float:
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0
