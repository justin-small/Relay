"""Where Relay keeps what changes: the per-user state folder.

One folder holds config.json, relay.db, blocklist.txt, certs/, backups/, recordings/,
panel-firmware/ and logs. Nothing mutable lives in the install directory, so a frozen or
read-only install works the same as a source checkout.

Which folder, first match wins:
  1. RELAY_HOME                      (explicit)
  2. the folder holding RELAY_CONFIG (tests, the container, the service install)
  3. the checkout itself, when it already has a config.json (an existing source-checkout setup)
  4. the per-user default for the OS
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def default_home() -> Path:
    """The per-user folder for this OS (not created here)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Relay"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Relay"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "relay"


def config_path() -> Path:
    env = (os.environ.get("RELAY_CONFIG") or "").strip()
    if env:
        return Path(env)
    home = (os.environ.get("RELAY_HOME") or "").strip()
    if home:
        return Path(home) / "config.json"
    if (ROOT / "config.json").exists():
        return ROOT / "config.json"
    return default_home() / "config.json"


def state_dir() -> Path:
    """The folder everything mutable lives in: wherever config.json is."""
    return config_path().parent


def make_private_dir(path: Path) -> None:
    """Create `path` (and parents) owner-only where the OS supports it."""
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True)
    if not existed:
        restrict(path)


def restrict(path: Path) -> None:
    """Owner-only: 0700 for a folder, 0600 for a file; an owner-only ACL on Windows.

    Best effort for a folder or file this process cannot change; a secret file that
    cannot be made private is handled by the caller (config._tighten).
    """
    if sys.platform == "win32":
        user = os.environ.get("USERNAME")
        if not user:
            return
        try:
            subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F" if path.is_dir()
                 else f"{user}:F"],
                check=True, capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    except OSError:
        pass
