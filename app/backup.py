"""Encrypted database backups (#278).

Every backup Relay writes is encrypted with AES-256-GCM under its own random data key. That key is stored in the
file twice at most:

  - sealed with this computer's at-rest key (app/secretbox.py), so Relay restores it here with nothing typed;
  - when the owner has set a backup passphrase, also wrapped with a key derived from it (scrypt), so a copy taken
    to another computer can be restored there with the passphrase.

The file is `RELAYBK1\\n`, one JSON header line, then nonce + ciphertext of the SQLite file. The header is bound
into the tag, so it cannot be swapped onto another backup. config.json never holds the passphrase, only the key
derived from it (sealed, as `backup_key`).

Restoring: `relay --restore-backup <file>` (the installed app) or `python tools/restore_backup.py <file>`.
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import secretbox

MAGIC = b"RELAYBK1\n"
SUFFIX = ".db.enc"
DATA_KEY_PURPOSE = "backup:data-key"
PASSPHRASE_AAD = b"backup:passphrase"
MIN_PASSPHRASE = 10
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class BackupError(Exception):
    """A backup that cannot be read or opened. `message` is a fixed sentence for people."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _b64d(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


def derive(passphrase: str, salt: bytes, n: int = SCRYPT["n"], r: int = SCRYPT["r"], p: int = SCRYPT["p"]) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def passphrase_setting(passphrase: str) -> str:
    """What config.json's `backup_key` holds for `passphrase`: its salt, scrypt cost and derived key (sealed on save).

    '' clears it. Raises ValueError for one shorter than MIN_PASSPHRASE characters."""
    if not passphrase:
        return ""
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"The passphrase needs at least {MIN_PASSPHRASE} characters.")
    salt = os.urandom(16)
    return json.dumps({"salt": _b64e(salt), **SCRYPT, "key": _b64e(derive(passphrase, salt))})


def _passphrase_key(setting: str) -> dict | None:
    if not setting:
        return None
    try:
        got = json.loads(setting)
        return {"salt": got["salt"], "n": int(got["n"]), "r": int(got["r"]), "p": int(got["p"]),
                "key": _b64d(got["key"])}
    except (ValueError, KeyError, TypeError):
        return None


def encrypt(plain: bytes, passphrase_key: str = "") -> bytes:
    """`plain` (a SQLite file) as an encrypted backup. `passphrase_key` is config's `backup_key` ('' for none)."""
    data_key = AESGCM.generate_key(bit_length=256)
    header: dict = {"v": 1, "machine": secretbox.seal(_b64e(data_key), DATA_KEY_PURPOSE)}
    pk = _passphrase_key(passphrase_key)
    if pk:
        nonce = os.urandom(12)
        header["passphrase"] = {"salt": pk["salt"], "n": pk["n"], "r": pk["r"], "p": pk["p"],
                                "wrapped": _b64e(nonce + AESGCM(pk["key"]).encrypt(nonce, data_key, PASSPHRASE_AAD))}
    head = json.dumps(header, separators=(",", ":")).encode() + b"\n"
    nonce = os.urandom(12)
    return MAGIC + head + nonce + AESGCM(data_key).encrypt(nonce, plain, MAGIC + head)


def is_encrypted(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def decrypt(blob: bytes, passphrase: str = "") -> bytes:
    """The SQLite file inside an encrypted backup. Tries this computer's key, then `passphrase` if given."""
    if not is_encrypted(blob):
        raise BackupError("This is not an encrypted Relay backup.")
    end = blob.find(b"\n", len(MAGIC))
    if end < 0:
        raise BackupError("The backup is damaged.")
    head = blob[len(MAGIC):end + 1]
    body = blob[end + 1:]
    try:
        header = json.loads(head)
    except ValueError:
        raise BackupError("The backup is damaged.") from None
    keys: list[bytes] = []
    if passphrase and isinstance(header.get("passphrase"), dict):
        pp = header["passphrase"]
        try:
            kek = derive(passphrase, _b64d(pp["salt"]), int(pp["n"]), int(pp["r"]), int(pp["p"]))
            wrapped = _b64d(pp["wrapped"])
            keys.append(AESGCM(kek).decrypt(wrapped[:12], wrapped[12:], PASSPHRASE_AAD))
        except (InvalidTag, ValueError, KeyError, TypeError):
            pass
    opened = secretbox.unseal(header.get("machine"), DATA_KEY_PURPOSE) if header.get("machine") else None
    if opened:
        try:
            keys.append(_b64d(opened))
        except ValueError:
            pass
    for key in keys:
        try:
            return AESGCM(key).decrypt(body[:12], body[12:], MAGIC + head)
        except (InvalidTag, ValueError):
            continue
    if passphrase:
        raise BackupError("Neither this computer's key nor that passphrase opens this backup.")
    if header.get("passphrase"):
        raise BackupError("This computer's key does not open this backup. Give the backup passphrase.")
    raise BackupError("This computer's key does not open this backup, and it was made without a passphrase.")


def restore(file: Path, dest: Path, passphrase: str = "") -> Path | None:
    """Write the database inside `file` to `dest`; the database already there is kept beside it, renamed
    `.before-restore`. Returns that kept file, or None. A plain (pre-#278) backup is copied as it is."""
    import sqlite3

    blob = Path(file).read_bytes()
    plain = decrypt(blob, passphrase) if is_encrypted(blob) else blob
    if not plain.startswith(b"SQLite format 3\x00"):
        raise BackupError("The backup does not hold a Relay database.")
    tmp = dest.with_name(f".{dest.name}.restore.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(plain)
    try:
        conn = sqlite3.connect(tmp)
        try:
            ok = conn.execute("pragma integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        ok = False
    if not ok:
        tmp.unlink(missing_ok=True)
        raise BackupError("The database inside the backup is damaged.")
    kept = None
    if dest.exists():
        kept = dest.with_name(dest.name + ".before-restore")
        os.replace(dest, kept)
    for side in ("-wal", "-shm"):
        dest.with_name(dest.name + side).unlink(missing_ok=True)
    os.replace(tmp, dest)
    return kept


def main(argv: list[str]) -> int:
    """`restore_backup.py <backup file> [--passphrase] [--to <relay.db>]`. Run it with Relay stopped."""
    from . import db

    args = argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(f"usage: {argv[0]} <backup file> [--passphrase] [--to <database file>]\n"
              "Restores a Relay database backup. Stop Relay first. --passphrase asks for the backup passphrase,\n"
              "needed on a computer other than the one that made the backup.")
        return 2
    file = Path(args[0])
    dest = Path(args[args.index("--to") + 1]) if "--to" in args else db.path()
    passphrase = getpass.getpass("Backup passphrase: ") if "--passphrase" in args else ""
    try:
        kept = restore(file, dest, passphrase)
    except BackupError as e:
        print(e.message, file=sys.stderr)
        return 1
    except OSError as e:
        print(f"Could not restore: {e}", file=sys.stderr)
        return 1
    print(f"Restored {file.name} to {dest}." + (f" The database it replaced is kept as {kept.name}." if kept else ""))
    return 0
