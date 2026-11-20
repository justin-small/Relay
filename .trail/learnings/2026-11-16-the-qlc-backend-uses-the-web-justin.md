---
title: "The QLC+ backend uses the web API with buttons, sliders and optional functions, and reads state over one held connection"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 3, tokens: 54735 }
id: l-ceb10fc9
scope: repo
status: active
replaces: l-c91adb2d
src: agent
touches:
  - app/modules/lighting/qlcplus.py
  - app/modules/lighting/routes.py
---

## Decided
Issue 381, 2026-11-16, Justin's choices: reuse the websockets library already shipped (routed through class "lighting"); ignore the password setting (QLC+'s web API has none); a button press sends its id with 255 once and release is not supported; default port 9999. Justin then asked for every extra the API allows: sliders (ids s<id>, set as 0 to 100 percent), functions (ids f<id>, toggled, behind a setting that is off by default) and live state. Presses and level changes each use a short connection and are never retried. State comes from one held connection that re-reads on QLC+'s FUNCTION pushes and every 5 seconds.

## Tried and ruled out
- A new WebSocket dependency: not needed.
- QLC+ OSC and MIDI routes: every button must be mapped by hand and state is unreliable.
- Press then release for every button: a toggle button would toggle twice.
- Frames, pages and a blackout command: the web API has none. A blackout is a button in the QLC+ project.
- Waiting for widget pushes: QLC+ pushes only FUNCTION events to other clients, never button or slider changes, so state is re-read instead.

## Watch out
- Python 3.10 is supported: asyncio.timeout does not exist, use asyncio.wait_for.
- Checked on a real test QLC+ (buttons, a slider, functions, state from another client) and against a fake server in tests. Buttons 3 and 8 on that setup are unused and never report on.
- Only widgets of type Button or Slider become cues. QLC+ must run with -w.

In the code: app/modules/lighting/qlcplus.py, tests/test_lighting_qlcplus.py, the level route in app/modules/lighting/routes.py.
