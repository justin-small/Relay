---
title: "Panel Service view: big state never on a task's stack, the Service feed reuses a caption slot, and plain LVGL boxes swallow taps"
author: Justin
date: 2026-11-10
branch: panel-service-view-82
cost: { minutes: 23, tokens: 293749 }
id: l-c4b7823c
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/static/admin.js
  - app/templates/admin.html
  - tests/test_pccontrol.py
  - firmware/relay-display/main/service_format.h
  - firmware/relay-display/main/service_view.h
  - firmware/relay-display/main/service_view.cpp
  - firmware/relay-display/tests/test_service_view.py
---

## Decided
- The Service feed is a caption slot pointed at /stream?stream=service: same socket, backoff and silence clock; the slot holds a service_state instead of caption lines. Each message is the whole state, so nothing resumes.
- The menu offers a Service feed only while Relay's service mode (check-in reply `service`, `service_mode` event) is not off; memory only, re-sent on every check-in.
- Only the 36 px caption font draws accents; smaller Montserrat text is folded to ASCII ("José" -> "Jose") rather than adding another generated font (flash).

## Tried and ruled out
- `*s = service_state()` to clear the state: it builds a ~20 KB temporary on the 6 KB caption task stack and corrupted memory; the panic showed up later inside LVGL's refresh, far from the cause. service_reset() clears in place.

## Watch out
- The console task stack is 4 KB: `feeds` overflowed it once with a feeds_view on the stack (now static).
- lv_obj_create objects are clickable by default; a press goes to the deepest clickable child, so a box inside a tappable row eats the tap unless its CLICKABLE flag is removed. The emulator test for notes-on-tap is skipped (user's call); it is a bench item.
- The emulator suite refuses a build older than any source file (staleness check): rebuild with build.sh --qemu after every edit.

In the code: firmware/relay-display/main/service_format.h (service_reset, service_apply), service_view.cpp, captions.cpp (on_service_event), panel_link.cpp (set_service), tests/test_service_view.py.
