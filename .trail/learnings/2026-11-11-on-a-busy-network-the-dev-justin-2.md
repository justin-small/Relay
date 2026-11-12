---
title: "On a busy network the dev build's packet trace makes the CrowPanel flicker; turn diag off before judging flicker on the bench"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 1251 }
id: l-cba23bd8
scope: repo
status: active
replaces: l-3861e3f4
src: agent
touches:
  - firmware/relay-display/main/diag.cpp
---

## Decided
After the still-screen redraw fix (LVGL at 0 flushes), the bench CrowPanel still flickered. Switching the dev diagnostics off over serial (`diag off`) stopped it on a still screen, and scrolling captions in rehearsal mode (RELAY_DEMO=1) showed no flicker either. Justin confirmed both on the glass (2026-11-11). On this office network the diag packet trace printed about 25 broadcast/mDNS lines a second (about 1000 lines in 40 s), and the diag task used 15-18% CPU. Judge flicker on a dev image only with diag off. Release images do not include diag.

## Tried and ruled out
Not traced further.

## Watch out
`diag off` lasts only until the panel restarts: the dev image boots with diag on. The 30 s heap_log in main.cpp still calls heap_caps_get_largest_free_block, but only on internal RAM. Suspect it only if the flicker comes every 30 s. The lvgl task sat at 25-28% CPU with 0 flushes; not traced.

In the code: firmware/relay-display/main/diag.cpp (log_trace, diag_set_mode, modes on/off/quiet/print/tasks/wifi), main.cpp heap_log. Send console commands with pyserial on /dev/cu.wchusbserial10 at 115200 with dtr and rts off (no reset), writing "diag off\r\n".
