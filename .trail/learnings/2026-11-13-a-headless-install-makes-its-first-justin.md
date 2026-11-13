---
title: "A headless install makes its first Owner with a one-time setup code, not the loopback rule"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 3, tokens: 131645 }
id: l-3a825357
scope: repo
status: active
src: agent
touches:
  - app/setupcode.py
  - run.py
  - app/main.py
  - app/templates/login.html
  - tests/test_auth.py
---

## Decided
Issue 185: with RELAY_SETUP_CODE=1 (set only by the systemd unit) Relay makes a code while no account exists, prints it to stdout (the journal), keeps it in setup-code in the state folder so the installer and restarts agree, and the first-run page accepts it from any device instead of requiring "from this machine". It is deleted when the Owner is created. Desktop and `python run.py` installs leave it off and keep the loopback rule. The unit is a static file (tools/relay.service) so a test can read it.

## Tried and ruled out
Generating the code in the installer: the service would not know it. SystemCallFilter in the unit: could not be tried on Linux from a Mac and a wrong filter stops the service starting; add it once `systemd-analyze security relay` has been read on a real machine.

## Watch out
Not run on a real Debian, Raspberry Pi OS or systemd: only the Python side, the unit's text and bash syntax/shellcheck are tested. Debian 12 ships Python 3.11 and the pins need 3.13+, so the installer asks for --python there. CodeQL may flag the stdout write of the code in app/main.py (_announce_setup_code) as clear-text logging; the issue requires it in the journal. The `systemd-analyze security` score for the PR is still to be recorded.

In the code: app/setupcode.py, app/main.py (_announce_setup_code, admin_setup, _login_page), app/templates/login.html, tools/install-service.sh, tools/relay.service, tests/test_service_unit.py, tests/test_auth.py (setup code section).
