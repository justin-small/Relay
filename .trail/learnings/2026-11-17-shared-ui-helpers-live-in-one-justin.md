---
title: "Shared UI helpers live in one core script, every copy was migrated in one pull request, and a contract test with a shrink-only baseline stops new copies"
author: Justin
date: 2026-11-17
branch: ui-shared-helpers-461
cost: { minutes: 20, tokens: 313356 }
id: l-a1488dce
scope: repo
status: active
src: agent
touches:
  - app/static/relay-ui.js
  - tests/test_ui_contract.py
  - tests/js/relay_ui.test.mjs
  - docs/modules.md
  - AGENTS.md
  - tests/_harness.py
---

## Decided
Justin, 2026-11-17, issue 461: the whole issue goes in one pull request: the shared script, all migrations of can/send/h/el/say/api, and every confirm() call moved to an awaited shared confirm and enforced now (core scripts too, not only modules). The shared script is loaded on every page whose scripts use it. Behaviour must not change: copies that differed (error text, session handling, reload on 401, "HTTP <status>" fallback) are kept exact through options on the shared api, not normalised.

## Tried and ruled out
- Foundation plus only the `can` migration, or foundation alone with a full baseline: offered, not chosen.
- Leaving confirm() for later: not chosen; migrated and enforced now.
- Normalising error sentences into one: not chosen.
- Loading the shared script on the remote phone page: the remote-access tunnel exposes only that page's four routes, so the phone page keeps its own status helper and is exempt in the test.

## Watch out
- Widget helpers (button, field, pill, link, check, active, row, showError) are still copied; they sit in the baseline, which may only shrink.
- Small visible differences accepted: el() with text 0 now shows "0"; say() toggles ok/err classes instead of replacing className.
- Planning Center and ProPresenter both use the element id sgState (pre-existing clash).
- trail check flags lighting.js "Choose a program" as a conflict: false positive from an argument reorder.

In the code: app/static/relay-ui.js (window.relayUI: can, api, client, el, say, confirm), tests/test_ui_contract.py (--rebaseline after removing a copy), tests/ui_contract_baseline.txt, tests/js/relay_ui.test.mjs, docs/modules.md "Shared helpers for module scripts", AGENTS.md rule.
