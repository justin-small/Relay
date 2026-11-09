"""Source and target language definitions.

The relay runs on `gpt-realtime-translate`, a purpose-built streaming
interpreter. It exposes exactly one knob -- the output language -- and no
prompting, register or dialect control, so a target is just a label and an ISO
code. (The dialect system that steered the old `gpt-realtime` + instructions
path was removed along with it.)
"""

# Writing systems a language can be drawn in. Clients with a limited font (the
# touch-screen panels) hide any language whose script they cannot render.
SCRIPTS = ("latin", "cyrillic", "han", "kana", "hangul", "devanagari", "arabic", "telugu")

SOURCE_LANGUAGES = {
    "ENGLISH": {"label": "English", "iso": "en", "script": "latin"},
}

# The 13 output languages gpt-realtime-translate can produce, then the ones only
# the text translation mode can (`text_only`): a chat model translates into
# them, so `name` is what the request calls the language -- spelled out where
# the bare label is ambiguous. Input is far wider -- 70+ languages -- but this
# app only ever sends one source language.
TARGETS = {
    "SPANISH": {"label": "Spanish", "iso": "es", "script": "latin"},
    "PORTUGUESE": {"label": "Portuguese", "iso": "pt", "script": "latin"},
    "FRENCH": {"label": "French", "iso": "fr", "script": "latin"},
    "GERMAN": {"label": "German", "iso": "de", "script": "latin"},
    "ITALIAN": {"label": "Italian", "iso": "it", "script": "latin"},
    "RUSSIAN": {"label": "Russian", "iso": "ru", "script": "cyrillic"},
    "CHINESE": {"label": "Chinese", "iso": "zh", "script": "han"},
    "JAPANESE": {"label": "Japanese", "iso": "ja", "script": "kana"},
    "KOREAN": {"label": "Korean", "iso": "ko", "script": "hangul"},
    "HINDI": {"label": "Hindi", "iso": "hi", "script": "devanagari"},
    "INDONESIAN": {"label": "Indonesian", "iso": "id", "script": "latin"},
    "VIETNAMESE": {"label": "Vietnamese", "iso": "vi", "script": "latin"},
    "ENGLISH": {"label": "English", "iso": "en", "script": "latin"},
    "ARABIC": {"label": "Arabic", "iso": "ar", "script": "arabic", "text_only": True,
               "name": "Modern Standard Arabic"},
    "NEPALI": {"label": "Nepali", "iso": "ne", "script": "devanagari", "text_only": True,
               "name": "Nepali, in Devanagari script"},
    "TELUGU": {"label": "Telugu", "iso": "te", "script": "telugu", "text_only": True,
               "name": "Telugu, in Telugu script"},
    # No ISO 639-1 code; hmn is the ISO 639-2 code for the Hmong group.
    "HMONG": {"label": "Hmong", "iso": "hmn", "script": "latin", "text_only": True,
              "name": "White Hmong (Hmoob Dawb), in the Romanized Popular Alphabet"},
}


def text_only(target: str) -> bool:
    """True for a target only the text translation mode can produce."""
    return bool(TARGETS.get(target, {}).get("text_only"))


def target_prompt_name(target: str) -> str:
    spec = TARGETS.get(target) or {}
    return spec.get("name") or spec.get("label") or target.title()


def target_names(source: str | None = None) -> list[str]:
    """Every target the operator may enable, minus the source language.

    Translating English into English would open a billed session to no effect,
    so the source language is never offered as a target.
    """
    return [name for name in TARGETS if name != (source or "").upper()]


def target_label(target: str) -> str:
    spec = TARGETS.get(target)
    return spec["label"] if spec else target.title()


def target_iso(target: str) -> str:
    spec = TARGETS.get(target)
    return spec["iso"] if spec else target.lower()[:2]


def target_script(target: str) -> str:
    spec = TARGETS.get(target)
    return spec["script"] if spec else "latin"
