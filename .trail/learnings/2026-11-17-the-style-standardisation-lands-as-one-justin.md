---
title: "The style standardisation lands as one PR covering every page, with a browser style check and shared tabs"
author: Justin
date: 2026-11-17
branch: ignore-local-files
cost: { minutes: 1, tokens: 1456 }
id: l-ab9fa4d7
scope: repo
status: active
src: agent
touches:
  - app/static/tokens.css
  - app/static/admin.css
  - tests/test_browser.py
---

## Decided
Issue 463, 2026-11-17, Justin's choices:
- One PR migrates everything: tokens, admin.css components, all module CSS, template <style> blocks, style="" attributes and JS-injected CSS, until the issue's "Done when" holds.
- The written exceptions stay at docs/ui-audit/standardization-spec.md; that one file is un-ignored while the rest of docs/ui-audit/ stays local.
- The same PR adds the Playwright computed-style check and the shared tabs styles (the ARIA keyboard work stays in issue 462).
- The 14% green tint means live/pressed state, not a primary button variant.

## Tried and ruled out
- Foundation plus ratchet now and migration in follow-up PRs: Justin wanted the whole migration at once.
- Moving the spec to docs/style.md: Justin kept the path the issue names.

## Watch out
- docs/ui-audit/ is gitignored (commit 1a01d4f), so the spec needs a ! exception in .gitignore or it silently won't commit.

In the code:
- app/static/tokens.css, app/static/admin.css (button.primary at :121, .qc .go at :31, button.danger literal #7f1d1d at :123)
- tests/test_style_contract.py (new ratchet), tests/test_browser.py
