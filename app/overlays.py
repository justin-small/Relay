"""Broadcast overlay settings: validation, layout presets, and named overlays.

Pure functions only -- nothing here touches the engine, so config.py can use it
to clean the `overlays` list without an import cycle. The page that draws an
overlay is `/overlay` in main.py (template viewer.html, script viewer.js).

An overlay is set by URL (`/overlay?lang=SPANISH&pos=top...`) or saved by name
and opened at `/overlay/<id>`. Both go through `resolve()`, so a hand-typed URL
and a saved overlay accept exactly the same values. Every value lands in CSS or
markup, so each one is an enum, a clamped number, or a 6-digit hex colour; a
value that is none of those falls back to its default rather than reaching the
page (a hand-typed URL must never inject CSS).

A named overlay is stored in config.json as:

    {"id": "stream-es", "name": "Stream – Spanish",
     "settings": {"preset": "lower", "stream": "translation", "lang": "SPANISH", ...}}

`settings` holds only what differs from the preset, so a later change to a
preset's defaults reaches overlays saved without overriding that value.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

NAME_MAX = 60
ID_MAX = 40
_ID = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?\Z")
_HEX = re.compile(r"#?([0-9A-Fa-f]{6})\Z")
# Background: the transparent keyword, a hex colour (with or without '#'; the
# builder sends it bare so the URL needs no escaping), or a bare colour name.
_BG_HEX = re.compile(r"#?([0-9A-Fa-f]{3,8})\Z")
_BG_NAME = re.compile(r"[A-Za-z]{1,20}\Z")

STREAMS = ("translation", "transcription", "both")
POSITIONS = ("bottom", "middle", "top")
ALIGNS = ("center", "left", "right")
FAMILIES = ("sans", "serif", "rounded", "hyper", "mono")
EDGES = ("plate", "outline", "shadow", "none")
MODES = ("rollup", "popon")

# Every option: default, then how it is checked. Defaults match the overlay as
# it was before the builder (bottom, centred, plated, roll-up), so an existing
# /overlay URL in a church's OBS scene renders the same after an upgrade.
DEFAULTS: dict[str, Any] = {
    "preset": "",
    "stream": "translation",
    "lang": "",            # a target name (SPANISH); empty follows the first live target
    "lang2": "",           # second feed when stream=both; empty is the original speech
    "lines": 2,
    "font": 54,
    "w": 1920,
    "h": 1080,
    "hold": 4000,
    "safe": 5.0,
    "plate": 0.55,
    "matte": False,
    "bg": "transparent",
    "pos": "bottom",
    "align": "center",
    "maxw": 88,
    "family": "sans",
    "edge": "plate",
    "color": "FFFFFF",
    "platec": "000000",
    "mode": "rollup",
    "finals": False,
    "labels": True,        # language names over each feed when two are stacked
}

_INTS = {"lines": (1, 12), "font": (8, 400), "w": (16, 8192), "h": (16, 8192), "hold": (0, 600_000),
         "maxw": (20, 100)}
_FLOATS = {"safe": (0.0, 45.0), "plate": (0.0, 1.0)}
_ENUMS = {"stream": STREAMS, "pos": POSITIONS, "align": ALIGNS, "family": FAMILIES, "edge": EDGES,
          "mode": MODES}
_BOOLS = ("matte", "finals", "labels")
_COLOURS = ("color", "platec")

# Layout presets: the four starting points the builder offers. A preset is only
# defaults; anything set alongside it wins.
PRESETS: dict[str, dict[str, Any]] = {
    "lower": {"label": "Lower third", "pos": "bottom", "align": "center", "lines": 2, "font": 54, "maxw": 80,
              "edge": "plate", "plate": 0.6},
    "top": {"label": "Top strip", "pos": "top", "align": "center", "lines": 1, "font": 46, "maxw": 100,
            "edge": "plate", "plate": 0.75},
    "slate": {"label": "Accessibility slate", "pos": "middle", "align": "left", "lines": 6, "font": 84,
              "maxw": 100, "edge": "none", "plate": 0.0, "family": "hyper", "bg": "#000000", "hold": 0},
    "dual": {"label": "Two languages", "stream": "both", "pos": "bottom", "align": "center", "lines": 1,
             "font": 44, "maxw": 85, "edge": "plate", "plate": 0.6},
}

# Each language's own name, for the labels over two stacked feeds: a viewer
# reading Spanish looks for "Español", not "Spanish".
NATIVE = {
    "en": "English", "es": "Español", "pt": "Português", "fr": "Français", "de": "Deutsch", "it": "Italiano",
    "ru": "Русский", "zh": "中文", "ja": "日本語", "ko": "한국어", "hi": "हिन्दी", "id": "Bahasa Indonesia",
    "vi": "Tiếng Việt", "ar": "العربية", "ne": "नेपाली", "te": "తెలుగు", "hmn": "Hmoob",
}

# What the builder's preview plays (`/overlay?demo=1`), by ISO code; a language
# not listed falls back to English. A sermon opening, so the preview shows what
# a Sunday looks like rather than lorem ipsum.
SAMPLES = {
    "en": ["Good morning, church. It is so good to be together.", "Open your Bibles with me to John chapter fifteen.",
           "Jesus says, I am the vine and you are the branches.", "Whoever abides in me bears much fruit."],
    "es": ["Buenos días, iglesia. Qué bueno estar juntos.", "Abran sus Biblias conmigo en Juan capítulo quince.",
           "Jesús dice: yo soy la vid y ustedes los pámpanos.", "El que permanece en mí da mucho fruto."],
    "fr": ["Bonjour, église. Quelle joie d’être ensemble.", "Ouvrez vos Bibles avec moi à Jean chapitre quinze.",
           "Jésus dit : je suis le cep, vous êtes les sarments.", "Celui qui demeure en moi porte beaucoup de fruit."],
    "pt": ["Bom dia, igreja. Que bom estarmos juntos.", "Abram suas Bíblias comigo em João capítulo quinze.",
           "Jesus diz: eu sou a videira, vocês são os ramos.", "Quem permanece em mim dá muito fruto."],
    "de": ["Guten Morgen, Gemeinde. Wie schön, dass wir zusammen sind.",
           "Schlagt mit mir Johannes Kapitel fünfzehn auf.",
           "Jesus sagt: Ich bin der Weinstock, ihr seid die Reben.", "Wer in mir bleibt, bringt viel Frucht."],
    "ko": ["좋은 아침입니다, 여러분. 함께하게 되어 기쁩니다.", "요한복음 15장을 함께 펴 주십시오.",
           "예수님은 나는 포도나무요 너희는 가지라고 하십니다.", "내 안에 거하는 자는 열매를 많이 맺습니다."],
    "zh": ["弟兄姊妹早上好，很高兴我们聚在一起。", "请和我一起翻开约翰福音第十五章。",
           "耶稣说：我是葡萄树，你们是枝子。", "常在我里面的，就多结果子。"],
    "vi": ["Chào buổi sáng, Hội Thánh. Thật vui khi được ở bên nhau.", "Hãy mở Kinh Thánh cùng tôi đến Giăng chương mười lăm.",
           "Chúa Giê-xu phán: Ta là cây nho, các ngươi là nhánh.", "Ai ở trong Ta thì kết nhiều quả."],
}

MESSAGES = {
    "name": "Give the overlay a name.",
    "name_long": f"Keep the name to {NAME_MAX} characters.",
    "id": "Use lowercase letters, numbers and hyphens for the link name.",
    "id_taken": "Another overlay already uses that link name.",
}


class OverlayError(ValueError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _clean_one(key: str, raw: Any, targets: Mapping[str, Any]) -> Any:
    """One option checked; None when the value is not acceptable (the caller
    keeps the default)."""
    if raw is None:
        return None
    if key in _INTS:
        lo, hi = _INTS[key]
        try:
            return max(lo, min(hi, int(float(raw))))
        except (TypeError, ValueError, OverflowError):
            return None
    if key in _FLOATS:
        lo, hi = _FLOATS[key]
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return None
        return None if val != val else round(max(lo, min(hi, val)), 3)   # NaN
    if key in _ENUMS:
        val = str(raw).strip().lower()
        return val if val in _ENUMS[key] else None
    if key in _BOOLS:
        return _truthy(raw)
    if key in _COLOURS:
        m = _HEX.match(str(raw).strip())
        return m.group(1).upper() if m else None
    if key in ("lang", "lang2"):
        val = str(raw).strip().upper()
        return val if val == "" or val in targets else None
    if key == "preset":
        val = str(raw).strip().lower()
        return val if val == "" or val in PRESETS else None
    if key == "bg":
        val = str(raw).strip()
        if val == "transparent":
            return val
        m = _BG_HEX.match(val)
        if m and len(m.group(1)) in (3, 4, 6, 8):
            return "#" + m.group(1).upper()
        return val if _BG_NAME.match(val) else None
    return None


def clean(raw: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[str, Any]:
    """Only the recognised, valid options in `raw`, normalised. Unknown keys and
    bad values are dropped."""
    out: dict[str, Any] = {}
    for key in DEFAULTS:
        if key in raw:
            val = _clean_one(key, raw[key], targets)
            if val is not None:
                out[key] = val
    return out


def resolve(raw: Mapping[str, Any], targets: Mapping[str, Any]) -> dict[str, Any]:
    """The full settings for one overlay: defaults, then the preset's, then
    whatever `raw` sets."""
    given = clean(raw, targets)
    preset = PRESETS.get(given.get("preset", ""), {})
    out = dict(DEFAULTS)
    out.update(clean(preset, targets))      # the same checks as anything typed into a URL
    out.update(given)
    if out["mode"] == "popon":
        out["finals"] = True      # pop-on shows finished sentences only, by definition
    return out


def query(settings: Mapping[str, Any]) -> dict[str, str]:
    """`settings` as URL query values, for the builder's long link."""
    out = {}
    for key, val in settings.items():
        if isinstance(val, bool):
            out[key] = "1" if val else "0"
        else:
            out[key] = str(val)
    return out


# ------------------------------------------------------------- named overlays

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:ID_MAX].strip("-") or "overlay"


def validate(payload: Mapping[str, Any], targets: Mapping[str, Any], taken: set[str],
             oid: str | None = None) -> dict[str, Any]:
    """A named overlay from the admin page, checked. `taken` holds the other
    overlays' ids; `oid` is this overlay's current id when it is an edit."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise OverlayError("name")
    if len(name) > NAME_MAX:
        raise OverlayError("name_long")
    wanted = str(payload.get("id") or "").strip().lower() or oid or slug(name)
    if not _ID.match(wanted):
        raise OverlayError("id")
    if wanted in taken:
        if payload.get("id"):
            raise OverlayError("id_taken")
        base, n = wanted[:ID_MAX - 3], 2
        while f"{base}-{n}" in taken:
            n += 1
        wanted = f"{base}-{n}"
    settings = payload.get("settings")
    settings = clean(settings if isinstance(settings, Mapping) else {}, targets)
    return {"id": wanted, "name": name, "settings": settings}


def clean_list(items: Any, targets: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The stored list, with anything malformed (a hand edit of config.json)
    dropped rather than failing startup."""
    out: list[dict[str, Any]] = []
    taken: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        try:
            ov = validate({"id": item.get("id"), "name": item.get("name"), "settings": item.get("settings")},
                          targets, taken)
        except OverlayError:
            continue
        taken.add(ov["id"])
        out.append(ov)
    return out
