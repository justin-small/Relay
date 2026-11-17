---
title: "A seeded demo Relay for UI review runs every module against the test suite's fakes, over plain HTTP"
author: Justin
date: 2026-11-17
branch: main
cost: { minutes: 21, tokens: 337013 }
id: l-eb281e5a
scope: repo
status: active
src: agent
touches:
  - docs/ui-audit/verification.md
---

## Decided
2026-11-17, while verifying the UI audit: docs/ui-audit/demo/serve.py brings up a whole Relay with made-up data so every page looks like a real church's, instead of the empty "not configured" states. It reuses the fakes the tests already have by pulling just their class definitions out of the test files with `ast` (ProPresenter's Fake + make_handler, OBS's FakeObs), so a fake that changes in a test changes here too. Planning Center is a `pc.Client` subclass swapped in as `planningcenter.Client`; lighting is a Backend registered with `be.register`.

## Watch out
- Run uvicorn yourself on the admin port, the viewer port, and every module listener (`registry.listeners(cfg)` + `serving.publish_listeners`), or /signage and /crew-join answer nothing.
- Boards, overlays, signage screens and lighting pages only take shape through the admin API (slug, catalogue item sizes); the script signs in and posts them once.
- Stopping the server with SSE pages open hangs on shutdown; kill -9.
- Use Playwright with is_mobile for phone/tablet checks: the rundown kiosk only mis-scales under real mobile viewport rules.
- No `sk-` placeholder key in the seed: gitleaks fails it.

In the code: docs/ui-audit/demo/serve.py, sweep.py (5 viewports, screenshots + measurements), analyze.py; .claude/launch.json entry relay-ui-demo.
