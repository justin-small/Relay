---
title: "Overlay builder (#139): named saved overlays plus the long URL, its own admin page, any two languages stacked, bundled OFL fonts, and a preview that is the rea…"
author: Justin
date: 2026-11-12
branch: overlay-builder-139
cost: { minutes: 11, tokens: 213314 }
id: l-bcdcd631
scope: repo
status: active
replaces: l-4bab8610
src: agent
touches:
  - docs/mockups/overlay-builder.html
  - app/overlays.py
  - app/static/overlay-fonts.css
  - app/static/overlay-builder.js
  - tests/test_overlays.py
  - app/main.py
  - app/templates/viewer.html
  - app/static/viewer.css
  - app/static/viewer.js
---

## Decided
- Justin chose (Nov 12) for issue 139: overlays are saved and named on the server with a short link (/overlay/<id>), so editing in admin reaches OBS/vMix with no re-paste; the long query-string URL keeps working for hand-built links, and query values still apply on top of a saved one.
- The builder is its own admin sidebar page, "Broadcast overlay" under Output, with Build and Setup guides tabs; Viewer links keeps a default overlay row that points to it.
- The two-languages layout stacks any two the operator picks; with no second language (or the same one twice) the second feed is the original speech.
- Fonts are bundled (Atkinson Hyperlegible 700 and Source Serif 4 600, OFL, latin + latin-ext) because the overlay runs offline on a church LAN; other scripts fall back to the host's fonts glyph by glyph.
- The admin preview is an iframe of the real /overlay page with ?demo=1, which plays sample sentences through the same Pane.apply a live feed uses. A hand-drawn copy of the overlay in admin would drift from what goes on air.
- Pop-on implies finished lines only; a full block of `lines` sentences is cleared by the next one.
- A saved overlay stores only what differs from its preset, so later preset changes reach it.

## Tried and ruled out
- Copying a builder from ProDeck or ChurchBoard: neither has one. ProDeck only has a lower-third preview card; ChurchBoard's useful pattern is an editor with a live preview on demo data.
- Outline with -webkit-text-stroke: it eats into glyphs and paint-order is missing from the older Chromium inside OBS and vMix, so the outline is eight text-shadows.

## Watch out
- The overlay is core output, not an integration, so overlays.py is in CORE_FILES, not app/modules.
- Every value lands in CSS or markup: preset values go through the same clean() as URL values. A preset written with a bare hex background once rendered `--ov-bg: 000000` until that was done.
- `stream=both` on an old URL used to show transcription then translation; it now stacks translation over the original.
- Not tried in OBS, vMix, ProPresenter or on an ATEM; checked only in Chrome via the builder preview and tests.

In the code: app/overlays.py (options, presets, samples, named-overlay validation), page_overlay / page_named_overlay / admin overlay API in app/main.py, overlay branch in app/templates/viewer.html, overlay CSS in app/static/viewer.css, demo + finals + pop-on in app/static/viewer.js, builder in app/static/overlay-builder.js, tests in tests/test_overlays.py.
