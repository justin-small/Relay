---
title: "The panel shows the Relay logo from power-on until its feeds are connected; the colour bars are only the bench command's now"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 23, tokens: 289903 }
id: l-fed0e83c
scope: repo
status: active
src: agent
touches:
  - app/pccontrol.py
  - firmware/relay-display/main/service_control.cpp
  - firmware/relay-display/main/service_control.h
  - firmware/relay-display/main/service_format.h
  - firmware/relay-display/main/service_view.cpp
  - firmware/relay-display/main/test_native.cpp
  - firmware/relay-display/tests/test_service_buttons.py
  - tests/test_pccontrol.py
  - firmware/relay-display/main/screen.cpp
---

## Decided
Justin asked for the logo animation on the glass from power-on until the feeds the panel shows are fully loaded. The boot picture goes up right after the display starts, and every other picture asked for while it is up is held, not drawn. It comes down no sooner than the 2.4 s the logo takes to be written; then at once for a lines screen (setup network, failed network), and for the caption view when every shown feed is connected, or after 15 s when one is not (the caption view's header then says why). On the CrowPanel with a stored network and Relay up it came down after 7.6 s.

The animation is drawn by LVGL from two alpha-only images (bubble, the word) and plain rectangles, not played from a GIF.

## Tried and ruled out
Playing the GIF itself with LVGL's GIF decoder: it keeps a full-colour canvas of the whole picture in RAM (about 260 KB for the logo alone), and the emulator's module has no PSRAM.
The colour bars as the boot's first picture: they are still the `bench` console command, but the boot now lights the glass on the plain ground.

## Watch out
Not run through the emulator suite (Justin asked for a USB flash only). Tests that expect the colour bars or the caption view straight after boot will need the boot picture taken into account.
The logo images are generated. Change the drawing in brand/marks.py and rerun the scripts; do not edit the C file.
The lockup is a fixed 384 by 112 pixels, not scaled per board.

In the code: firmware/relay-display/main/boot_view.cpp, boot_logo.c (from brand/panel_assets.py), screen.cpp (screen_show_boot, ask, boot_may_end, feeds_loaded, kBootMaxMs), display.cpp (display_light_up), main.cpp. Web and docs logo files come from brand/build.py.
