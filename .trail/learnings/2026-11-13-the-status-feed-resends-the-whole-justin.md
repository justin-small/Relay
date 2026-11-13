---
title: "The status feed resends the whole state every 5 seconds, only the input's coarse state counts as a change, and a test must not read an endless event stream thr…"
author: Justin
date: 2026-11-13
branch: status-view-224
cost: { minutes: 17, tokens: 255900 }
id: l-84c40a54
scope: repo
status: active
src: agent
touches:
  - app/engine.py
  - app/main.py
  - app/panels.py
  - app/static/admin.js
  - app/static/status.js
  - app/static/viewer.css
  - app/streamstatus.py
  - app/templates/status.html
  - docs/guide/src/51-panels.md
  - docs/modules.md
---

## Decided
- The status feed (224) is polled per subscriber once a second and sent when what it shows changes, plus the whole state again every 5 s. There is one message type, no separate tick, so a panel only has to compare what it shows. The input level moves every poll, so only its coarse state (off, lost, clipping, silent, ok) counts as a change, and the dB value rides along.
- Times go out as Relay's epoch seconds (started_at, live_since, server_time). The panel turns them into elapsed seconds as of the message and counts on from its own clock. Epoch seconds overflow the tenths reader the timers use, so the status has its own whole-seconds reader.
- The fake Relay in the emulator suite leaves Status off by default (as an older Relay would), so the other suites' exact menu lists are unchanged. The status tests switch it on.

## Tried and ruled out
- Reading /stream?stream=status through starlette's TestClient (client.stream, then breaking out): it hangs, because the endless generator never ends. Call the route function directly and read one frame from body_iterator, as the Service feed tests do.

## Watch out
- Adding feed kind 7 broke native checks that used "kFeedTimers + 1" or the literal 7 to mean "an unknown feed". They now use kFeedLast + 1; the next new feed kind moves kFeedLast.
- Not yet run through the ESP-IDF build or the emulator suite (Docker was not answering on 2026-11-13).

In the code: app/streamstatus.py (frames, signature, input_state), tests/test_streamstatus.py (first_frame), firmware/relay-display/main/status_format.h (status_read_seconds, status_apply), tests/fake_relay.py (status_offered).
