---
title: "Shared controls live in one stylesheet and the shared script, and a ratchet plus a browser check stop pages drifting"
author: Justin
date: 2026-11-17
branch: ui-standardize-463
cost: { minutes: 27, tokens: 376835 }
id: l-0df13d91
scope: repo
status: active
replaces: l-ab9fa4d7
src: agent
touches:
  - tests/test_style_contract.py
  - app/static/components.css
  - tests/_style_probe.py
  - app/static/tokens.css
  - app/static/relay-ui.js
  - app/modules/__init__.py
  - tests/test_browser.py
  - docs/modules.md
---

## Decided
Issue 463, 2026-11-17, Justin's choices:
- One pull request migrates every operator and module page, not foundation first.
- The shared controls go in their own stylesheet loaded by every page with controls (the panel, the sign-in and tool pages, the crew page), not in the panel's stylesheet as the issue first said, so the sign-in pages stop keeping their own copy.
- The shared script also builds the controls (button, field, pill, link and the rest), and the module copies of those helpers were removed.
- The primary button is the preset colour ringed in the text colour (option B of three shown side by side). The 14% tint means a live or pressed control only.
- A module ships its own stylesheet through admin_styles / crew_styles; no <style> in its templates, no CSS from its scripts.
- The written exceptions are committed at the audit folder's spec file; the rest of the audit stays ignored.
- The ratchet counts literal px font sizes, hex/rgb colours and px radii per file and may only go down; the browser test checks drawn button/field heights and text sizes on every operator page in all four presets.

## Tried and ruled out
- Components in the panel stylesheet only: the sign-in pages would keep redrawing buttons.
- CSS-only, leaving the copied JS helpers: Justin wanted them shared too.
- A filled-no-ring or tinted primary: the tint nearly vanished on Brutalist light.
- Rewriting the viewer, overlay, rundown, signage, board/cue canvases, tally and stage: they draw the operator's own looks or read across a room; listed as exceptions and frozen by the ratchet instead.

## Watch out
- Buttons in a .row used to stretch to the row (Audio's Save was 350x56); they now size to their label.
- Pages served on their own listener, through the remote tunnel or offline cannot load the tokens file and keep their own font stack.
- An inline preview widget restyled buttons and showed no colours; mock real CSS in the browser pane instead.

In the code: app/static/tokens.css (scales), app/static/components.css, app/static/relay-ui.js (button, buttons, field, pill, link, kv, note, shown), Module.admin_styles / crew_styles in app/modules/__init__.py, tests/test_style_contract.py + tests/style_contract_baseline.txt (--rebaseline after a clean-up), tests/_style_probe.py (EXEMPT) used by tests/test_browser.py, docs/ui-audit/standardization-spec.md, docs/modules.md "Shared styles for module pages", AGENTS.md rule.
