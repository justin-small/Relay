---
title: "Check the admin page in the browser against a throwaway config, and give any flex row a [hidden] rule"
author: Justin
date: 2026-11-10
branch: pc-service-mode-81
cost: { minutes: 6, tokens: 48894 }
id: l-00c28ecc
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/static/admin.js
  - app/templates/admin.html
  - tests/test_pccontrol.py
  - ".github/workflows/release.yml"
  - app/panels.py
  - app/pccontrol.py
  - app/pclive.py
  - app/planningcenter.py
---

## Decided
- To check admin UI changes in the browser, run Relay with RELAY_CONFIG pointing at a throwaway config (own admin token and ports, fake Planning Center credentials) instead of the real config.json. A fake panel check-in with curl on the viewer port puts a panel card on the page, and the fake credentials show the bad-token message in the Planning Center preview.

## Watch out
- An element styled `display:flex` ignores the `hidden` attribute. The panel card's buttons stayed visible while Rename, Set feed or Service was open until `.pcard .pactions[hidden]` got its own rule. Any new flex or grid row that the JS hides needs the same rule.

In the code: app/templates/admin.html (.pcard .pactions[hidden]); a "relay-scratch" entry in the git-ignored .claude/launch.json runs `/usr/bin/env RELAY_CONFIG=<scratch>/config.json .venv/bin/python run.py`.
