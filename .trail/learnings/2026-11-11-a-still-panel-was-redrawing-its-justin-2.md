---
title: "A still panel was redrawing its header 20 times a second and flickered in bands; views now skip setting a label, colour or visibility to the value it already h…"
author: Justin
date: 2026-11-11
branch: ci-step-timeouts
cost: { minutes: 1, tokens: 894 }
id: l-8da6a162
scope: repo
status: active
replaces: l-24d66e79
src: agent
touches:
  - firmware/relay-display/main/caption_view.cpp
---

## Decided
The CrowPanel flickered in horizontal bands all the time, even on a still screen. The caption view's 50 ms tick set the header's state label and dot colour every tick, and LVGL 9.2.2 redraws on every lv_label_set_text, local style set and un-hide whether or not the value changed. That redrew the header, flipped the two frame buffers and copied the area PSRAM to PSRAM twenty times a second, and that traffic starved the bounce-buffer refill. The tick now sets nothing to the value it already has. Confirmed on the glass by Justin (2026-11-11): bands gone; diag went from about 540 flushes per block to 0 on a still screen.

## Tried and ruled out
Porting more settings from vxp: all of them were already here; the generated sdkconfigs differ only in LVGL's allocator and mDNS.

## Watch out
Anything new on a view's tick must compare before it sets. The check is the dev build's diag line "lvgl N flushes": near zero on a still screen. The lvgl task still sits at about 16.5% CPU with nothing drawn; not traced.

In the code: firmware/relay-display/main/caption_view.cpp (set_text, set_bg, set_shown), service_view.cpp (show_jump). Serial log read with pyserial at 115200 with dtr and rts off, which does not reset the panel.
