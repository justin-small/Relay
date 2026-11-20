---
title: Talkback is a module that listens to a bus lane and keeps its text out of the hub
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 8, tokens: 209236 }
id: l-2dcfe2f0
scope: repo
status: active
src: agent
touches:
  - app/modules/talkback/service.py
  - tests/test_talkback.py
  - app/engine.py
  - app/modules/reaper/__init__.py
---

## Decided
Issue 93, Justin's answers (2026-11-20): the TalkbackEngine is a module (app/modules/talkback), not core. Its audio is a bus lane chosen in its settings (the bus from #92 replaced a dedicated talkback input). Backend and language follow the main feed's until set. One PR covers engine, routes, settings, status with billed seconds, and an admin page.
The module owns its own AudioCapture and a private `talkback_lane` source (a LaneSource instance), so it never shares state with the main capture. Text lives in the module (ring, listeners, `modules.provide("talkback.lines")`), never in hub, which is what keeps it out of the feeds, recordings and exports.
In the code: app/modules/talkback/service.py, tests/test_talkback.py.

## Tried and ruled out
Publishing talkback on a hub stream and filtering it out of recordings and exports: one missed filter would leak crew chatter to viewers.

## Watch out
Scribe v2 has no speaker labels yet (issue 495). The capture's silence gate holds 0.6 s after speech, so a test must feed audio at real time.
