"""The two signage sources this module registers: saved links by id."""
from __future__ import annotations

from ... import config
from . import links


def _links(kind: str) -> list[dict]:
    want = "youtube" if kind == "youtube" else "web"
    return [l for l in config.get()["signage_web"]["links"] if l["kind"] == want]


def _find(kind: str, ref: str) -> dict | None:
    return next((l for l in _links(kind) if l["id"] == ref), None)


def resolver(kind: str):
    def resolve(ref: str):
        link = _find(kind, ref)
        if link is None:
            return None
        return links.resolve_youtube(link) if link["kind"] == "youtube" else links.resolve_web(link)
    return resolve


def describer(kind: str):
    def describe(ref: str):
        link = _find(kind, ref)
        return {"name": link["name"], "thumb": ""} if link else {"name": ref, "thumb": "", "missing": True}
    return describe


def chooser(kind: str):
    def choices():
        # A single video plays to its end by default (duration 0); everything else gets a number of seconds.
        out = []
        for l in _links(kind):
            yt = links.parse_youtube(l["url"]) or {}
            out.append({"ref": l["id"], "name": l["name"], "thumb": "",
                        "kind": "video" if yt.get("video") and not yt.get("list") else ""})
        return out
    return choices
