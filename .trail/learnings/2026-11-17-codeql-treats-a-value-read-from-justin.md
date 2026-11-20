---
title: CodeQL treats a value read from an environment variable with OAUTH in its name as a password
author: Justin
date: 2026-11-17
branch: oauth-cloud-redirect
cost: { minutes: 3, tokens: 26367 }
id: l-11ac2005
scope: repo
status: active
src: agent
touches:
  - app/oauth.py
  - ".github/workflows/release.yml"
  - README.md
  - app/modules/youtube/__init__.py
  - app/modules/youtube/service.py
  - app/modules/youtube/static/youtube.js
  - app/static/oauth.js
  - docs/reference/oauth-apps.md
  - site/public/privacy.html
  - tests/test_oauth.py
---

## Decided
relay-cloud's address in the sign-in helper is a plain constant, not read from an environment variable. PR 449's CodeQL check (required) failed with "logs sensitive data (password) as clear text": the source was `os.environ.get("RELAY_OAUTH_CLOUD")`, and a test that compared the address with eq() printed it through the harness.

## Watch out
- Any environment variable whose name contains OAUTH, TOKEN, SECRET, KEY or PASSWORD is a CodeQL password source, even when it holds a URL. Do not read such names into values a test prints.
- The local security scans (bandit, gitleaks) do not catch this; only CodeQL on GitHub does.

In the code: app/oauth.py CLOUD; tests/_harness.py eq().
