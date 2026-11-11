---
title: "The 10-inch Waveshare panel is on hold: not built, not offered firmware; rotation from admin restarts the panel"
author: Justin
date: 2026-11-10
branch: panels-122-crowpanel
cost: { minutes: 11, tokens: 269595 }
id: l-5b6f41b2
scope: repo
status: active
src: agent
touches:
  - app/panelfw.py
  - firmware/relay-display/build.sh
  - app/panels.py
  - firmware/relay-display/main/panel_link.cpp
  - app/static/admin.js
---

## Decided
- Development of the Waveshare ESP32-P4 10.1" is paused (Justin, 2026-11-10). Issue #122 was done for the CrowPanel 7" only. The P4 code stays in the tree but is switched off in two places: build.sh refuses `--board waveshare-p4-10.1` and skips it in the native checks unless `--on-hold` is passed, and app/panelfw.py marks the board `on_hold`, so Relay refuses its images (by chip id), lists no firmware row for it and offers its panels nothing.
- Firmware is stored per board (panel-firmware/<board>/), and the board is read from the image's chip id, since every board's image carries the same project name. A panel that reports no board is a CrowPanel.
- Rotation from admin is level-triggered, not by revision: there is no rotation control at the screen, the panel's screen and touch are set up once at boot, so the panel saves the rotation and restarts only when it differs from what it booted with.
- Reboot goes on the event stream only, never in a check-in reply, so a panel cannot get stuck restarting; Relay answers 409 when no stream is open.

## Watch out
- To bring the 10" back, take it out of `on_hold` in build.sh and set `on_hold=False` in panelfw.BOARDS.
- `caption_json::read_string` truncates silently at the buffer; the service type in set_feed is length-checked on the raw value so a long id is refused, not cut into another id.

In the code: firmware/relay-display/build.sh (on_hold), app/panelfw.py (BOARDS, _migrate), app/panels.py (reboot, rotation), firmware/relay-display/main/panel_link.cpp (set_rotation, restart), panel_link_format.h.
