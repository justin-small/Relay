"""Bring the settings of a v2.0 Docker install of Relay into this computer's state folder.

A Docker install kept everything mutable in `docker-config/` beside its scripts. This reads that folder
(never writes to it) and copies config.json, blocklist.txt, recordings/ and panel-firmware/ into the state
folder, rewriting what only made sense inside the container. It is safe to run twice: a second run over the
same source changes nothing.

Not imported: certs/ (the old certificate is not trusted by anything here; a new one is minted on start) and
the generated Caddyfile. Paired caption panels need nothing: their tokens are in config.json and the mDNS
service name is unchanged.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

ROOT = Path(__file__).resolve().parent.parent

CONTAINER_PREFIX = "/app/config"
# The container's own listening setup: the new install picks its own (app/serving.py), so none is carried.
CONTAINER_ONLY_KEYS = ("host", "admin_host", "port", "admin_port")
FOLDERS = ("recordings", "panel-firmware")

CERT_SENTENCE = ("The old certificate was not imported. Relay made a new one, so each browser that used the "
                 "operator panel warns once more: check the fingerprint shown in Settings matches, then accept it.")


class ImportRefused(Exception):
    """The import cannot go ahead; the message is a sentence for the operator."""


@dataclass
class Report:
    source: str = ""
    copied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.copied)


def find_source(path: str | Path) -> Path:
    """The docker-config folder for `path`: the folder itself, or an old `Relay` folder that holds it."""
    p = Path(path).expanduser()
    for cand in (p, p / "docker-config"):
        if (cand / "config.json").is_file():
            return cand.resolve()
    raise ImportRefused("That folder has no config.json, so it is not a Relay docker-config folder. Pick the "
                        "old Relay folder, or the docker-config folder inside it.")


def _relative(value: str) -> str | None:
    """`/app/config/x` -> `x`; None for any other absolute path (a container path that means nothing here)."""
    if value == CONTAINER_PREFIX:
        return ""
    if value.startswith(CONTAINER_PREFIX + "/"):
        return value[len(CONTAINER_PREFIX) + 1:]
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        return None
    return value


def _rewrite_paths(node: Any) -> Any:
    """Every string under /app/config/ becomes relative to the state folder."""
    if isinstance(node, dict):
        return {k: _rewrite_paths(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_rewrite_paths(v) for v in node]
    if isinstance(node, str) and node.startswith(CONTAINER_PREFIX + "/"):
        return _relative(node)
    return node


def convert_config(old: dict) -> tuple[dict, list[str]]:
    """The v2.0 config as this version wants it, plus the notices to show the operator."""
    notes: list[str] = []
    cfg = _rewrite_paths(old)
    for key in CONTAINER_ONLY_KEYS:
        cfg.pop(key, None)
    bl = _relative(str(cfg.get("blocklist_file") or "blocklist.txt"))
    cfg["blocklist_file"] = bl or "blocklist.txt"
    rec = cfg.get("recording")
    if isinstance(rec, dict):
        rec["dir"] = _relative(str(rec.get("dir") or "recordings")) or "recordings"
    from .audio import is_legacy_pulse, LEGACY_PULSE_NOTICE

    if is_legacy_pulse(cfg.get("audio_device")):
        cfg["audio_device"] = None
        notes.append(LEGACY_PULSE_NOTICE)
    return cfg, notes


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportRefused(f"{path.name} could not be read as settings ({exc.__class__.__name__}).") from exc
    if not isinstance(data, dict):
        raise ImportRefused(f"{path.name} is not a Relay settings file.")
    return data


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    paths.restrict(tmp)
    tmp.replace(path)


def _same(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _has_own_settings(cfg: dict) -> bool:
    return bool(str(cfg.get("openai_api_key") or "").strip() or str(cfg.get("admin_token") or "").strip())


def import_from(source: str | Path, dest: str | Path, *, force: bool = False) -> Report:
    """Copy a docker-config folder into the state folder `dest`. Reads `source` only.

    Refuses to replace a config.json that already holds a key or token of its own and differs from what
    would be written, unless `force`, which keeps the old one as config.json.before-import."""
    src = find_source(source)
    dst = Path(dest).expanduser().resolve()
    if src == dst:
        raise ImportRefused("The old folder and Relay's own folder are the same place; nothing to import.")
    report = Report(source=str(src))
    new_cfg, notes = convert_config(_read_json(src / "config.json"))
    report.notes += notes
    wanted = (json.dumps(new_cfg, indent=2, ensure_ascii=False)).encode("utf-8")

    paths.make_private_dir(dst)
    cfg_path = dst / "config.json"
    if cfg_path.exists():
        try:
            current = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        if current == new_cfg:
            report.unchanged.append("config.json")
        elif _has_own_settings(current) and not force:
            raise ImportRefused("This Relay already has settings of its own (an API key or admin token). "
                                "Nothing was changed. Importing replaces them; run it again with --force "
                                "to keep the current file as config.json.before-import.")
        else:
            if _has_own_settings(current):
                shutil.copyfile(cfg_path, cfg_path.with_name("config.json.before-import"))
                paths.restrict(cfg_path.with_name("config.json.before-import"))
            _write_private(cfg_path, wanted)
            report.copied.append("config.json")
    else:
        _write_private(cfg_path, wanted)
        report.copied.append("config.json")

    # The blocklist: replace the packaged copy Relay seeds on first run, never one the operator edited since.
    bl_src, bl_dst = src / "blocklist.txt", dst / "blocklist.txt"
    if bl_src.is_file():
        packaged = ROOT / "blocklist.txt"
        if _same(bl_src, bl_dst):
            report.unchanged.append("blocklist.txt")
        elif not bl_dst.exists() or force or (packaged.is_file() and _same(packaged, bl_dst)):
            shutil.copyfile(bl_src, bl_dst)
            report.copied.append("blocklist.txt")
        else:
            report.notes.append("This Relay has its own blocklist.txt, so the old one was left out.")

    # Folders merge: a file already there is kept, so a second run adds nothing and loses nothing.
    for name in FOLDERS:
        base = src / name
        if not base.is_dir():
            continue
        added = 0
        for f in sorted(base.rglob("*")):
            if f.is_symlink() or not f.is_file():
                continue
            target = dst / name / f.relative_to(base)
            if target.exists():
                continue
            paths.make_private_dir(target.parent)
            shutil.copyfile(f, target)
            added += 1
        (report.copied if added else report.unchanged).append(f"{name}/" + (f" ({added} files)" if added else ""))

    report.notes.append(CERT_SENTENCE)
    return report
