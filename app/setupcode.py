"""The one-time setup code of a headless install (tools/install-service.sh).

A machine with no screen cannot open the first-run page on itself, which is the only place the first Owner
can otherwise be made. With RELAY_SETUP_CODE=1 (the systemd unit sets it) Relay makes a code while no account
exists, prints it to its log (the journal) and keeps it in `setup-code` in the state folder, where the
installer reads it. The first-run page then takes the code from any device, over the panel's HTTPS. The code
is removed as soon as the first Owner exists, and never accepted again.

Only a desktop or `python run.py` install, which has a browser on the machine, leaves this off and keeps the
loopback-only rule.
"""
from __future__ import annotations

import contextlib
import os
import secrets

from . import config, paths

# No 0/O, 1/I/L: the code is read off a terminal and typed on another device.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 3, 4          # XXXX-XXXX-XXXX: about 59 bits


def enabled() -> bool:
    return os.environ.get("RELAY_SETUP_CODE") == "1"


def _file():
    return config.STATE_DIR / "setup-code"


def _normal(text: str) -> str:
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def _make() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))


def active() -> str:
    """The code in force, or "" when this install does not use one or none is pending."""
    if not enabled():
        return ""
    try:
        return _file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def matches(supplied: str) -> bool:
    """True when `supplied` is the code in force (case, dashes and spaces ignored). Constant time."""
    real = _normal(active())
    given = _normal(supplied)
    if not real or not given:
        return False
    return secrets.compare_digest(given.encode("utf-8"), real.encode("utf-8"))


def prepare(have_users: bool) -> str:
    """At start-up: keep the pending code (so the journal and the installer agree across restarts), make one
    when none exists, and drop it once an account exists. Returns the code to announce, or "" for none."""
    if not enabled():
        return ""
    if have_users:
        clear()
        return ""
    existing = active()
    if existing:
        return existing
    code = _make()
    paths.make_private_dir(config.STATE_DIR)
    path = _file()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(code + "\n")
    paths.restrict(path)
    return code


def clear() -> None:
    """The first Owner exists: the code is spent."""
    with contextlib.suppress(OSError):
        _file().unlink()
