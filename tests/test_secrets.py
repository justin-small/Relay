"""Secrets at rest and encrypted backups (#278): the machine key, sealed config and database secrets, encrypted
backups with an optional owner passphrase, and restoring them. Run: python tests/test_secrets.py
"""
import json
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_DB_BACKUPS"] = os.path.join(TMP, "backups")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import capture_log, check, done, eq, section, tmpdir  # noqa: E402
import _db  # noqa: E402

_db.start()
_db.reset()

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import backup, config, db, secretbox  # noqa: E402
from app.main import app  # noqa: E402

POSIX = os.name == "posix"


class Memory(KeyringBackend):
    """A keyring that keeps passwords in this process, standing in for the Keychain or Credential Manager."""
    priority = 1
    kept: dict = {}

    def get_password(self, service, username):
        return self.kept.get((service, username))

    def set_password(self, service, username, password):
        self.kept[(service, username)] = password

    def delete_password(self, service, username):
        self.kept.pop((service, username), None)


class Broken(Memory):
    def get_password(self, service, username):
        raise RuntimeError("the keychain is locked")


def elsewhere(store: str = "file") -> str:
    """Point the key store at a fresh state folder, as if on another computer. Returns the folder."""
    folder = tmpdir()
    os.environ["RELAY_CONFIG"] = os.path.join(folder, "config.json")
    os.environ["RELAY_KEY_STORE"] = store
    secretbox.reset()
    return folder


def home() -> None:
    os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
    os.environ["RELAY_KEY_STORE"] = "file"
    secretbox.reset()


with section("sealing"):
    sealed = secretbox.seal("hunter2-hunter2", "test:a")
    check("a sealed value is marked and does not hold the text", sealed.startswith("enc:v1:") and "hunter2" not in sealed)
    eq("it opens for the same purpose", secretbox.unseal(sealed, "test:a"), "hunter2-hunter2")
    with capture_log("relay.secretbox"):
        eq("it does not open for another purpose (moved to another field)", secretbox.unseal(sealed, "test:b"), None)
        tampered = sealed[:-3] + ("AAA" if not sealed.endswith("AAA") else "BBB")
        eq("a changed value does not open", secretbox.unseal(tampered, "test:a"), None)
    eq("plain text (written before #278) is read as it is", secretbox.unseal("old-plain", "test:a"), "old-plain")
    eq("an empty secret stays empty", secretbox.seal("", "test:a"), "")
    check("two seals of one secret differ (fresh nonce)", secretbox.seal("x", "p") != secretbox.seal("x", "p"))
    key_file = Path(TMP) / secretbox.KEY_FILE
    check("with the file store the key is in the state folder", key_file.exists())
    POSIX and eq("…owner-only", stat.S_IMODE(key_file.stat().st_mode), 0o600)
    eq("where() names it", secretbox.where(), "key file")

with section("the keyring"):
    keyring.set_keyring(Memory())
    folder = elsewhere("keyring")
    sealed_k = secretbox.seal("in-the-keychain", "test:k")
    check("the key is kept in the keyring, not a file", not (Path(folder) / secretbox.KEY_FILE).exists()
          and any(u.startswith("at-rest-key-") for (_, u) in Memory.kept))
    check("only a non-secret id is beside config.json", (Path(folder) / secretbox.ID_FILE).exists())
    secretbox.reset()
    eq("it opens again after a restart", secretbox.unseal(sealed_k, "test:k"), "in-the-keychain")
    check("where() names the store", secretbox.where() in ("macOS Keychain", "Windows Credential Manager",
                                                          "system keyring"))
    keyring.set_keyring(Broken())
    secretbox.reset()
    with capture_log("relay.secretbox") as logged:
        fallback = secretbox.seal("fallback", "test:f")
    check("a keyring that fails falls back to a key file, and says so",
          (Path(folder) / secretbox.KEY_FILE).exists() and "key file" in secretbox.where() and logged)
    keyring.set_keyring(Memory())
    secretbox.reset()
    eq("values sealed during the fallback still open once the keyring is back",
       secretbox.unseal(fallback, "test:f"), "fallback")
    eq("…and so do the earlier ones", secretbox.unseal(sealed_k, "test:k"), "in-the-keychain")
    home()

with section("config.json"):
    config.ensure_file()
    raw = json.loads(Path(config.CONFIG_PATH).read_text())
    raw["admin_token"] = "typed-by-hand-token"
    raw["planning_center"] = {"app_id": "abc", "secret": "pc-secret-value"}
    Path(config.CONFIG_PATH).write_text(json.dumps(raw))
    cfg = config.load()
    eq("a token typed into config.json by hand works", cfg["admin_token"], "typed-by-hand-token")
    eq("so does a module's secret", cfg["planning_center"]["secret"], "pc-secret-value")
    text = Path(config.CONFIG_PATH).read_text()
    check("both are sealed on disk straight away", "typed-by-hand-token" not in text and "pc-secret-value" not in text)
    config.save({"openai_api_key": "sk-test-1234567890", "port": 8000, "admin_port": 8001,
                 "remote_access": {"secret": "enroll-secret-xyz", "tunnel_token": "tunnel-token-xyz"}})
    text = Path(config.CONFIG_PATH).read_text()
    for plain in ("sk-test-1234567890", "enroll-secret-xyz", "tunnel-token-xyz"):
        check(f"{plain[:8]}… is not in config.json", plain not in text)
    eq("in memory they are plain", (config.get()["openai_api_key"], config.get()["remote_access"]["secret"]),
       ("sk-test-1234567890", "enroll-secret-xyz"))
    red = config.redacted()
    check("the admin view has no secret and no backup key",
          "backup_key" not in red and "admin_token" not in red and "sk-test" not in json.dumps(red))
    eq("it says where the key is", red["secret_store"], "key file")

    # A sealed value this computer cannot open (its key lost, or the keyring locked for this start).
    raw = json.loads(Path(config.CONFIG_PATH).read_text())
    raw["planning_center"]["secret"] = "enc:v1:00000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    Path(config.CONFIG_PATH).write_text(json.dumps(raw))
    with capture_log("relay.secretbox"):
        cfg = config.load()
    eq("it reads as not set", cfg["planning_center"]["secret"], "")
    config.save({"history_lines": 7})
    check("an unrelated save keeps it as stored rather than wiping it",
          "enc:v1:00000000:" in Path(config.CONFIG_PATH).read_text())
    config.save({"planning_center": {"secret": "new-pc-secret"}})
    check("a new value replaces it", "enc:v1:00000000:" not in Path(config.CONFIG_PATH).read_text()
          and config.get()["planning_center"]["secret"] == "new-pc-secret")

with section("the crew push key in relay.db"):
    from app.modules.crew import notifications
    key = notifications.vapid_key()
    stored = db.one("select private_pem from crew_vapid where id = 1")["private_pem"]
    check("relay.db keeps it sealed", secretbox.is_sealed(stored) and "PRIVATE KEY" not in stored)
    eq("and it opens as the same key", notifications.public_key(), notifications.public_key())
    from app.modules.crew import webpush
    db.execute("update crew_vapid set private_pem = ? where id = 1", (webpush.key_to_pem(key),))
    pub = notifications.public_key()
    check("a key stored before #278 is sealed on first use, unchanged",
          secretbox.is_sealed(db.one("select private_pem from crew_vapid where id = 1")["private_pem"])
          and pub == webpush.b64e(webpush.public_bytes(key)))

with section("encrypted backups"):
    db.execute("create table if not exists backup_probe (v text)")
    db.execute("insert into backup_probe (v) values ('needle-in-the-database')")
    made = db.backup()
    check("a backup is written as .db.enc", made is not None and made.name.endswith(".db.enc"), made)
    blob = made.read_bytes()
    check("it is not a readable SQLite file", not blob.startswith(b"SQLite format 3")
          and b"needle-in-the-database" not in blob and b"PRIVATE KEY" not in blob)
    POSIX and eq("it is owner-only", stat.S_IMODE(made.stat().st_mode), 0o600)
    plain = backup.decrypt(blob)
    check("this computer opens it with nothing typed", plain.startswith(b"SQLite format 3")
          and b"needle-in-the-database" in plain)
    header_swap = blob.replace(b'"v":1', b'"v":2', 1)
    try:
        backup.decrypt(header_swap)
        check("a changed header is refused", False)
    except backup.BackupError:
        check("a changed header is refused", True)
    eq("no plain copy is left beside it", sorted(p.name for p in made.parent.iterdir() if "plain" in p.name), [])

    # An older, plain backup is encrypted in place at the next backup.
    old = made.parent / "relay-20200101-000000.db"
    old.write_bytes(plain)
    db.backup()
    check("a plain backup left by an older version is encrypted", not old.exists()
          and (made.parent / "relay-20200101-000000.db.enc").exists())
    eq("backup_due counts encrypted backups", db.backup_due(), False)

with section("the backup passphrase"):
    try:
        backup.passphrase_setting("too short")
        check("a short passphrase is refused", False)
    except ValueError:
        check("a short passphrase is refused", True)
    setting = backup.passphrase_setting("correct horse battery")
    check("the setting holds a derived key, not the passphrase", "correct horse" not in setting)
    blob = backup.encrypt(plain, setting)
    elsewhere()
    try:
        backup.decrypt(blob)
        check("another computer cannot open it without the passphrase", False)
    except backup.BackupError as e:
        check("another computer cannot open it without the passphrase", "passphrase" in e.message, e.message)
    try:
        backup.decrypt(blob, "wrong horse battery")
        check("a wrong passphrase is refused", False)
    except backup.BackupError:
        check("a wrong passphrase is refused", True)
    check("the right passphrase opens it there", backup.decrypt(blob, "correct horse battery") == plain)
    no_pass = backup.encrypt(b"SQLite format 3\x00", "")
    home()
    elsewhere()
    try:
        backup.decrypt(no_pass)
        check("one made without a passphrase cannot be opened elsewhere", False)
    except backup.BackupError as e:
        check("one made without a passphrase cannot be opened elsewhere", "without a passphrase" in e.message)
    home()

with section("restoring"):
    folder = Path(tmpdir())
    dest = folder / "relay.db"
    dest.write_bytes(b"old database")
    kept = backup.restore(made, dest)
    eq("the database being replaced is kept", (kept and kept.name, kept and kept.read_bytes()),
       ("relay.db.before-restore", b"old database"))
    conn = sqlite3.connect(dest)
    eq("the restored database holds the data", conn.execute("select v from backup_probe").fetchone()[0],
       "needle-in-the-database")
    conn.close()
    junk = folder / "junk.db.enc"
    junk.write_bytes(b"not a backup")
    eq("the command refuses something that is not a backup", backup.main(["restore", str(junk), "--to",
                                                                           str(folder / "x.db")]), 1)
    eq("the command restores one", backup.main(["restore", str(made), "--to", str(folder / "y.db")]), 0)
    eq("a pre-#278 plain backup still restores", backup.restore(old.with_name(old.name + ".enc"),
                                                                folder / "z.db") is None, True)

with section("setting the passphrase from the admin page"):
    owner = _db.client(app)
    r = owner.post("/api/admin/config", json={"backup_passphrase": "short"})
    eq("too short is refused with a fixed sentence", (r.status_code, r.json().get("detail")),
       (400, "The backup passphrase needs at least 10 characters."))
    r = owner.post("/api/admin/config", json={"backup_passphrase": "a long enough passphrase"})
    eq("the owner sets it", (r.status_code, r.json()["config"]["backup_passphrase_set"]), (200, True))
    text = Path(config.CONFIG_PATH).read_text()
    check("config.json holds neither the passphrase nor the derived key in the clear",
          "a long enough passphrase" not in text and json.loads(text)["backup_key"].startswith("enc:v1:"))
    made = db.backup(passphrase_key=config.get()["backup_key"])
    elsewhere()
    check("the next backup opens elsewhere with it",
          backup.decrypt(made.read_bytes(), "a long enough passphrase").startswith(b"SQLite format 3"))
    home()
    admin = _db.client(app, role="admin")
    eq("an admin may not change it", admin.post("/api/admin/config", json={"backup_passphrase": None}).status_code,
       403)
    r = owner.post("/api/admin/config", json={"backup_passphrase": None})
    eq("the owner removes it", (r.status_code, r.json()["config"]["backup_passphrase_set"]), (200, False))

with section("login lockout"):
    eq("five failures per address, then 10 minutes (#278)", (main.LOGIN_MAX_FAILS, main.LOGIN_LOCKOUT_S), (5, 600.0))

done()
