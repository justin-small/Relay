---
title: "The NDI viewer is MJPEG through Pillow on the operator port, and the signage takeover widgets are a list and a Return button"
author: Justin
date: 2026-11-17
branch: ndi-discovery-server
cost: { minutes: 12, tokens: 193340 }
id: l-fcf712b1
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/viewer.py
  - app/modules/signage/__init__.py
  - app/static/layout-render.js
---

## Decided
Justin answered questions on issues 296 and 207 (2026-11-17). #207 was already built (PR 292), so only its board widgets were added. #296: everything including tally border and audio meter; Pillow added as a pinned dependency; viewer served on the operator port only behind ndi.view; stacked on the ndi-discovery-server branch.
In the code: app/modules/ndi/viewer.py (one feed per source, shared by all browsers), app/modules/signage/__init__.py (board_widgets), core "frame" board kind in app/static/layout-render.js.

## Tried and ruled out
Viewer on the public viewer port like signage screens: offered, not chosen. TestClient cannot hold an endless MJPEG response, so the stream test calls the route and reads its body.

## Watch out
The audio meter (cyndilib AudioRecvFrame, runtime.py) is written from its docs and not proved on real NDI; trouble there only blanks the meter. A public board's NDI tile shows nothing because the stream needs a sign-in.
