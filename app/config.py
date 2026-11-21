"""Server-side config: load, validate, atomic save, hot-reload.

The file holds the OpenAI key, so it is written 0600 and git-ignored. It is
never serialised to a browser in full -- see `redacted()`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from . import languages
from . import boards, overlays
from . import paths
from . import redact
from . import schedules
from . import secretbox

ROOT = Path(__file__).resolve().parent.parent
# The per-user state folder (app/paths.py): config.json, relay.db, blocklist.txt, certs/, backups/,
# recordings/, panel-firmware/. ROOT is where the code and its packaged data are, never written to.
CONFIG_PATH = paths.config_path()
STATE_DIR = CONFIG_PATH.parent
# Container images mount a writable state volume and keep /app read-only, so
# the blocklist has to live outside the code tree. Same escape hatch as
# RELAY_CONFIG: when set it wins over the `blocklist_file` config key.
BLOCKLIST_ENV = os.environ.get("RELAY_BLOCKLIST") or ""
# Same escape hatch for the recordings directory: the container keeps /app
# read-only, so a recorded event has to land on the mounted state volume.
RECORDINGS_ENV = os.environ.get("RELAY_RECORDINGS") or ""
log = logging.getLogger("relay.config")
EXAMPLE_PATH = ROOT / "config.example.json"

DEFAULTS: dict[str, Any] = {
    "openai_api_key": "",
    # The one Vercel AI Gateway key (#495): every AI task goes through it unless set to the OpenAI key.
    "vercel_api_key": "",
    # Which provider and model each AI task uses (app/aimodels.py); filled in by normalize.
    "ai": {},
    "admin_token": "",
    # The owner's backup passphrase, as the key derived from it (app/backup.py); never the passphrase itself.
    "backup_key": "",
    # Which bus lane (1 to 32) the captions listen to. The input itself is patched to that lane on the Audio bus page.
    "audio_lane": 1,
    # The 32-lane audio bus (app/audiobus.py): inputs patched to lanes, a crosspoint matrix, outputs. Always on.
    "audio_bus": {"lanes": [], "outputs": [], "matrix": []},
    "mixreport": {"enabled": False, "left": 1, "right": 2, "silence_dbfs": -50.0, "target_lufs": -16.0,
                  "tolerance_lu": 2.0, "peak_limit_dbfs": -1.0, "rehearsal": False},
    "metering": {"enabled": False, "lane": 1, "kind": "room", "calibration_db": 110.0, "calibrated": False,
                 "warn_db": 85.0, "limit_db": 95.0},
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
    # The one Relay time zone (#526): signage, Planning Center, YouTube, call times and every schedule use it.
    # Empty means "not chosen yet": load fills it from the host's TZ (or the zone an older schedule carried).
    "timezone": "",
    # Named schedules -- see schedules.py for the shape. Empty by default: nothing starts on its own until the
    # operator sets a schedule and picks it for the engine.
    "schedules": [],
    # (`engine_schedules`, the refs "schedule:relay:<id>" that start and stop the engine, is not defaulted here: a
    # config from before #526 has none, and then every schedule drove the engine, which load keeps.)
    # Named broadcast overlays (/overlay/<id>) -- see overlays.py for the shape.
    "overlays": [],
    # Boards (/board/<slug>, #156): screens built from widgets -- see boards.py for the shape.
    "boards": [],
    # Caption panels the operator has named or given a feed, by panel id --
    # see panels.py. Panels themselves are never written here by a check-in.
    "panels": {},
    # Alert buttons (#121): what each flashes, and how long an alert flashes when nobody taps -- see alerts.py.
    "alerts": {"auto_stop_s": 120, "buttons": []},
    # Which modules are switched on, by module id: {"<id>": {"enabled": bool}} -- see app/modules/. A
    # module with no switch saved is on only if its integration was already set up (Module.configured).
    "modules": {},
    # Which network port each kind of traffic uses, and the ports Relay has seen -- see netroute.py. No route
    # means the system default, as before routes existed.
    "network": {"routes": {}, "policy": {}},
    # What the operator adds to the network map: a name Relay shows for a device, notes on devices and switch ports,
    # and the closets the switches hang in -- see network_map.py. Nothing here is sent to a device.
    "network_map": {"names": {}, "notes": {}, "places": []},
    "host": "0.0.0.0",
    # The operator panel is HTTPS only (app/tls.py), so it can face the network: 443 on every interface.
    # Viewers get plain HTTP on 80. If either cannot be opened Relay uses 8080 / 8443 (app/serving.py).
    "admin_host": "0.0.0.0",
    "port": 80,
    "admin_port": 443,
    # Optional hostname for the operator panel certificate (app/tls.py), on top of this machine's own
    # addresses and host name. Blank certifies those alone.
    "admin_fqdn": "",
    "realtime": {
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
        # Who transcribes the main feed (app/transcribe.py): "scribe" (Vercel) or "openai", or the id of a
        # backend a module registers. One that is unknown, or whose key is not saved, falls back to OpenAI.
        "backend": "scribe",
        # The Vercel model the scribe backend streams to (a streaming transcription model in Vercel's list).
        "scribe_model": "elevenlabs/scribe-v2-realtime",
        # Sent as reasoning_effort when non-empty. "none" keeps a reasoning
        # model from thinking before each line, which only adds delay here;
        # blank omits it, for a model that does not take the parameter.
        "text_reasoning_effort": "none",
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
# Secrets kept sealed in config.json (app/secretbox.py, #278) and held as plain text only in memory. Modules add
# their own with Module.secret_settings. The backup passphrase key is core's (app/backup.py).
SECRET_KEYS = ("openai_api_key", "vercel_api_key", "admin_token", "backup_key")
# Sealed values this computer cannot open (its key was lost), kept as stored until a new value replaces them, so a
# keyring that is locked for one start does not wipe them on the next save.
_unopened: dict[str, str] = {}


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
    from . import audiobus  # here, not at the top: it loads the sound library, which config must not need
    audiobus.migrate_legacy_input(cfg)
    try:
        cfg["audio_lane"] = max(1, min(audiobus.LANES, int(cfg.get("audio_lane") or 1)))
    except (TypeError, ValueError):
        cfg["audio_lane"] = 1
    for gone in ("audio_source", "audio_device", "input_channel"):   # the capture's own input, before it read a lane
        cfg.pop(gone, None)
    cfg["audio_bus"] = audiobus.normalize(cfg.get("audio_bus"))
    from . import metering  # the same reason: it imports the bus
    cfg["metering"] = metering.normalize(cfg.get("metering"))
    from . import mixreport  # the same reason
    cfg["mixreport"] = mixreport.normalize(cfg.get("mixreport"))
    # Viewers get `port`; the operator panel is served on `admin_port`, over HTTPS only, so the link handed
    # to a room never reaches the panel. They cannot be the same port: that would serve the panel where
    # viewers are served, in cleartext.
    cfg["port"] = int(cfg.get("port") or 80)
    cfg["admin_port"] = int(cfg.get("admin_port") or 443)
    if cfg["admin_port"] == cfg["port"]:
        cfg["admin_port"] = 8443 if cfg["port"] == 443 else 443
    cfg["admin_fqdn"] = str(cfg.get("admin_fqdn") or "").strip().strip(".")
    # `host` is where the viewer link listens -- every interface, because the room has to reach it.
    # `admin_host` is where the HTTPS panel listens; set it to "127.0.0.1" to keep the panel off the network.
    cfg["host"] = (str(cfg.get("host") or "").strip() or "0.0.0.0")
    cfg["admin_host"] = (str(cfg.get("admin_host") or "").strip() or "0.0.0.0")
    rt = cfg.get("realtime") or {}
    # The realtime translation engine (one gpt-realtime-translate session per language) is gone (#496): text
    # translation is the only engine, so a config that still names it runs as text mode.
    if rt.pop("translation_mode", None) == "realtime":
        log.info("translation_mode \"realtime\" is no longer offered; translating with the text engine")
    rt.pop("translate_model", None)
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
    rt["backend"] = str(rt.get("backend") or DEFAULTS["realtime"]["backend"]).strip()[:40]
    rt["scribe_model"] = str(rt.get("scribe_model") or "").strip()[:120] or DEFAULTS["realtime"]["scribe_model"]
    # The translation model moved to ai.tasks.translation (#495); an older config's choice is carried over once.
    from . import aimodels  # here, not at the top: it imports the network helpers, which read config
    cfg["ai"] = aimodels.normalize(cfg.get("ai"), str(rt.pop("text_model", "") or "").strip())
    effort = rt.get("text_reasoning_effort", DEFAULTS["realtime"]["text_reasoning_effort"])
    rt["text_reasoning_effort"] = str(effort or "").strip()
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
    # The one Relay time zone: an older config kept a zone per schedule and one for signage; carry the first over.
    legacy = schedules.legacy_zone(cfg.get("schedules")) or (cfg.get("signage") or {}).get("timezone")
    cfg["timezone"] = schedules.clean_zone(cfg.get("timezone") or legacy, os.environ.get("TZ"))
    # A hand-edited schedule that does not validate is dropped, not fatal.
    had_engine = "engine_schedules" in cfg
    cfg["schedules"] = schedules.clean_list(cfg.get("schedules"))
    chosen = cfg.get("engine_schedules") if had_engine and isinstance(cfg.get("engine_schedules"), list) else \
        [schedules.ref(s["id"]) for s in cfg["schedules"]]
    cfg["engine_schedules"] = [r for r in dict.fromkeys(chosen)
                               if schedules.find(cfg["schedules"], r) is not None]
    cfg["overlays"] = overlays.clean_list(cfg.get("overlays"), languages.TARGETS)
    cfg["boards"] = boards.clean_list(cfg.get("boards"))
    # Imported here: panels.py reads config, so the module-level import would loop.
    from . import panels

    cfg["panels"] = panels.clean_stored(cfg.get("panels"))
    from . import alerts

    cfg["alerts"] = alerts.clean_stored(cfg.get("alerts"))
    # Each module's settings and its on/off switch (app/modules/). Imported here for the same reason.
    from .modules import modules

    modules.clean_config(cfg)
    from . import netroute

    cfg["network"] = netroute.clean(cfg.get("network"))
    from . import network_map

    cfg["network_map"] = network_map.clean_stored(cfg.get("network_map"))
    # The blocklist lives in its own file; config.json only points at it.
    cfg.pop("blocklist", None)
    cfg["blocklist_file"] = str(cfg.get("blocklist_file") or "blocklist.txt")
    return cfg


def _secret_fields(cfg: dict):
    """(dict holding it, key, purpose) for every secret in `cfg`, core's and each module's."""
    for key in SECRET_KEYS:
        yield cfg, key, f"config:{key}"
    from .modules import modules

    for settings_key, field in modules.secret_paths():
        sub = cfg.get(settings_key)
        if isinstance(sub, dict):
            yield sub, field, f"config:{settings_key}.{field}"


def _open_secrets(raw: dict) -> bool:
    """Unseal `raw`'s secrets in place. True when one was still stored as plain text."""
    plain = False
    _unopened.clear()
    for box, key, purpose in list(_secret_fields(raw)):
        value = box.get(key)
        if secretbox.is_sealed(value):
            opened = secretbox.unseal(value, purpose)
            if opened is None:
                _unopened[purpose] = value
            box[key] = opened or ""
        elif isinstance(value, str) and value.strip():
            plain = True
    return plain


def _sealed(cfg: dict) -> dict:
    """A copy of `cfg` as it is written to disk: every secret sealed."""
    out = json.loads(json.dumps(cfg))
    for box, key, purpose in list(_secret_fields(out)):
        value = box.get(key)
        if isinstance(value, str) and value:
            box[key] = secretbox.seal(value, purpose)
        elif not value and purpose in _unopened:
            box[key] = _unopened[purpose]
    return out


def _write(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(_sealed(cfg), indent=2, ensure_ascii=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)
    globals()["_mtime"] = CONFIG_PATH.stat().st_mtime


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
        plain = _open_secrets(raw) if isinstance(raw, dict) else False
        _data = _normalise(raw)
        if plain:
            # A secret typed into config.json by hand (the first admin token) works, and is sealed straight away.
            try:
                _write(_data)
            except Exception as exc:          # pragma: no cover - a read-only state folder: it stays as typed
                import logging

                logging.getLogger("relay.config").warning("could not seal the secrets in config.json: %s", exc)
        return _data


def read_raw() -> dict:
    """config.json as stored, before normalising ({} when missing or unreadable)."""
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def blocklist_path() -> Path:
    """Absolute path of the blocklist file (relative entries resolve to the state folder)."""
    p = Path(BLOCKLIST_ENV or get().get("blocklist_file") or "blocklist.txt")
    return p if p.is_absolute() else STATE_DIR / p


def recordings_path() -> Path:
    """Absolute path of the recordings directory (relative entries resolve to
    the state folder). RELAY_RECORDINGS wins over the config key, as with the blocklist."""
    p = Path(RECORDINGS_ENV or get()["recording"]["dir"] or "recordings")
    return p if p.is_absolute() else STATE_DIR / p


def _migrate_blocklist(raw: dict) -> None:
    """Carry a pre-file `blocklist` array in config.json over to the file."""
    legacy = raw.get("blocklist")
    if not legacy:
        return
    target = Path(BLOCKLIST_ENV or raw.get("blocklist_file") or "blocklist.txt")
    if not target.is_absolute():
        target = STATE_DIR / target
    if not target.exists():
        redact.write_blocklist_file(target, legacy)


def ensure_blocklist_file() -> Path:
    """Create the blocklist file if it is missing: a copy of the packaged one, else just the header."""
    path = blocklist_path()
    if not path.exists():
        paths.make_private_dir(path.parent)
        packaged = ROOT / "blocklist.txt"
        if packaged.is_file() and packaged.resolve() != path.resolve():
            path.write_text(packaged.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text(redact.FILE_HEADER, encoding="utf-8")
    return path


def timezone() -> str:
    """The Relay time zone (an IANA name from the US list)."""
    return get()["timezone"]


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


def save(updates: dict, replace: tuple[str, ...] = ()) -> dict:
    """Merge `updates` into the live config and persist it atomically.

    Keys named in `replace` overwrite instead of merging, for a dict whose
    entries have to be removable (the panel list).
    """
    global _data
    with _lock:
        base = dict(get())
        for key in replace:
            if key in updates:
                base.pop(key, None)
        merged = _normalise(_merge(base, updates))
        for _box, key, purpose in list(_secret_fields(updates)):
            if _box.get(key):
                _unopened.pop(purpose, None)          # a new value replaces one that could not be opened
        _write(merged)
        _data = merged
        return _data


def redacted() -> dict:
    """Config safe to hand to the admin browser: no key, no token, and each
    module's settings as its `public()` gives them (no Planning Center secret)."""
    cfg = dict(get())
    key = cfg.pop("openai_api_key", "") or ""
    vkey = cfg.pop("vercel_api_key", "") or ""
    cfg.pop("admin_token", None)
    cfg["backup_passphrase_set"] = bool(cfg.pop("backup_key", ""))
    cfg["secret_store"] = secretbox.where()
    from .modules import modules

    modules.redact_config(cfg)
    cfg["openai_api_key_set"] = bool(key)
    cfg["openai_api_key_hint"] = f"…{key[-4:]}" if len(key) >= 4 else ""
    cfg["vercel_api_key_set"] = bool(vkey)
    cfg["vercel_api_key_hint"] = f"…{vkey[-4:]}" if len(vkey) >= 4 else ""
    return cfg


def ensure_file() -> Path:
    """Create the state folder and config.json from the example on first run."""
    paths.make_private_dir(STATE_DIR)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            EXAMPLE_PATH.read_text(encoding="utf-8")
            if EXAMPLE_PATH.exists()
            else json.dumps(DEFAULTS, indent=2),
            encoding="utf-8",
        )
        paths.restrict(CONFIG_PATH)
    else:
        _tighten(CONFIG_PATH)
    return CONFIG_PATH


def _tighten(path: Path) -> None:
    """Make an existing file 0600, taking ownership of it first if need be.

    chmod needs the caller to own the file. One this process did not create --
    copied in from another machine, which the OS can present as owned
    by someone else -- refuses it, and the relay would crash
    on every start. Rewriting it through a fresh 0600 file and renaming that
    over the original, as save() does, leaves a file this process owns. Where
    even that is refused the error is raised: a secret that cannot be made
    private should stop the start, not be served as it is.
    """
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        tmp = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(path.read_bytes())
            os.replace(tmp, path)
        except BaseException:
            # The copy holds the same secrets; never leave it lying around.
            tmp.unlink(missing_ok=True)
            raise
