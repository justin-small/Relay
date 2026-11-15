---
title: "A locked panel is unlocked at its screen by an optional operator PIN that relocks after minutes untouched, and Relay itself sends the operator's feed back afte…"
author: Justin
date: 2026-11-15
branch: panel-lock-return-126
id: l-46e5f17f
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/panels.py
  - app/static/admin.js
  - docs/guide/src/51-panels.md
  - firmware/relay-display/README.md
  - firmware/relay-display/main/CMakeLists.txt
  - firmware/relay-display/main/caption_view.cpp
  - firmware/relay-display/main/main.cpp
  - firmware/relay-display/main/panel_link.cpp
---

## Decided
Justin answered for issue 126 (2026-11-14): panels have no user account and do not pair; the panel's ID is its identity, like a service account. A locked panel's menu still opens and refuses a change (feeds, one/two feeds, rotation) with a note. The operator may set a PIN (4 to 8 digits) per panel: the right PIN at the screen unlocks it until the screen goes N minutes untouched (1 to 60, default 5), then it locks itself again. Five wrong PINs make the keypad wait a minute. Return to the operator's choice is off by default and set per panel, 1 to 60 minutes.

The return is done by Relay, not the panel: the panel reports `idle_s` (LVGL's inactivity time) on each check-in, and Relay sends the stored operator feeds as a new `set_feed` once a screen change has gone that long untouched. That way the split layout comes back too, and old firmware (no `idle_s`) is simply never sent back.

## Tried and ruled out
- Adding real pairing (operator approves, per-panel token): offered, not chosen.
- A PIN required only after idle, or switched on at a set time: offered, not chosen.
- Firmware-side return: the panel would need to keep the operator's feed across restarts; Relay already has it.

## Watch out
The PIN reaches the panel over plain HTTP in `panel_policy`, like the Planning Center control key: it stops someone at the screen, not someone on the network. Admin never shows the PIN back, only `pin_set`. A panel swapped to lyrics or a language by a slide-note tag is not returned (Relay's doing, not the screen's): `screen_choice` is cleared by any Relay push. Linux gcc flags snprintf truncation that macOS clang does not; the check-in body's number buffer had to grow. The keypad is not tried on a panel yet.

In the code: app/panels.py (policy, _return_due, screen_choice, idle_s), app/static/admin.js (drawer "At the screen"), firmware/relay-display/main/lock_format.h, panel_lock.cpp, caption_view.cpp (lock strip, keypad), panel_link_format.h (idle_s, policy).
