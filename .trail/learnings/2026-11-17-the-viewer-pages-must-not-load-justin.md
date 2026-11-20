---
title: "The viewer pages must not load the tokens stylesheet, because its colour scheme paints the overlay's transparent page dark"
author: Justin
date: 2026-11-17
branch: ui-standardize-463
cost: { minutes: 3, tokens: 24824 }
id: l-10b22139
scope: repo
status: active
src: agent
touches:
  - app/templates/viewer.html
  - app/static/viewer.css
  - app/static/tokens.css
---

## Decided
Issue 463, 2026-11-17: the viewer page template (captions, translation and the broadcast overlay) keeps its own font stack and does not load the tokens stylesheet. It is listed as an exception in the standardisation spec and in the style contract test's allowed stacks.

## Tried and ruled out
- Loading the tokens stylesheet on the viewer page to share the font token: the before/after screenshot sweep showed the overlay page (/overlay) turn from transparent to dark grey. The tokens file sets color-scheme per theme, which changes the browser's default canvas, and the overlay relies on a transparent page in OBS and other browser sources.

## Watch out
- Any page used as a browser source or keyed over video must not pick up color-scheme. Check /overlay?demo=1 in a before/after sweep after touching shared CSS.

In the code: app/templates/viewer.html, app/static/viewer.css (STACK_ALLOWED in tests/test_style_contract.py), app/static/tokens.css ([data-theme] color-scheme), docs/ui-audit/standardization-spec.md exceptions table.
