"""Server-side config: load, validate, atomic save, hot-reload.

The file holds the OpenAI key, so it is written 0600 and git-ignored. It is
never serialised to a browser in full -- see `redacted()`.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from . import languages
from . import redact
from . import schedules

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("RELAY_CONFIG", ROOT / "config.json"))
# Container images mount a writable state volume and keep /app read-only, so
# the blocklist has to live outside the code tree. Same escape hatch as
# RELAY_CONFIG: when set it wins over the `blocklist_file` config key.
BLOCKLIST_ENV = os.environ.get("RELAY_BLOCKLIST") or ""
# Same escape hatch for the recordings directory: the container keeps /app
# read-only, so a recorded event has to land on the mounted state volume.
RECORDINGS_ENV = os.environ.get("RELAY_RECORDINGS") or ""
EXAMPLE_PATH = ROOT / "config.example.json"

DEFAULTS: dict[str, Any] = {
    "openai_api_key": "",
    "admin_token": "",
    "audio_device": None,
    "input_channel": 0,
    "source_language": "ENGLISH",
    "targets": {"SPANISH": {"enabled": True}},
    "default_font_px": 40,
    "speaking_indicator_vad": True,
    "blocklist_file": "blocklist.txt",
    "history_lines": 40,
    # Session recording, for the post-event transcript and the paired
    # fine-tuning export. Off by default: writing every word spoken in a room
    # to disk is the operator's call, not a default we make for them. Lines
    # are recorded after the blocklist runs, so a blocked term never lands.
    "recording": {
        "enabled": False,
        "dir": "recordings",
        # Oldest runs beyond this are deleted when a new one starts, so a
        # machine left running a season of events cannot fill its disk.
        "keep_runs": 20,
    },
    # Scheduled start/stop windows -- see schedules.py for the shape. Empty by
    # default: nothing starts on its own until the operator sets a schedule.
    "schedules": [],
    "host": "0.0.0.0",
    "admin_host": "127.0.0.1",
    "port": 8000,
    "admin_port": 8001,
    # Optional hostname for the operator panel certificate, asked for by
    # setup and read by tools/setup_caddy.py. Blank certifies the IP only.
    "admin_fqdn": "",
    "realtime": {
        "translate_model": "gpt-realtime-translate",
        # The source-language transcript is opt-in: the translations endpoint only
        # emits the input transcript when an input transcription model is set. An
        # empty string turns the source (e.g. English) pane off.
        # All of these are accepted here; the figure is the account TPM ceiling,
        # and continuous audio makes that the binding constraint:
        #   gpt-live-transcribe    60k   built for streaming -- the default
        #   gpt-transcribe        200k   most headroom
        #   gpt-realtime-whisper   60k
        #   gpt-4o-mini-transcribe 50k
        #   gpt-4o-transcribe      10k   lowest ceiling of the set; avoid
        "transcribe_model": "gpt-live-transcribe",
        # Segment timing for the committed caption lines. A line is committed
        # once its deltas have been quiet for segment_idle_s AND it ends on a
        # sentence boundary; segment_max_idle_s is the backstop that commits a
        # line with no sentence end (always at a word boundary). Keep the
        # backstop generous: in-progress text is already on screen as it
        # streams, so an early commit only splits sentences -- a stall in the
        # transcription stream is not a speaker pause.
        "segment_idle_s": 1.0,
        "segment_max_idle_s": 10.0,
        # Send only audio around detected speech. The transcription model
        # hallucinates text from a room's noise floor when fed continuous
        # silence; set false to restore the old always-send behaviour.
        "gate_silence": True,
        # No turn-detection settings: the translations endpoint has no turn
        # lifecycle and rejects `turn_detection` outright, so semantic_vad,
        # server_vad and the ms thresholds have no meaning here. Line breaks are
        # steered by segment_idle_s / segment_max_idle_s above. See the
        # comment in realtime.py's _session_update().
        # Input noise reduction, applied *before* VAD. null = off (the default,
        # for an already-conditioned board feed); "far_field" for a room or
        # laptop mic; "near_field" for a headset or lavalier.
        "noise_reduction": None,
    },
}

_lock = threading.RLock()
_data: dict[str, Any] = {}
_mtime: float = 0.0


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalise(cfg: dict) -> dict:
    cfg = _merge(DEFAULTS, cfg)
    if cfg.get("source_language") not in languages.SOURCE_LANGUAGES:
        cfg["source_language"] = "ENGLISH"
    # gpt-realtime-translate takes no dialect or register setting, so a target
    # is just on or off. A stale "variant" from an older config is dropped here.
    allowed = languages.target_names(cfg["source_language"])
    targets = {}
    for name, spec in (cfg.get("targets") or {}).items():
        name = name.upper()
        if name not in allowed:
            continue
        targets[name] = {"enabled": bool((spec or {}).get("enabled"))}
    for name in allowed:
        targets.setdefault(name, {"enabled": False})
    cfg["targets"] = targets
    cfg["default_font_px"] = max(16, min(160, int(cfg.get("default_font_px") or 40)))
    cfg["history_lines"] = max(1, min(500, int(cfg.get("history_lines") or 40)))
    # A channel index, or the string "mix" to average every input channel --
    # a stereo board feed is usually better summed than halved.
    _ch = cfg.get("input_channel", 0)
    if isinstance(_ch, str) and _ch.strip().lower() == "mix":
        cfg["input_channel"] = "mix"
    else:
        try:
            cfg["input_channel"] = max(0, int(_ch or 0))
        except (TypeError, ValueError):
            cfg["input_channel"] = 0
    # Viewers get `port`; the operator panel is served on `admin_port` so the
    # link handed to a room never reaches the panel. Setting them equal puts
    # everything back on one port.
    cfg["port"] = int(cfg.get("port") or 8000)
    cfg["admin_port"] = int(cfg.get("admin_port") or cfg["port"])
    cfg["admin_fqdn"] = str(cfg.get("admin_fqdn") or "").strip().strip(".")
    # `host` is where the viewer link listens -- every interface, because the
    # room has to reach it. The panel reads and writes the OpenAI key and the
    # admin token, so it binds loopback by default and is reached from another
    # machine through the Caddy front end in the same container, which
    # terminates TLS for it. Setting `admin_host` to "0.0.0.0" puts the panel
    # back on the network in cleartext -- never the default, and it would only
    # be reachable at all if 8001 were also published.
    cfg["host"] = (str(cfg.get("host") or "").strip() or "0.0.0.0")
    # Not rewritten when admin_port == port: in single-port mode run.py binds
    # one socket on `host` and never looks at admin_host, and collapsing the
    # stored value here would mean a round trip through single-port mode left
    # the panel silently on 0.0.0.0 afterwards.
    cfg["admin_host"] = (str(cfg.get("admin_host") or "").strip() or "127.0.0.1")
    # The relay moved to the translations endpoint, which only serves
    # gpt-realtime-translate. A config written before that still names the old
    # conversational model; point it at the right one instead of failing to
    # connect. transcribe_model is gone -- one session now carries both feeds.
    rt = cfg.get("realtime") or {}
    if rt.get("translate_model") in (None, "", "gpt-realtime", "gpt-realtime-mini"):
        rt["translate_model"] = DEFAULTS["realtime"]["translate_model"]
    # transcribe_model is back: it enables the source-language transcript on the
    # same session. Missing key -> default it on; an explicit empty/null -> off.
    rt["gate_silence"] = bool(rt.get("gate_silence", True))
    rt["segment_idle_s"] = max(0.2, min(10.0, float(rt.get("segment_idle_s") or 1.0)))
    rt["segment_max_idle_s"] = max(
        rt["segment_idle_s"], min(60.0, float(rt.get("segment_max_idle_s") or 10.0))
    )
    if "transcribe_model" not in rt:
        rt["transcribe_model"] = DEFAULTS["realtime"]["transcribe_model"]
    elif rt["transcribe_model"] in (None, ""):
        rt["transcribe_model"] = ""
    # Turn-detection settings were written by an earlier version that believed
    # the translations endpoint honoured them. It does not, so they are dropped
    # here rather than left in the file looking like live controls -- the same
    # treatment the dialect "variant" got.
    for _dead in ("vad_mode", "vad_eagerness", "vad_threshold",
                  "vad_prefix_padding_ms", "vad_silence_duration_ms"):
        rt.pop(_dead, None)
    # An empty string from the panel's default option means "off".
    if rt.get("noise_reduction") not in ("near_field", "far_field"):
        rt["noise_reduction"] = None
    cfg["realtime"] = rt
    rec = cfg.get("recording") or {}
    rec = {
        "enabled": bool(rec.get("enabled", False)),
        "dir": str(rec.get("dir") or "recordings").strip() or "recordings",
        "keep_runs": max(1, min(500, int(rec.get("keep_runs") or 20))),
    }
    cfg["recording"] = rec
    # A hand-edited schedule that does not validate is dropped, not fatal.
    cfg["schedules"] = schedules.clean_list(cfg.get("schedules"))
    # The blocklist lives in its own file; config.json only points at it.
    cfg.pop("blocklist", None)
    cfg["blocklist_file"] = str(cfg.get("blocklist_file") or "blocklist.txt")
    return cfg


def load() -> dict:
    global _data, _mtime
    with _lock:
        raw = {}
        _mtime = CONFIG_PATH.stat().st_mtime if CONFIG_PATH.exists() else 0.0
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"config.json is not valid JSON: {exc}") from exc
        _migrate_blocklist(raw)
        _data = _normalise(raw)
        return _data


def blocklist_path() -> Path:
    """Absolute path of the blocklist file (relative entries resolve to ROOT)."""
    p = Path(BLOCKLIST_ENV or get().get("blocklist_file") or "blocklist.txt")
    return p if p.is_absolute() else ROOT / p


def recordings_path() -> Path:
    """Absolute path of the recordings directory (relative entries resolve to
    ROOT). RELAY_RECORDINGS wins over the config key, as with the blocklist."""
    p = Path(RECORDINGS_ENV or get()["recording"]["dir"] or "recordings")
    return p if p.is_absolute() else ROOT / p


def _migrate_blocklist(raw: dict) -> None:
    """Carry a pre-file `blocklist` array in config.json over to the file."""
    legacy = raw.get("blocklist")
    if not legacy:
        return
    target = Path(BLOCKLIST_ENV or raw.get("blocklist_file") or "blocklist.txt")
    if not target.is_absolute():
        target = ROOT / target
    if not target.exists():
        redact.write_blocklist_file(target, legacy)


def ensure_blocklist_file() -> Path:
    """Create the blocklist file with its format header if it is missing."""
    path = blocklist_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(redact.FILE_HEADER, encoding="utf-8")
    return path


def get() -> dict:
    """Live config. Re-reads the file if it changed on disk (hot-reload).

    Hand-editing config.json is the documented way to set the first admin
    token, so that edit has to take effect without restarting the server.
    """
    with _lock:
        if not _data:
            return load()
        try:
            if CONFIG_PATH.exists() and CONFIG_PATH.stat().st_mtime != _mtime:
                return load()
        except OSError:
            pass
        return _data


def save(updates: dict) -> dict:
    """Merge `updates` into the live config and persist it atomically."""
    global _data
    with _lock:
        merged = _normalise(_merge(get(), updates))
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG_PATH)
        globals()["_mtime"] = CONFIG_PATH.stat().st_mtime
        _data = merged
        return _data


def redacted() -> dict:
    """Config safe to hand to the admin browser: no key, no token."""
    cfg = dict(get())
    key = cfg.pop("openai_api_key", "") or ""
    cfg.pop("admin_token", None)
    cfg["openai_api_key_set"] = bool(key)
    cfg["openai_api_key_hint"] = f"…{key[-4:]}" if len(key) >= 4 else ""
    return cfg


def ensure_file() -> Path:
    """Create config.json from the example on first run."""
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            EXAMPLE_PATH.read_text(encoding="utf-8")
            if EXAMPLE_PATH.exists()
            else json.dumps(DEFAULTS, indent=2),
            encoding="utf-8",
        )
        os.chmod(CONFIG_PATH, 0o600)
    else:
        os.chmod(CONFIG_PATH, 0o600)
    return CONFIG_PATH
