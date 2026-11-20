---
title: "CodeQL fails a plain hash of any secret key, and gitleaks fails fake sk- keys in tests"
author: Justin
date: 2026-11-16
branch: intercom-security-278
cost: { minutes: 5, tokens: 242524 }
id: l-836edd67
scope: repo
status: active
src: agent
touches:
  - ".gitleaksignore"
  - app/secretbox.py
  - README.md
  - app/backup.py
  - app/config.py
  - app/db.py
  - app/main.py
  - app/modules/__init__.py
  - app/modules/crew/paging.py
  - app/modules/intercom/livekit.py
---

## Decided
To give an encryption key a short public id, take the first bytes of its own AES-GCM tag over an empty message, not a SHA-256 of the key. A fake API key that a test already committed goes in .gitleaksignore by fingerprint.

## Tried and ruled out
sha256(prefix + key)[:8] as the key id: CodeQL (required check) reported it as weak password hashing because the key came from keyring.get_password.

## Watch out
Run tests/run_security.py before pushing: gitleaks flagged `"openai_api_key": "sk-test-…"` in a new test only after it was committed. Prefer obviously fake values without an sk- prefix in new tests.

In the code: app/secretbox.py kid(); .gitleaksignore line format commit:file:rule:line.
