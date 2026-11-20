---
title: "The OBS module connects to several OBS machines, switches scenes from admin and /screen, previews on admin, and sends captions per machine"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 8, tokens: 33018 }
id: l-19efe1ac
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/build.sh
---

## Decided
- Issue 250, 2026-11-16, Justin's choices: one module (app/modules/obs/) that connects to several OBS machines (e.g. a stream PC and a record PC); each is its own entry in the shared stream status.
- Scene switching on the module's admin page and on a /screen scene page with a per-page key, like the lighting cue pages.
- Preview: a small JPEG of the program scene on the admin page only, refreshed about once a second while the page is open, off by default. Never on the viewer port.
- The #140 OBS caption sender is built here, not just a hook: each OBS entry has its own "send captions" switch and language, off by default; only finished lines are sent, split to about 32 characters, over the same connection. The hub's single line_sink becomes a list modules add to and remove from, as #140 proposed.
- Health: warn over 1% dropped or skipped frames in the last minute, or reconnecting; error when OBS can't be reached or the output stopped unexpectedly; idle when connected and not streaming. Fixed thresholds, not settings.

## Tried and ruled out
- One OBS machine only: churches with a separate record PC need both.
- Only a call hook for captions, sender later: Justin wanted the sender in this issue.
- Adjustable health thresholds: a fixed rule is enough.
