"""Match Planning Center plan items to ProPresenter presentations (#138, the Planning Center link).

Pure functions, no network and no state, so the rules are easy to test and to read. `pclink.py` feeds them what
it read from both systems.

The order of trust, per plan item:

  1. `mapping`: an operator-set pairing of a plan item's title to a presentation, for items whose presentation
     is named differently ("Message" -> "Sermon: Grace"). The operator's word beats every guess.
  2. `synced`: ProPresenter's own playlist synced from Planning Center, in its order. Each plan item takes the
     next playlist entry (from where the last one matched) whose name fits. A PCO entry that was never linked
     to a presentation is a placeholder: reported, never guessed.
  3. exact: the same title once both are normalised (case, accents, punctuation, "(Live)", key and
     arrangement suffixes).
  4. fuzzy: the closest candidate at or above `threshold`, only when it is clearly ahead of the runner-up. A tie
     is reported as ambiguous, not picked.

Headers have nothing to match and are left out ("skipped").
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

DEFAULT_THRESHOLD = 0.8
MARGIN = 0.03            # the best fuzzy candidate must beat the runner-up (another presentation) by this much

_KEY = r"[a-g](?:#|b|♯|♭)?m?"
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
# A tail after a dash that says which version of the song, not which song: a key, "live", an arrangement.
_VERSION_TAIL = re.compile(
    rf"^(?:{_KEY}|key(?: of)? {_KEY}|live|reprise|acoustic|radio(?: edit| version)?|"
    rf"(?:default |alt(?:ernate)? |short |long |full |original )?(?:arr(?:angement)?|version|edit)(?: .*)?)$")
_KEY_SUFFIX = re.compile(rf"\s+(?:in|key of)\s+{_KEY}$")
_DASHES = re.compile(r"\s[-–—]\s")
_APOS = re.compile(r"['‘’`´]")
_NONWORD = re.compile(r"[^0-9a-z]+")


def normalise(title: Any) -> str:
    """A title reduced to what makes it the same song: lower case without accents or punctuation, no "(Live)",
    no key ("- G", "in G", "(Key of G)") and no arrangement or version suffix."""
    text = unicodedata.normalize("NFKD", str(title or ""))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("&", " and ")
    text = _PARENS.sub(" ", text)
    text = _APOS.sub("", text)
    parts = _DASHES.split(" " + text.strip() + " ")
    parts = [p.strip() for p in parts]
    while len(parts) > 1 and _VERSION_TAIL.match(_NONWORD.sub(" ", parts[-1]).strip()):
        parts.pop()
    text = " ".join(parts)
    text = _KEY_SUFFIX.sub("", _NONWORD.sub(" ", text).strip())
    words = text.split()
    if len(words) > 1 and words[-1] == "live":
        words.pop()
    return " ".join(words)


def similarity(a: str, b: str) -> float:
    """0 to 1: the better of the character match and the word overlap of two normalised titles."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()), set(b.split())
    dice = 2 * len(ta & tb) / (len(ta) + len(tb)) if ta and tb else 0.0
    return max(SequenceMatcher(None, a, b).ratio(), dice)


def _pres(uuid: str, name: str) -> dict:
    return {"uuid": str(uuid or ""), "name": str(name or "")}


def _row(item: dict, status: str, method: str = "", score: float = 0.0, pres: dict | None = None,
         note: str = "", options: list[dict] | None = None) -> dict:
    return {"item_id": str(item.get("id") or ""), "title": str(item.get("title") or ""),
            "type": str(item.get("type") or "item"), "status": status, "method": method,
            "score": round(float(score), 3), "presentation": pres, "note": note, "options": options or []}


def _fits(norm_title: str, norm_name: str, threshold: float) -> bool:
    return bool(norm_title) and (norm_title == norm_name or similarity(norm_title, norm_name) >= threshold)


def clean_mapping(raw: Any) -> dict[str, dict]:
    """The operator's pairings as {normalised plan item title: {uuid, name}}. Anything malformed is dropped."""
    out: dict[str, dict] = {}
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        key = normalise(m.get("item"))
        uuid = str(m.get("uuid") or "").strip()
        if key and uuid:
            out[key] = _pres(uuid, m.get("name"))
    return out


def match_items(items: list[dict], candidates: list[dict], *, mapping: Any = None,
                synced: list[dict] | None = None, threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """One row per plan item, in plan order.

    `items`: [{id, title, type}]. `candidates`: presentations ProPresenter has, best source first
    [{uuid, name}]. `mapping`: the operator's list [{item, uuid, name}]. `synced`: the entries of ProPresenter's
    Planning-Center-synced playlist, in order [{name, type: "presentation"|"placeholder", uuid}].
    A row's status is matched, unmatched, ambiguous, placeholder or skipped.
    """
    maps = clean_mapping(mapping)
    pool = [(c, normalise(c.get("name"))) for c in candidates if c.get("uuid")]
    entries = [(s, normalise(s.get("name"))) for s in (synced or [])
               if s.get("type") in ("presentation", "placeholder")]
    pointer = 0
    rows: list[dict] = []
    for item in items:
        if item.get("type") == "header":
            rows.append(_row(item, "skipped", note="A header has no presentation."))
            continue
        nt = normalise(item.get("title"))
        # 1. the operator's pairing
        if nt in maps:
            m = maps[nt]
            rows.append(_row(item, "matched", "mapping", 1.0, m, "Set by the operator."))
            continue
        # 2. ProPresenter's playlist synced from Planning Center, walked in order
        hit = next(((i, s) for i, (s, ns) in enumerate(entries[pointer:], pointer) if _fits(nt, ns, threshold)), None)
        if hit is not None:
            i, s = hit
            pointer = i + 1
            if s.get("type") == "placeholder" or not s.get("uuid"):
                rows.append(_row(item, "placeholder", "synced", 1.0, None,
                                 "ProPresenter's synced playlist has this item but it is not linked to a presentation."))
            else:
                rows.append(_row(item, "matched", "synced", similarity(nt, entries[i][1]),
                                 _pres(s["uuid"], s.get("name")), "From ProPresenter's playlist synced from Planning Center."))
            continue
        # 3. the same title once normalised
        exact = [c for c, nc in pool if nt and nc == nt]
        if exact:
            first = exact[0]
            same = {c["uuid"] for c in exact}
            note = "Same title." if len(same) == 1 else f"{len(same)} presentations share this title; the first was used."
            rows.append(_row(item, "matched", "exact", 1.0, _pres(first["uuid"], first.get("name")), note))
            continue
        # 4. the closest title, if it is clearly the closest
        best: dict[str, tuple[float, dict]] = {}
        for c, nc in pool:
            s = similarity(nt, nc)
            if s > best.get(c["uuid"], (0.0, c))[0]:
                best[c["uuid"]] = (s, c)
        ranked = sorted(best.values(), key=lambda p: -p[0])
        close = [(s, c) for s, c in ranked if s >= threshold]
        if not close:
            near = [{"uuid": c["uuid"], "name": str(c.get("name") or ""), "score": round(s, 3)}
                    for s, c in ranked[:3] if s >= 0.5]
            rows.append(_row(item, "unmatched", note="No presentation has a similar title.", options=near))
        elif len(close) > 1 and close[0][0] - close[1][0] < MARGIN:
            rows.append(_row(item, "ambiguous", score=close[0][0],
                             note="More than one presentation fits equally well; pick one.",
                             options=[{"uuid": c["uuid"], "name": str(c.get("name") or ""), "score": round(s, 3)}
                                      for s, c in close[:4]]))
        else:
            s, c = close[0]
            rows.append(_row(item, "matched", "fuzzy", s, _pres(c["uuid"], c.get("name")),
                             f"Similar title ({round(s * 100)}%)."))
    return rows


def items_for(rows: list[dict], uuid: str | None, name: str = "") -> list[str]:
    """The plan items (ids, plan order) ProPresenter's presentation `uuid` stands for. A presentation the rows do
    not know by UUID falls back to its normalised name against the plan item titles."""
    if uuid:
        found = [r["item_id"] for r in rows if r["status"] == "matched" and r["presentation"]
                 and r["presentation"]["uuid"] == uuid]
        if found:
            return found
    nn = normalise(name)
    if not nn:
        return []
    return [r["item_id"] for r in rows if r["status"] in ("unmatched", "ambiguous") and normalise(r["title"]) == nn]
