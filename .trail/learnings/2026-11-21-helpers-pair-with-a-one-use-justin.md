---
title: Helpers pair with a one-use code and a token kept as a hash; helper management is barred from API tokens
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 8, tokens: 100300 }
id: l-a65b45f7
scope: repo
status: active
src: agent
touches:
  - app/helperhub.py
  - app/main.py
  - app/users.py
  - tests/test_auth.py
  - tests/test_helperhub.py
  - tests/test_modules.py
  - ".github/workflows/tests.yml"
  - docs/helper.md
  - native/relay-helper/Cargo.lock
  - native/relay-helper/Cargo.toml
---

## Decided
Justin, 2026-11-21, issue 497: Relay's end of the Relay Helper is a core hub, because a helper is not an integration with another product. An admin makes a ten-minute one-use pairing code on the Helpers page (System tabs); the helper trades it for a token on the same WebSocket, and Relay stores only the token's sha256, like API tokens. Modules ask the hub to call a helper by capability and never import helper code.

## Tried and ruled out
tokens=False on the helper routes: tests/test_auth.py pins the exact list of routes that refuse tokens. Barring the permission in TOKEN_BARRED does the job, so a leaked API token cannot mint pairing codes.
Putting the token in the connection address: addresses reach logs, so it travels in the first frame.

## Watch out
In the code: app/helperhub.py, /ws/helper and /api/admin/helpers* in main.py, permissions helpers.view and helpers.manage (both in tests/test_auth.py MIN_ROLE), helperhub.py is listed in CORE_FILES. Ten wrong codes in ten minutes stop pairing for a while. Proved live: the real Rust helper paired with a real uvicorn Relay and ran status, api and open_files against ProPresenter 22 on this Mac. Not yet: the ProPresenter module does not call the hub, and the Helpers page has not been looked at in a browser (the full suite was still running).
