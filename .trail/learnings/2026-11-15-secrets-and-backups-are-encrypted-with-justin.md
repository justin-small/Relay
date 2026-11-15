---
title: "Secrets and backups are encrypted with a machine key kept in the OS keyring, and backups can also open with an owner passphrase"
author: Justin
date: 2026-11-15
branch: intercom-security-278
cost: { minutes: 17, tokens: 271974 }
id: l-7a06132a
scope: repo
status: active
src: agent
touches:
  - app/secretbox.py
  - app/backup.py
  - tests/test_secrets.py
---

## Decided
Justin answered four questions on issue 278 (2026-11-15):
- The at-rest key lives in the keyring package's store (macOS Keychain, Windows Credential Manager), not hand-rolled `security`/DPAPI calls. Linux uses a 0600 `secret.key` file in the state folder (a systemd service has no desktop keyring).
- The owner sets an optional backup passphrase on the Credentials page (credentials.manage, owner only). Only the scrypt-derived key is kept (config `backup_key`, sealed), never the passphrase.
- Core sign-in keeps 5 failures per IP (stricter than the issue's 10) and the lockout is now 10 minutes.
- Core's secrets are sealed too, not only the intercom's: OpenAI key, admin token, Planning Center secret, remote-access secret/tunnel token, LiveKit secret, crew VAPID key.

## Tried and ruled out
- Writing the LiveKit secret into livekit.yaml: livekit-server 1.13 reads `LIVEKIT_KEYS="key: secret"` from its environment, so the yaml has no `keys:` block.
- Dropping an unopenable sealed config value on the next save: a keyring locked for one start would then wipe every secret. It is kept as stored until a new value replaces it.

## Watch out
- Copying the state folder to another Mac or Windows PC no longer carries the secrets: they must be re-entered, and backups need the passphrase.
- Not run on a real Windows Credential Manager, a signed macOS build, or a frozen PyInstaller app. An unsigned or re-signed Mac app may get a Keychain prompt after an update.
- The physical offline pass (router WAN out, three channels) and inspecting a real install's relay.db are still manual.
- Crew page confirm tokens (`crew_page_recipients.token`) are still plain in relay.db.

In the code: app/secretbox.py (seal/unseal, `enc:v1:<kid>:` values, RELAY_KEY_STORE=file|keyring; tests default to file), app/backup.py (RELAYBK1 format, restore), config.SECRET_KEYS + Module.secret_settings, `relay --restore-backup`, tools/restore_backup.py.
