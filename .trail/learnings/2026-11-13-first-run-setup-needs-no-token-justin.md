---
title: First-run setup needs no token when it comes from this machine; a configured admin token is still required
author: Justin
date: 2026-11-13
branch: feature/state-folder-180
cost: { minutes: 2, tokens: 37048 }
id: l-27768d0c
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/templates/login.html
  - tests/test_auth.py
  - tools/write_config.py
---

## Decided
First-run (#180 item 10): with no admin_token in config.json, the setup form and POST work only when the client is the machine itself, and the form also takes an optional OpenAI key. A config.json that does carry an admin token (container installs) still requires it, so Docker setups keep working.

## Tried and ruled out
Judging the TCP peer for "local": behind Caddy every request has a loopback peer, so a LAN client would pass. The check uses the client the proxy reports (X-Forwarded-For, trusted only from a loopback peer), the same rule the login lockout uses.

## Watch out
tools/write_config.py and the setup.* prompts are NOT removed yet: they are the only way a Docker install gets a token, and a Docker install behind Caddy cannot do token-less first-run from the LAN. Remove them with the compose files when Docker goes. Only exercised with the test client, not a real browser on a clean machine.

In the code: app/main.py (_setup_local, admin_setup, _login_page), app/templates/login.html, tests/test_auth.py (first run section).
