---
title: Caption list scroll looks like it jumps because the panel has one framebuffer and strips are written live; resource use is not the cause
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 1, tokens: 20344 }
id: l-24d27f4a
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/board_rgb_stc8.cpp
---

## Decided
Nothing changed yet. Diagnosis from the dev build's diag log on the CrowPanel 7": the panel holds 39 frames/s, LVGL's task runs 50-81% CPU while scrolling (about 24% idle), flushes are 1-2 ms, task stack is stable. Hardware is keeping up. Likely cause is tearing: one framebuffer scanned out continuously while LVGL's 16-row strips are copied straight into it with no vsync sync.

## Tried and ruled out
Resource limits: CPU, frame rate and stack are fine. Internal RAM sits low (about 19 KB free, low point 18 KB) with captions up, but it did not coincide with the jumping. Touch logging: the board build logs pointer events only at debug level, so a scroll leaves no touch lines in the serial log.

## Watch out
Not confirmed on the glass; the log cannot show tearing. A scroll triggers about 165 redraws/s against a 39 Hz refresh. Candidate fix is two framebuffers with vsync-aligned flush (about 770 KB of the 5.5 MB free PSRAM), which touches the display, screen and software-rotation paths and the emulator tests. Cheaper partial fix: poll touch faster and use a taller draw buffer.

In the code: `cfg.num_fbs = 1` in firmware/relay-display/main/board_rgb_stc8.cpp; `kDrawRows = 16` and PARTIAL render mode in main/screen.cpp; `display_write_rect` in main/display.cpp; LV_DEF_REFR_PERIOD=33.
