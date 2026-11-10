---
title: "Panel Service view: big state never on a task's stack, the Service feed reuses a caption slot, and a row opens notes on any press length"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 1, tokens: 7653 }
id: l-be739cf5
scope: repo
status: active
replaces: l-c4b7823c
src: agent
touches:
  - firmware/relay-display/main/service_format.h
---

## Decided
- The Service feed is a caption slot pointed at /stream?stream=service: same socket, backoff and silence clock; the slot holds a service_state instead of caption lines. Each message is the whole state, so nothing resumes.
- The menu offers a Service feed only while Relay's service mode (check-in reply `service`, `service_mode` event) is not off; memory only, re-sent on every check-in.
- Only the 36 px caption font draws accents; smaller Montserrat text is folded to ASCII ("José" -> "Jose") rather than adding another generated font (flash).
- A row with notes listens for LV_EVENT_CLICKED, not LV_EVENT_SHORT_CLICKED (#125). LVGL sends SHORT_CLICKED only for a press shorter than its 400 ms long-press time, so a finger held a little longer opened nothing. CLICKED fires on release at any length and still not when the press scrolled.

## Tried and ruled out
- `*s = service_state()` to clear the state: it builds a ~20 KB temporary on the 6 KB caption task stack and corrupted memory; the panic showed up later inside LVGL's refresh, far from the cause. service_reset() clears in place.
- Leaving notes-on-tap as a bench-only item: the emulator can show it; the test now holds the finger 1 s, which is what caught the SHORT_CLICKED bug.

## Watch out
- The console task stack is 4 KB: `feeds` overflowed it once with a feeds_view on the stack (now static).
- lv_obj_create objects are clickable by default; a press goes to the deepest clickable child, so a box inside a tappable row eats the tap unless its CLICKABLE flag is removed.
- The emulator suite refuses a build older than any source file (staleness check): rebuild with build.sh --qemu after every edit.
- test_service_buttons.py::test_a_double_tap_is_one_press failed on 2026-11-10 with or without the CLICKED change; not investigated. Some service tests are timing-flaky in a multi-test run and pass alone.

In the code: firmware/relay-display/main/service_format.h (service_reset, service_apply), service_view.cpp (build_rows, on_row), captions.cpp (on_service_event), panel_link.cpp (set_service), tests/test_service_view.py (held_tap, test_notes_open_on_a_tap).
