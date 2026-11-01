"""Source and target language definitions.

The relay runs on `gpt-realtime-translate`, a purpose-built streaming
interpreter. It exposes exactly one knob -- the output language -- and no
prompting, register or dialect control, so a target is just a label and an ISO
code. (The dialect system that steered the old `gpt-realtime` + instructions
path was removed along with it.)
"""

SOURCE_LANGUAGES = {
    "ENGLISH": {"label": "English", "iso": "en"},
}

# The 13 output languages gpt-realtime-translate can produce, then the ones only
# the text translation mode can (`text_only`): a chat model translates into
# them, so `name` is what the request calls the language -- spelled out where
# the bare label is ambiguous. Input is far wider -- 70+ languages -- but this
# app only ever sends one source language.
TARGETS = {
    "SPANISH": {"label": "Spanish", "iso": "es"},
    "PORTUGUESE": {"label": "Portuguese", "iso": "pt"},
    "FRENCH": {"label": "French", "iso": "fr"},
    "GERMAN": {"label": "German", "iso": "de"},
    "ITALIAN": {"label": "Italian", "iso": "it"},
    "RUSSIAN": {"label": "Russian", "iso": "ru"},
    "CHINESE": {"label": "Chinese", "iso": "zh"},
    "JAPANESE": {"label": "Japanese", "iso": "ja"},
    "KOREAN": {"label": "Korean", "iso": "ko"},
    "HINDI": {"label": "Hindi", "iso": "hi"},
    "INDONESIAN": {"label": "Indonesian", "iso": "id"},
    "VIETNAMESE": {"label": "Vietnamese", "iso": "vi"},
    "ENGLISH": {"label": "English", "iso": "en"},
    "ARABIC": {"label": "Arabic", "iso": "ar", "text_only": True,
               "name": "Modern Standard Arabic"},
    "NEPALI": {"label": "Nepali", "iso": "ne", "text_only": True,
               "name": "Nepali, in Devanagari script"},
    "TELUGU": {"label": "Telugu", "iso": "te", "text_only": True,
               "name": "Telugu, in Telugu script"},
    # No ISO 639-1 code; hmn is the ISO 639-2 code for the Hmong group.
    "HMONG": {"label": "Hmong", "iso": "hmn", "text_only": True,
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
