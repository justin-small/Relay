---
title: "The sound desk on panels gets all three views, mic states on Status, a channel wall and a micboard, as its own issue after the desk drivers"
author: Justin
date: 2026-11-20
branch: audio-mixers-150
id: l-c2bc86cc
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/boards.py
  - app/main.py
  - app/modules/crew/__init__.py
  - app/static/board.js
  - app/static/layout-render.js
  - app/static/layout.css
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
Justin, 2026-11-20, after three mockups for issue 150: he liked all three panel views and wants every one, chosen per panel. The panel work is its own issue (569), not part of the Audio Mixers PR, so the desk drivers can be proved on real hardware first.

## Tried and ruled out
- Building one panel view in the #150 PR: left out; firmware is not built in CI and no panel was at hand to check it.

## Watch out
Views are read-only; moving the desk stays behind audio_mixers.control on the admin page and boards. Keep "unknown" apart from "muted" on the glass.

In the code: docs/mockups/panel-mixer/index.html (A status mics, B channel wall, C micboard); issue https://github.com/justin-small/Relay/issues/569.
