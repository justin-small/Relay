---
title: "Quarter-turn rotation runs the panel UI upright at 480 by 800, turned in software, with two feeds stacked"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 1787 }
id: l-e69855e9
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/panelfw.py
  - app/panels.py
  - app/static/admin.js
  - app/templates/admin.html
  - firmware/relay-display/README.md
  - firmware/relay-display/build.sh
  - firmware/relay-display/main/CMakeLists.txt
  - firmware/relay-display/main/caption_view.cpp
  - firmware/relay-display/main/caption_view_format.h
---

## Decided
- Justin asked for 90 and 270 as well as 180. A quarter turn is done in software where rectangles are written to the frame buffer: LVGL is given a 480 x 800 display and every layout reads the picture's size at run time, not the panel's 800 x 480 constants. Touch is turned back with the inverse mapping. At 90 the picture's top runs down the glass's right edge, at 270 down its left; the operator picks whichever is upright for the mounting.
- Upright, two feeds sit one above the other: side by side would leave each pane about 240 px. The setup screen puts its QR code above the text. "Narrow" pane layouts switch on width under 440 px, not "smaller than the panel".
- The panel menu offers 0 / 90 / 180 / 270; another angle needs two taps because it restarts the panel.

## Tried and ruled out
- LVGL's own display rotation: this firmware writes LVGL's rectangles into its own double frame buffers, so the turn belongs in that write path, as the 180 one already did.

## Watch out
- The quarter-turn write reads the source pixel by pixel; it has not been timed on the glass, and a 90/270 picture had not been seen on the bench when this was written.

In the code: firmware/relay-display/main/panel_format.h (panel_physical_x/y, panel_logical_x/y, panel_logical_width/height), display.cpp (display_write_rect, display_width/height), touch_format.h, feed_format.h (feed_pane_rect_for stacked), caption_view.cpp (on_menu_turn), caption_view_format.h (kCaptionNarrowW).
