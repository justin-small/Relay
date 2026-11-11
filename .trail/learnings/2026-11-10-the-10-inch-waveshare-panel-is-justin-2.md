---
title: "The 10-inch Waveshare panel is on hold; panel rotation is set from admin or the screen, newest wins"
author: Justin
date: 2026-11-10
branch: panels-122-crowpanel
cost: { minutes: 6, tokens: 75972 }
id: l-05f58ad6
scope: repo
status: active
replaces: l-5b6f41b2
src: agent
touches:
  - app/panelfw.py
  - app/panels.py
---

## Decided
- Development of the Waveshare ESP32-P4 10.1" is paused (Justin, 2026-11-10). Issue #122 was done for the CrowPanel 7" only. build.sh refuses `--board waveshare-p4-10.1` and skips it in the native checks unless `--on-hold`; app/panelfw.py marks the board `on_hold`, so Relay refuses its images (by chip id), lists no firmware row for it and offers its panels nothing.
- Firmware is stored per board (panel-firmware/<board>/), the board read from the image's chip id. A panel that reports no board is a CrowPanel.
- Rotation can be changed at the panel (menu "Turn 180", two taps) and from admin. Justin asked for the screen control. It follows the feed rule: the operator's rotation carries a revision, the panel reports `rotation_rev` and keeps it in NVS (key rot_rev) because applying a rotation restarts the panel; a screen turn does not move the revision, so the next check-in does not undo it.
- Reboot goes on the event stream only, never in a check-in reply.
- The 65% UI scale (ui_scale.h) had been left uncommitted in another worktree and was brought onto the #122 branch; the Service "now" box was tightened (kNowH ui_px(100)).

## Tried and ruled out
- Sending the operator's rotation on every check-in (level-triggered): it would undo a turn made at the screen.

## Watch out
- To bring the 10" back, take it out of `on_hold` in build.sh and set `on_hold=False` in panelfw.BOARDS.
- caption_json::read_string truncates silently; the service type in set_feed is length-checked on the raw value.

In the code: build.sh (on_hold), app/panelfw.py (BOARDS), app/panels.py (_pending_rotation, reboot), main/panel_link.cpp (set_rotation, panel_link_turn_from_screen), main/caption_view.cpp (on_menu_turn), main/settings.cpp (settings_save_rotation).
