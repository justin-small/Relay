---
title: CrowPanel uses two framebuffers and flips once per LVGL pass; the 180 degree turn is done in software
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 15, tokens: 100904 }
id: l-9e104777
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/boards/crowpanel_adv7.h
  - firmware/relay-display/main/board_panel.h
  - firmware/relay-display/main/board_dsi_jd9365.cpp
  - firmware/relay-display/main/board_rgb_stc8.cpp
  - firmware/relay-display/main/display.cpp
  - firmware/relay-display/main/display.h
  - firmware/relay-display/main/screen.cpp
  - firmware/relay-display/main/test_native.cpp
---

## Decided
The CrowPanel RGB panel keeps two PSRAM framebuffers. Strips are copied into the back buffer, and the panel is flipped by passing that whole buffer to esp_lcd_panel_draw_bitmap. The flip is done once after each lv_timer_handler pass in lvgl_task, whatever LVGL reports. After a flip the new back buffer is brought up to date from the new front by copying the area just presented, once the next frame-complete event shows the old front is no longer scanned. Rotation 180 is done in software while writing strips, so kPanelTurnsInSoftware is now true for this board.

## Tried and ruled out
Flipping on lv_display_flush_is_last: a removed feed pane stayed up and a new service feed stayed blank, so redraws were being written to the back buffer and never shown. Relying on esp_lcd_panel_mirror: in IDF 5.4.4 a whole-buffer draw_bitmap only switches cur_fb_index, skipping the copy path that applies the mirror.

## Watch out
The driver switches buffers at the end of the frame being scanned (bb_fb_index = cur_fb_index at the bounce wrap), so wait one frame-complete event before touching the old front. Software turn and the buffer copy raised the slowest flush from about 1-2 ms to 12-50 ms. Not run on the emulator suite or on the Waveshare P4 board, which has no framebuffers and just returns not supported.

In the code: display.cpp (put, present, settle), screen.cpp (lvgl_task calls display_present), board_rgb_stc8.cpp (num_fbs = 2, board_panel_frame_buffers), boards/crowpanel_adv7.h, test_native.cpp (check on kPanelTurnsInSoftware).
