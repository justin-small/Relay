---
title: "Panel LIVE buttons act on the press, show Next at once, and queue a quick second tap instead of refusing it"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 5, tokens: 122838 }
id: l-2b757e68
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/service_format.h
  - firmware/relay-display/main/service_control.cpp
  - firmware/relay-display/main/test_native.cpp
  - app/pccontrol.py
---

## Decided
- Justin found the panel's Previous/Next/Take control slow and "Too fast" on quick taps. Relay's per-panel gap is now 0.25 s (was 1 s); the panel already sends one press at a time and Relay serializes across panels, so the gap only stops a flood.
- A tap made while a press waits for Relay is queued (one deep, latest wins) and sent after the answer, at least 300 ms after the last send. This replaces the earlier "a double tap is one press": a quick second Next is meant.
- Next/Previous move the highlight on the glass the moment they are pressed (only when Relay has control and LIVE is running), stepping over headers like Planning Center. Each later feed message whose current item moved counts as one answered press; a refusal or 5 s without word shows the real state again.
- The buttons fire on LV_EVENT_PRESSED (the bar does not scroll), fill the bar's height, and have a gap/2 extended click area. Buttons stay enabled while a press waits.
- The footer shows the last press's result on the left and the Service state ("Not live", who controls) on the right with its dot.

## Watch out
- trail check flags service_preview_step as "brings back service state": it edits the state in place and builds no temporary, so the stack rule is kept.

In the code: MIN_GAP_S in app/pccontrol.py, s_queued/take_queued in service_control.cpp, service_preview_step in service_format.h, press/reapply_preview/build_bar in service_view.cpp, test_service_buttons.py test_a_quick_second_tap_is_queued_not_dropped.
