"""Secrets at rest (#278): one machine key kept outside relay.db and config.json, and AES-256-GCM with it.

A secret Relay must keep (the OpenAI key, the admin token, the LiveKit API secret, the relay-cloud enrollment
secret and tunnel token, the crew web-push key, the backup passphrase) is stored as `enc:v1:<kid>:<data>`,
never as plain text. The key that opens it lives in the operating system's own store:

  macOS     the login Keychain              (through the keyring package)
  Windows   Windows Credential Manager      (DPAPI-protected, through the keyring package)
  Linux     a 0600 file `secret.key` in the state folder, owned by the service user

RELAY_KEY_STORE=file forces the file on any platform (tests, throwaway installs); RELAY_KEY_STORE=keyring
forces the keyring. When the keyring cannot be used the file is used instead, and `where()` says so.

Losing the key loses what it sealed: the LiveKit key is made again (phones just reconnect), remote access is
enrolled again, the OpenAI key and admin token are entered again. No data in relay.db is lost. A value that is
still plain text (a hand-edited config.json) is read as it is and sealed the next time it is saved.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import sys
import threading
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import paths

log = logging.getLogger("relay.secretbox")

PREFIX = "enc:v1:"
SERVICE = "Relay"
KEY_FILE = "secret.key"
ID_FILE = "secret-key.id"

_lock = threading.RLock()
_keys: dict[str, bytes] = {}
_primary = ""
_where = ""
_warned: set[str] = set()


class Unavailable(Exception):
    """No key could be read or made."""


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def kid(key: bytes) -> str:
    """A short, non-secret name for a key, so a sealed value says which key opens it."""
    return hashlib.sha256(b"relay-key-id:" + key).hexdigest()[:8]


def choice() -> str:
    env = (os.environ.get("RELAY_KEY_STORE") or "").strip().lower()
    if env in ("file", "keyring"):
        return env
    return "keyring" if sys.platform in ("darwin", "win32") else "file"


def _write_private(path: Path, text: str) -> None:
    paths.make_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    paths.restrict(path)


def _file_key(create: bool) -> bytes | None:
    path = paths.state_dir() / KEY_FILE
    try:
        key = _b64d(path.read_text(encoding="utf-8").strip())
        if len(key) == 32:
            return key
        log.warning("secret store: %s does not hold a 32-byte key", path)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log.warning("secret store: could not read %s: %s", path, e)
    if not create:
        return None
    key = AESGCM.generate_key(bit_length=256)
    _write_private(path, _b64e(key) + "\n")
    log.info("secret store: made a new key file %s", path)
    return key


def _keyring_name() -> str:
    """The keyring account for this state folder, named by an id kept beside config.json (not a secret)."""
    path = paths.state_dir() / ID_FILE
    try:
        got = path.read_text(encoding="utf-8").strip()
        if got:
            return f"at-rest-key-{got}"
    except FileNotFoundError:
        pass
    got = uuid.uuid4().hex
    _write_private(path, got + "\n")
    return f"at-rest-key-{got}"


def _keyring_key() -> tuple[bytes, str]:
    import keyring
    from keyring.backends import fail

    ring = keyring.get_keyring()
    if isinstance(ring, fail.Keyring):
        raise Unavailable("no keyring backend on this computer")
    label = {"darwin": "macOS Keychain", "win32": "Windows Credential Manager"}.get(sys.platform, "system keyring")
    name = _keyring_name()
    got = keyring.get_password(SERVICE, name)
    if got:
        key = _b64d(got)
        if len(key) == 32:
            return key, label
    key = AESGCM.generate_key(bit_length=256)
    keyring.set_password(SERVICE, name, _b64e(key))
    if keyring.get_password(SERVICE, name) != _b64e(key):
        raise Unavailable("the keyring did not keep the key")
    log.info("secret store: made a new key in the %s", label)
    return key, label


def _load() -> None:
    global _primary, _where
    if _primary:
        return
    keys: dict[str, bytes] = {}
    primary, where = None, ""
    if choice() == "keyring":
        try:
            primary, where = _keyring_key()
        except Exception as e:      # any backend failure: locked, refused, no D-Bus session
            log.warning("secret store: the keyring could not be used (%s); using the key file instead", e)
    if primary is None:
        primary, where = _file_key(create=True), "key file"
        if where and choice() == "keyring":
            where = "key file (the keyring could not be used)"
    else:
        old = _file_key(create=False)          # values sealed while the keyring was down still open
        if old:
            keys[kid(old)] = old
    if primary is None:
        raise Unavailable("no key")
    keys[kid(primary)] = primary
    _keys.clear()
    _keys.update(keys)
    _primary, _where = kid(primary), where


def where() -> str:
    """Where the key is kept, for health and the admin page ('' before first use)."""
    with _lock:
        try:
            _load()
        except Exception:
            return ""
        return _where


def seal(text: str, purpose: str) -> str:
    """`text` sealed for `purpose` (bound into the tag, so a value cannot be moved to another field). '' stays ''."""
    if not text:
        return ""
    if is_sealed(text):
        return text
    with _lock:
        _load()
        key = _keys[_primary]
        nonce = os.urandom(12)
        data = AESGCM(key).encrypt(nonce, text.encode("utf-8"), purpose.encode("utf-8"))
        return f"{PREFIX}{_primary}:{_b64e(nonce + data)}"


def is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def unseal(value, purpose: str) -> str | None:
    """The plain text of a sealed value; a plain-text value as it is; None when it cannot be opened."""
    if not isinstance(value, str):
        return None
    if not is_sealed(value):
        return value
    try:
        key_id, data = value[len(PREFIX):].split(":", 1)
        raw = _b64d(data)
    except ValueError:
        _warn(purpose, "is not a sealed value")
        return None
    with _lock:
        try:
            _load()
        except Exception as e:
            _warn(purpose, f"cannot be opened: no key ({e})")
            return None
        key = _keys.get(key_id)
    if key is None or len(raw) < 13:
        _warn(purpose, "was sealed with a key this computer no longer has")
        return None
    try:
        return AESGCM(key).decrypt(raw[:12], raw[12:], purpose.encode("utf-8")).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError):
        _warn(purpose, "does not open with this computer's key")
        return None


def _warn(purpose: str, what: str) -> None:
    if purpose not in _warned:
        _warned.add(purpose)
        log.warning("secret store: the stored %s %s; it is treated as not set", purpose, what)


def reset() -> None:
    """Forget the cached key (tests, after the state folder changes)."""
    global _primary, _where
    with _lock:
        _keys.clear()
        _primary, _where = "", ""
        _warned.clear()
