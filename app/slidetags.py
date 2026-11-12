"""Slide-note tags (#138): a tag in a ProPresenter slide's notes triggers a Relay action when that slide goes live.

    [relay:action]            on a slide: runs when that slide has stayed live for a moment
    [relay:action=value]      the same, for an action that takes a value
    [relay-deck:action...]    in ANY slide's notes: a default for every slide of this presentation that has no
                              [relay:...] tag of its own

Action names are not case-sensitive. Anything else in the notes is ignored. An action Relay does not know is
ignored and listed in admin as a warning. A value may hold spaces, and `]` inside it as a balanced `[...]` pair or as
`\\]`; a value stops at the end of the line.

Relay never writes notes: this module only reads what the follower already knows (the current slide's notes from
`status/slide`, and every slide's notes from the one `presentation/{uuid}` read made when the presentation changes).

Two rules keep a tag from firing too much:

* **Settle**: an action runs only once the slide has stayed live for `settle_s` (1.5 s unless the operator changes
  it), so a fast run through the deck fires for the slide it stops on, not for every slide it passed.
* **Same set, once**: what runs is the slide's resolved set of actions. When the next slide that settles resolves to
  the same set (a deck-wide `[relay-deck:lyrics]` seen from every slide of a song) nothing runs again. A slide that
  resolves to nothing is a set of its own, so a tag that comes back after an untagged slide runs again.

This file knows nothing about what an action does: `Tags.actions` is filled in by main.py (capture, panels, alerts), and
the follower tells `observe` what is live. Off by default (`propresenter.tags.enabled`).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections import OrderedDict, deque
from typing import Any, Awaitable, Callable, NamedTuple

from . import languages

log = logging.getLogger("relay.slidetags")

# action -> whether it takes a value ("lang=es", "alert=Sound desk")
ACTIONS: dict[str, bool] = {
    "capture-start": False,
    "capture-stop": False,
    "lyrics": False,
    "captions": False,
    "lang": True,
    "alert": True,
}

SETTLE_DEFAULT_S = 1.5
SETTLE_MIN_S = 0.2
SETTLE_MAX_S = 30.0
VALUE_MAX = 200
ACTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
MAX_WARNINGS = 20
HISTORY_KEEP = 30
DECKS_KEEP = 32
NOTES_SCAN_MAX = 20000      # characters of one slide's notes read; a tag is short, a notes box can be huge
_OPENERS = (("[relay-deck:", "deck"), ("[relay:", "slide"))


class Tag(NamedTuple):
    action: str
    value: str | None = None


class Parsed(NamedTuple):
    slide: list[Tag]        # [relay:...], valid
    deck: list[Tag]         # [relay-deck:...], valid
    warnings: list[str]


# ---------------------------------------------------------------------------------------------------- parsing
def _read_value(text: str, i: int) -> tuple[str, int] | None:
    """The value that starts at `i` (after the `=`) and the position after the closing `]`, or None when the line
    ends first. `[`...`]` inside nest, and a backslash makes the next character plain (`\\]`)."""
    out: list[str] = []
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch in "\r\n":
            return None
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "[]\\":
            out.append(text[i + 1])
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 0:
                return "".join(out), i + 1
            depth -= 1
        out.append(ch)
        i += 1
    return None


def _check(action: str, value: str | None) -> tuple[Tag | None, str]:
    """A tag made valid (the value cleaned and the language named the way Relay names it), or a warning."""
    if action not in ACTIONS:
        return None, f"Unknown action \"{action}\" ignored."
    takes = ACTIONS[action]
    if not takes:
        if value is not None:
            return None, f"{action} takes no value, so [relay:{action}={value[:30]}] was ignored."
        return Tag(action), ""
    value = " ".join((value or "").split())
    if not value:
        return None, f"{action} needs a value, like [relay:{action}={'es' if action == 'lang' else 'Sound desk needed'}]."
    if action == "lang":
        name = language_name(value)
        if not name:
            return None, f"\"{value[:30]}\" is not a language Relay translates into, so lang was ignored."
        return Tag(action, name), ""
    return Tag(action, value[:VALUE_MAX]), ""


def language_name(value: str) -> str:
    """The target's name in languages.py ("SPANISH") for a code ("es"), a name ("Spanish") or the name as written
    there; "" when it is none of them."""
    v = str(value or "").strip()
    if not v:
        return ""
    if v.upper() in languages.TARGETS:
        return v.upper()
    low = v.lower()
    for name, spec in languages.TARGETS.items():
        if spec["iso"] == low or spec["label"].lower() == low:
            return name
    return ""


def parse(notes: object) -> Parsed:
    """Every tag in one slide's notes. A tag that is broken (no closing `]` on its line, no action name) is
    skipped with a warning and the rest of the notes are still read."""
    text = str(notes or "")[:NOTES_SCAN_MAX]
    slide: list[Tag] = []
    deck: list[Tag] = []
    warnings: list[str] = []
    low = text.lower()
    i = 0
    while True:
        i = low.find("[relay", i)
        if i < 0:
            break
        scope = ""
        for opener, name in _OPENERS:
            if low.startswith(opener, i):
                scope, start = name, i + len(opener)
                break
        if not scope:
            i += 1
            continue
        j = start
        while j < len(text) and text[j] not in "=]\r\n":
            j += 1
        action = text[start:j].strip().lower()
        if j >= len(text) or text[j] in "\r\n" or not ACTION_NAME_RE.match(action):
            warnings.append(f"A tag that starts \"{text[i:i + 24].splitlines()[0]}\" is not complete, so it was ignored.")
            i = start
            continue
        value: str | None = None
        if text[j] == "=":
            got = _read_value(text, j + 1)
            if got is None:
                warnings.append(f"[relay:{action}=... has no closing ] on its line, so it was ignored.")
                i = start
                continue
            value, end = got
        else:
            end = j + 1
        i = end
        tag, warn = _check(action, value)
        if tag is None:
            warnings.append(warn)
        else:
            (deck if scope == "deck" else slide).append(tag)
    return Parsed(slide, deck, warnings[:MAX_WARNINGS])


class Deck(NamedTuple):
    """What a whole presentation's notes say: its deck-wide defaults and the warnings found reading them."""
    tags: tuple[Tag, ...]
    warnings: tuple[str, ...]
    sig: str


def parse_deck(notes: list[str]) -> Deck:
    """The `[relay-deck:...]` tags of every slide's notes, in slide order, each once."""
    tags: list[Tag] = []
    warnings: list[str] = []
    for n, text in enumerate(notes, 1):
        got = parse(text)
        for t in got.deck:
            if t not in tags:
                tags.append(t)
        warnings += [f"Slide {n}: {w}" for w in got.warnings]
    sig = hashlib.sha1("\x1f".join(notes).encode("utf-8", "replace")).hexdigest()[:12]
    return Deck(tuple(tags), tuple(warnings[:MAX_WARNINGS]), sig)


def resolve(slide: list[Tag] | tuple[Tag, ...], deck: list[Tag] | tuple[Tag, ...]) -> tuple[Tag, ...]:
    """The actions for a slide: its own valid tags, or the deck's when it has none. Each once, in the order written."""
    chosen = slide if slide else deck
    out: list[Tag] = []
    for t in chosen:
        if t not in out:
            out.append(t)
    return tuple(out)


def label(tag: Tag) -> str:
    return tag.action if tag.value is None else f"{tag.action}={tag.value}"


# ------------------------------------------------------------------------------------------------- the watcher
Action = Callable[[str | None], Awaitable[str]]


class Tags:
    def __init__(self) -> None:
        # action -> async fn(value) -> a short sentence of what it did ("Capture started"). Filled in by main.py.
        self.actions: dict[str, Action] = {}
        self.decks: "OrderedDict[tuple[str, str], Deck]" = OrderedDict()   # (presentation uuid, hash of notes) -> Deck
        self.deck_reads = 0                  # how many times a presentation's notes were parsed (a cache hit is not one)
        self.history: deque[dict[str, Any]] = deque(maxlen=HISTORY_KEEP)
        self.seen: dict[str, Any] | None = None
        self.on_change: Callable[[], None] | None = None
        # Replaceable by a test: how a delay is scheduled and how a coroutine is run.
        self.schedule: Callable[[float, Callable[[], None]], Any] = (
            lambda delay, fn: asyncio.get_running_loop().call_later(delay, fn))
        self.spawn: Callable[[Any], Any] = lambda coro: asyncio.ensure_future(coro)
        self._key: tuple | None = None
        self._handle: Any = None
        self._pending: tuple[Tag, ...] | None = None
        self._settled: tuple[Tag, ...] | None = None
        self._lock: asyncio.Lock | None = None
        self._tasks: set[Any] = set()

    # -- decks ---------------------------------------------------------------------------------------------
    def deck_for(self, uuid: str, notes: list[str]) -> Deck:
        """The deck-wide tags of a presentation, parsed once per (uuid, hash of its notes): the follower reads the
        presentation when it changes, and coming back to one with the same notes costs nothing."""
        sig = hashlib.sha1("\x1f".join(notes).encode("utf-8", "replace")).hexdigest()[:12]
        key = (uuid, sig)
        hit = self.decks.get(key)
        if hit is not None:
            self.decks.move_to_end(key)
            return hit
        deck = parse_deck(notes)
        self.deck_reads += 1
        self.decks[key] = deck
        while len(self.decks) > DECKS_KEEP:
            self.decks.popitem(last=False)
        return deck

    # -- what is live ---------------------------------------------------------------------------------------
    def reset(self) -> None:
        """Forget what was settled and anything waiting (tags switched off, or ProPresenter gone)."""
        if self._handle is not None:
            try:
                self._handle.cancel()
            except Exception:
                pass
        self._handle = None
        self._pending = self._settled = self._key = None
        if self.seen is not None:
            self.seen = None
            self._changed()

    def observe(self, *, enabled: bool, settle_s: float, uuid: str | None, title: str = "",
                index: int | None = None, notes: str = "", deck: Deck | None = None) -> None:
        """The follower's slide changed (or may have). Costs a comparison when nothing did."""
        if not enabled:
            if self._key is not None or self.seen is not None or self._handle is not None:
                self.reset()
            return
        key = (uuid, index, notes, deck.sig if deck else "")
        if key == self._key:
            return
        self._key = key
        if self._handle is not None:
            try:
                self._handle.cancel()
            except Exception:
                pass
            self._handle = None
        if not uuid:
            slide_tags: list[Tag] = []
            deck_tags: list[Tag] = []
            warnings: list[str] = []
        else:
            got = parse(notes)
            slide_tags = got.slide
            # The tags of the slide that is up count as deck tags even if the deck has not been read (yet).
            deck_tags = list(deck.tags) if deck else []
            for t in got.deck:
                if t not in deck_tags:
                    deck_tags.append(t)
            warnings = list(dict.fromkeys(got.warnings + (list(deck.warnings) if deck else [])))[:MAX_WARNINGS]
        resolved = resolve(slide_tags, deck_tags)
        self._pending = resolved
        self.seen = {"at": time.time(), "presentation": title if uuid else "", "slide": None if index is None else index + 1,
                     "slide_tags": [label(t) for t in slide_tags], "deck_tags": [label(t) for t in deck_tags],
                     "resolved": [label(t) for t in resolved], "warnings": warnings, "settling": True}
        self._changed()
        self._handle = self.schedule(max(0.0, float(settle_s)), self._due)

    def _due(self) -> None:
        """The slide stayed up long enough. Run its set of actions unless it is the set that last ran."""
        self._handle = None
        resolved, self._pending = self._pending, None
        if resolved is None:
            return
        if self.seen is not None:
            self.seen["settling"] = False
        if resolved == self._settled:
            self._changed()
            return
        self._settled = resolved
        self._changed()
        if not resolved:
            return
        task = self.spawn(self._run(resolved))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, resolved: tuple[Tag, ...]) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            for tag in resolved:
                fn = self.actions.get(tag.action)
                row: dict[str, Any] = {"at": time.time(), "action": tag.action, "value": tag.value}
                if fn is None:
                    row.update(ok=False, result="Relay has nothing wired to that action.")
                else:
                    try:
                        row.update(ok=True, result=str(await fn(tag.value) or "Done."))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log.warning("Slide tag %s failed: %s", label(tag), exc)
                        row.update(ok=False, result=str(exc) or "That did not work.")
                self.history.appendleft(row)
                log.info("Slide tag %s: %s", label(tag), row["result"])
                self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                log.exception("slide tag listener failed")

    def state(self) -> dict[str, Any]:
        return {"seen": self.seen, "fired": list(self.history), "actions": [
            {"name": n, "value": v} for n, v in ACTIONS.items()]}


tags = Tags()
