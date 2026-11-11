---
title: "A still panel was redrawing its header 20 times a second; views now skip setting a label, colour or visibility to the value it already has"
author: Justin
date: 2026-11-11
branch: ci-step-timeouts
cost: { minutes: 7, tokens: 165162 }
id: l-24d66e79
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/caption_view.cpp
---

## Decided
The CrowPanel flickered in horizontal bands all the time, even on a still screen. Every build setting vxp needed for this (bounce buffer, code in PSRAM, 240 MHz, 120 MHz PSRAM, panel interrupt on core 1) was already here and the generated sdkconfig matched vxp's, so settings were not the cause. The dev build's diag log on a still caption screen showed 77 LVGL flushes a second and the lvgl task at 40-45% CPU. The caption view's 50 ms tick set the header's state label and dot colour every tick, and LVGL 9.2.2 redraws on every lv_label_set_text, local style set and un-hide whether or not the value changed. That was the 56 px header (four 16-row strips) redrawn, the two frame buffers flipped and the changed area copied PSRAM to PSRAM, twenty times a second. That constant PSRAM traffic is the likely thing the bounce-buffer refill loses to (my reading; the bench had already shown any delay to that refill is a band of flicker). The tick now sets nothing to the value it already has.

## Tried and ruled out
Porting more settings from vxp: nothing left to port, the two sdkconfigs differ only in LVGL's allocator and mDNS. Blaming the two-frame-buffer change of Nov 10: Justin says it flickered before that too.

## Watch out
Built, not yet flashed or seen on the glass when this was written. Check with the diag log: on a still screen "lvgl N flushes" should be near zero, not about 540 per block. Anything new that runs on a view's tick must compare before it sets. Real redraws (scrolling, the boot logo) still do the flip and copy, so flicker only while something moves would point at the double-buffer copy next. The bench network also delivers about 28 mDNS packets a second, which the dev build logs one by one.

In the code: firmware/relay-display/main/caption_view.cpp (set_text, set_bg, set_shown, used by refresh_header, refresh_service_header, refresh_alert_header, show_jump), service_view.cpp (show_jump). Serial log read with pyserial at 115200 with dtr and rts off, which does not reset the panel.
