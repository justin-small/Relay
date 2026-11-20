"""Shared text helpers (#522): one way to compare names and make slugs, for core and every module.

Before this, each feature tidied names its own way (exact, `.lower()`, `.casefold()`, accents kept or not), so the
same team or cue name could match on one page and not on another. Use these instead of writing a new one;
`tests/test_shared_concepts.py` refuses a new slug or name-tidying helper anywhere else.

* `squeeze`: runs of whitespace become one space, ends trimmed. For display text.
* `fold`: `squeeze` and case-insensitive. Two names are "the same" when their folds are equal: OBS scenes,
  lighting cues, slide-tag targets, checklist positions, rundown positions.
* `plain`: `fold` without accents or punctuation, words only. For fuzzy matching of people and titles.
* `slug`: a lowercase address from a name, numbered when already taken.

Fuzzy scoring stays with the feature that knows what it compares (people in crew, song titles in ProPresenter);
those build on `plain` or `strip_accents`. `app/redact.py` keeps its own `fold` because it must keep the text's
length one-to-one.
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from typing import Any

_NOT_WORD = re.compile(r"[^a-z0-9 ]+")


def squeeze(text: Any) -> str:
    """`text` as a string with each run of whitespace made one space and the ends trimmed ("" for None)."""
    return " ".join(str(text or "").split())


def fold(text: Any) -> str:
    """`text` squeezed and case-folded: the key two names are compared by."""
    return squeeze(text).casefold()


def same_name(a: Any, b: Any) -> bool:
    """Whether two names are the same, ignoring case and spacing. Two empty names are not a match."""
    fa = fold(a)
    return bool(fa) and fa == fold(b)


def strip_accents(text: Any) -> str:
    """`text` with accents removed ("José" -> "Jose"); case and punctuation kept."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def plain(text: Any) -> str:
    """Case-folded words without accents or punctuation, single-spaced: "O'Brien-Smith, José" -> "o brien smith
    jose". Letters outside a-z (other scripts) are dropped, as people matching always did."""
    return " ".join(_NOT_WORD.sub(" ", strip_accents(text).casefold()).split())


def slug(name: Any, taken: Collection[str] = (), *, sep: str = "-", limit: int | None = 32,
         fallback: str = "item") -> str:
    """A lowercase address made from `name`: letters and digits joined by `sep`, at most `limit` characters (None:
    no limit), or `fallback` when nothing is left. When it is in `taken`, `sep` and 2, 3... are added."""
    base = re.sub(r"[^a-z0-9]+", sep, str(name or "").lower()).strip(sep)
    base = base[:limit].strip(sep) if limit is not None else base
    base = base or fallback
    out, n = base, 2
    while out in taken:
        out = f"{base}{sep}{n}"
        n += 1
    return out
