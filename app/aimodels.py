"""AI models: which provider and model each AI task uses (#495).

Every AI task goes through the one Vercel AI Gateway key by default. The old OpenAI key stays selectable per task
as a fallback, and is used on its own when a task wants Vercel but only the OpenAI key is saved, so an install
that has only the OpenAI key keeps working and says why.

Tasks that send text to a chat or speech model are kept here, in `ai.tasks.<id>` (`provider`, `model` for Vercel,
`openai_model` for the OpenAI key). Live transcription is not: who transcribes is the transcription backend
(app/transcribe.py, `realtime.backend`), because a module can add its own backend. The AI models page shows both.

The model picker lists Vercel's public catalogue (no key needed) with each model's price, cached for an hour.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass

from . import netsock

log = logging.getLogger("relay.aimodels")

VERCEL = "vercel"
OPENAI = "openai"
PROVIDERS = (VERCEL, OPENAI)

VERCEL_CHAT_URL = "https://ai-gateway.vercel.sh/v1/chat/completions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
CATALOGUE_URL = "https://ai-gateway.vercel.sh/v1/models"
CATALOGUE_TTL_S = 3600.0
CATALOGUE_LIMIT_BYTES = 8_000_000
# The catalogue's model types each task can pick from.
PICKABLE_TYPES = ("language", "transcription", "speech")
MODEL_ID_MAX = 120


@dataclass(frozen=True)
class Task:
    """One AI task: what it is called, which kind of model it takes and what it starts on."""

    id: str
    label: str
    kind: str                  # a catalogue type: "language" or "speech"
    model: str                 # Vercel model id
    openai_model: str          # the model used through the OpenAI key
    note: str = ""


TASKS: tuple[Task, ...] = (
    Task("translation", "Translation", "language", "openai/gpt-6-luna", "gpt-6-luna",
         "Each finished caption line, into every language that is on."),
    Task("command_center", "Command Center chat", "language", "anthropic/claude-sonnet-5.5", "gpt-6-luna",
         "The chat page that drives Relay (#494). Not built yet: saved for when it is."),
    Task("summaries", "Summaries and notes", "language", "openai/gpt-6-luna", "gpt-6-luna",
         "The post-service pack's summary and notes (#143). Not built yet: saved for when it is."),
    Task("speech", "Spoken translation", "speech", "openai/tts-1", "tts-1",
         "Text to speech per language (#256). Not built yet: saved for when it is."),
)
TASK_IDS = tuple(t.id for t in TASKS)

KEY_SETTING = {VERCEL: "vercel_api_key", OPENAI: "openai_api_key"}
PROVIDER_LABEL = {VERCEL: "Vercel AI Gateway", OPENAI: "OpenAI key"}


def task(task_id: str) -> Task:
    for t in TASKS:
        if t.id == task_id:
            return t
    raise KeyError(task_id)


def defaults() -> dict:
    return {"tasks": {t.id: {"provider": VERCEL, "model": t.model, "openai_model": t.openai_model} for t in TASKS}}


def _model_id(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MODEL_ID_MAX or any(c.isspace() for c in text):
        return fallback
    return text


def normalize(raw: object, legacy_text_model: str = "") -> dict:
    """`ai` as saved: every task present, providers known, model ids plain. A config written before #495 named
    the translation model in `realtime.text_model` (an OpenAI id): it becomes the translation's OpenAI model, and
    its Vercel id when it is not the old default."""
    src = raw if isinstance(raw, dict) else {}
    saved_tasks = src.get("tasks") if isinstance(src.get("tasks"), dict) else {}
    out = {}
    for t in TASKS:
        cur = saved_tasks.get(t.id) if isinstance(saved_tasks.get(t.id), dict) else {}
        openai_default, vercel_default = t.openai_model, t.model
        if t.id == "translation" and legacy_text_model and not cur:
            openai_default = legacy_text_model
            if legacy_text_model != t.openai_model and "/" not in legacy_text_model:
                vercel_default = f"openai/{legacy_text_model}"
        provider = cur.get("provider") if cur.get("provider") in PROVIDERS else VERCEL
        out[t.id] = {"provider": provider, "model": _model_id(cur.get("model"), vercel_default),
                     "openai_model": _model_id(cur.get("openai_model"), openai_default)}
    return {"tasks": out}


@dataclass(frozen=True)
class Route:
    """Where one task's requests go right now, and why that is not what the operator chose (None: it is)."""

    task: str
    provider: str
    model: str
    key: str
    url: str
    warning: str | None = None

    @property
    def label(self) -> str:
        return PROVIDER_LABEL[self.provider]


def _key(cfg: dict, provider: str) -> str:
    return str(cfg.get(KEY_SETTING[provider]) or "").strip()


def _route(t: Task, saved: dict, cfg: dict, provider: str, warning: str | None = None) -> Route:
    if provider == VERCEL:
        return Route(t.id, VERCEL, saved["model"], _key(cfg, VERCEL), VERCEL_CHAT_URL, warning)
    return Route(t.id, OPENAI, saved["openai_model"], _key(cfg, OPENAI), OPENAI_CHAT_URL, warning)


def resolve(cfg: dict, task_id: str) -> Route:
    """The provider a task runs on. Vercel when chosen and its key is saved; when it is chosen but only the OpenAI
    key is saved, the OpenAI key with a warning. A task switched to the OpenAI key stays on it. With no usable key
    the chosen provider comes back with an empty key, so the caller can say which key is missing."""
    t = task(task_id)
    saved = normalize(cfg.get("ai"), str((cfg.get("realtime") or {}).get("text_model") or ""))["tasks"][t.id]
    wanted = saved["provider"]
    if _key(cfg, wanted) or wanted == OPENAI:
        return _route(t, saved, cfg, wanted)
    if _key(cfg, OPENAI):
        warning = f"{t.label} is set to the Vercel AI Gateway, but its key is not saved, so the OpenAI key is used."
        log.warning(warning)
        return _route(t, saved, cfg, OPENAI, warning)
    return _route(t, saved, cfg, wanted)


def routes(cfg: dict) -> list[dict]:
    """Every task with what it is set to and what it runs on, for the AI models page."""
    ai = normalize(cfg.get("ai"), str((cfg.get("realtime") or {}).get("text_model") or ""))["tasks"]
    out = []
    for t in TASKS:
        r = resolve(cfg, t.id)
        out.append({"id": t.id, "label": t.label, "kind": t.kind, "note": t.note, **ai[t.id],
                    "running_provider": r.provider, "running_model": r.model, "has_key": bool(r.key),
                    "warning": r.warning})
    return out


# ------------------------------------------------------------------ the Vercel catalogue

def _num(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _money(value: float) -> str:
    """Dollars for a picker: cents above a dime, else up to three places ($0.05, $0.005). Rounded first, since a
    per-token price times a million lands just under the figure (0.0000001 * 1e6 is 0.0999...)."""
    value = round(value, 6)
    if value == 0:
        return "$0"
    return f"${value:.2f}" if value >= 0.1 else "$" + f"{value:.3f}".rstrip("0").rstrip(".")


def price(kind: str, pricing: object) -> tuple[str, bool | None]:
    """A short price for a picker ("$0.10 in, $0.50 out per 1M tokens"), and whether the model is free (None: the
    catalogue lists no price)."""
    flat = {k: _num(v) for k, v in (pricing or {}).items() if isinstance(v, (str, int, float))} \
        if isinstance(pricing, dict) else {}
    flat = {k: v for k, v in flat.items() if v is not None}
    if not flat:
        return "price not listed", None
    if all(v == 0 for v in flat.values()):
        return "free", True
    per_second = flat.get("transcription_duration_cost_per_second") or flat.get("speech_output_cost_per_second")
    if per_second:
        return f"{_money(per_second * 3600)} per hour of audio", False
    if flat.get("speech_input_character_cost"):
        return f"{_money(flat['speech_input_character_cost'] * 1e6)} per 1M characters", False
    tin, tout = flat.get("input"), flat.get("output")
    if tin is not None and tout is not None:
        return f"{_money(tin * 1e6)} in, {_money(tout * 1e6)} out per 1M tokens", False
    if tin is not None:
        return f"{_money(tin * 1e6)} per 1M tokens", False
    return "price not listed", None


def trim(models: list) -> list[dict]:
    """The catalogue reduced to what a picker shows, for the types a task can take, cheapest-free first."""
    out = []
    for m in models:
        if not isinstance(m, dict) or m.get("type") not in PICKABLE_TYPES or not isinstance(m.get("id"), str):
            continue
        label, free = price(m["type"], m.get("pricing"))
        tags = m.get("tags") if isinstance(m.get("tags"), list) else []
        if "free" in tags:
            free = True
        out.append({"id": m["id"][:MODEL_ID_MAX], "name": str(m.get("name") or m["id"])[:80], "type": m["type"],
                    "price": "free" if free else label, "free": bool(free),
                    "streaming": m["type"] == "transcription" and m["id"].endswith(("-realtime", "-streaming", "-live"))})
    return sorted(out, key=lambda x: (x["type"], not x["free"], x["id"]))


class Catalogue:
    """Vercel's model list, fetched when asked for and kept for an hour. A failed fetch keeps the last list."""

    def __init__(self, url: str = CATALOGUE_URL, ttl_s: float = CATALOGUE_TTL_S):
        self.url, self.ttl_s = url, ttl_s
        self._lock = threading.Lock()
        self._models: list[dict] = []
        self._at = 0.0
        self.error: str | None = None

    def get(self, refresh: bool = False) -> dict:
        """Blocking: run it in a thread."""
        with self._lock:
            if refresh or not self._models or time.monotonic() - self._at > self.ttl_s:
                self._fetch()
            return {"models": list(self._models), "error": self.error}

    def _fetch(self) -> None:
        try:
            status, body = netsock.fetch("translation", self.url, headers={"Accept": "application/json"},
                                         timeout=15.0, limit=CATALOGUE_LIMIT_BYTES)
            if status != 200:
                raise ValueError(f"HTTP {status}")
            data = json.loads(body)
            models = trim(data.get("data") if isinstance(data, dict) else [])
            if not models:
                raise ValueError("empty list")
        except (OSError, ValueError) as exc:
            log.warning("Vercel model list could not be read: %s", exc)
            self.error = "Vercel's model list could not be read, so only the saved models are offered."
            return
        self._models, self._at, self.error = models, time.monotonic(), None


catalogue = Catalogue()
