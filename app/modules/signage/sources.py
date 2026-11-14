"""Where playlist items come from (#205). Signage supplies uploaded media itself ("media"); other modules add their
own kinds of item (ProPresenter announcement slides #208, Planning Center media #209, web pages and YouTube #210).

A module that adds items names signage in its `uses`, registers its source in `attach()` and unregisters it in
`detach()`:

    from ..signage import sources

    sources.register(sources.Source(
        kind="propresenter_slide", label="ProPresenter announcement",
        resolve=lambda ref: {"type": "image", "url": "..."},          # what the player shows, or None to skip
        describe=lambda ref: {"name": "Welcome slide", "thumb": ""},  # how the admin page lists it
        choices=lambda: [{"ref": "...", "name": "...", "thumb": ""}],  # what can be added from it
    ))

`resolve` returns one of:

    {"type": "image", "url": "<http URL or a path on the signage port>"}
    {"type": "video", "url": "..."}               # plays to the end, or for the item's duration when it is set
    {"type": "page",  "url": "https://..."}       # a web page in a frame, for the item's duration

Items whose source is not registered (its module is off) are skipped by the player and greyed on the admin page.
None of these may raise into signage: a failing source skips its item and is logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger("relay.signage")

PLAYER_TYPES = ("image", "video", "page")


@dataclass(frozen=True)
class Source:
    kind: str
    label: str
    resolve: Callable[[str], dict | None]
    describe: Callable[[str], dict] = field(default=lambda ref: {"name": ref, "thumb": ""})
    choices: Callable[[], list[dict]] = field(default=lambda: [])


_sources: dict[str, Source] = {}


def register(source: Source) -> None:
    _sources[source.kind] = source


def unregister(kind: str) -> None:
    _sources.pop(kind, None)


def get(kind: str) -> Source | None:
    return _sources.get(kind)


def kinds() -> list[Source]:
    return list(_sources.values())


def resolve(kind: str, ref: str) -> dict | None:
    src = _sources.get(kind)
    if src is None:
        return None
    try:
        out = src.resolve(ref)
    except Exception:
        log.exception("signage source %s failed to resolve %r", kind, ref)
        return None
    if not isinstance(out, dict) or out.get("type") not in PLAYER_TYPES or not isinstance(out.get("url"), str):
        return None
    return {"type": out["type"], "url": out["url"]}


def describe(kind: str, ref: str) -> dict[str, Any]:
    src = _sources.get(kind)
    if src is None:
        return {"name": ref, "thumb": "", "missing": True}
    try:
        d = src.describe(ref) or {}
    except Exception:
        log.exception("signage source %s failed to describe %r", kind, ref)
        d = {}
    return {"name": str(d.get("name") or ref), "thumb": str(d.get("thumb") or ""), "missing": bool(d.get("missing"))}
