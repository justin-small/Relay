---
title: The NDI multiview is composed once on the server and shown as one picture; sending it out as NDI is left for later
author: Justin
date: 2026-11-17
branch: ndi-viewer-signage-widgets
cost: { minutes: 7, tokens: 59974 }
id: l-2bf2b681
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/multiview.py
  - app/modules/__init__.py
---

## Decided
Justin answered questions on issue 297 (2026-11-17): browser multiview only this pass, the server composes one MJPEG image per multiview (one encode however many browsers watch), the countdown box can be a time of day or the next service start, and work stays on the same branch name (reset to main, since #456 was already squash-merged).
In the code: app/modules/ndi/multiview.py (layouts, Compositor built on viewer.Feed), `Module.service_starts_at` plus the registry method in app/modules/__init__.py, Planning Center's override.

## Tried and ruled out
One MJPEG stream per box: offered, not chosen (a 4x4 would be 16 streams). Sending the composed picture as an NDI source: offered, not chosen; ndi_register_output exists but no output is built (#299).

## Watch out
view_max does not limit multiviews: a 4x4 holds up to 16 receivers. Tally uses the single-source camera map, so a source has one camera number everywhere. Composing runs about 14 frames a second at 1280 wide on this Mac with one source present; not tried with many live sources.
