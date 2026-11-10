---
title: "A panel board is a header, an sdkconfig file and one driver; the P4 build needs three placeholder options to configure"
author: Justin
date: 2026-11-10
branch: board-seam-114
cost: { minutes: 26, tokens: 329687 }
id: l-180eca64
scope: repo
status: active
src: agent
touches:
  - README.md
  - docs/guide/src/52-planning-center.md
  - docs/guide/src/90-glossary.md
  - app/pccontrol.py
  - app/pclive.py
  - app/servicefeed.py
  - firmware/relay-display/README.md
  - firmware/relay-display/main/CMakeLists.txt
  - firmware/relay-display/main/boards/crowpanel_adv7.h
  - firmware/relay-display/main/config.h
---

## Decided
A second display board (#114, Waveshare ESP32-P4 10.1") is added as a board seam inside the one firmware, not a second copy. Everything a board differs in lives in three places, and the rest (LVGL, views, touch reader, Wi-Fi calls, captions, update) is shared. The 180 degree turn that used to be emulator-only is reused for the DSI panel, which cannot mirror. The default board keeps its build directory names so CI and the release paths did not move.

## Tried and ruled out
- Waveshare's own board support package as a dependency: it needs ESP-IDF 5.5 and brings its own LVGL port; this firmware is pinned to 5.4.4. Only its JD9365 init table was copied (Apache-2.0).
- Restricting the emulator option to the S3 in Kconfig: the component manifest reads that option on every chip, and an option that does not exist stops the configure.
- Declaring the missing esp_hosted options as bools: the newer esp_hosted manifests compare them with 1, so they must be ints, and they need a prompt or sdkconfig.json leaves them out.

## Watch out
- ESP-IDF 5.4.4's component manager reads every esp_hosted release while solving, and fails with "Missing required kconfig option after retry" on options only 2.x/3.x declare. The message does not name the option; the warning above it does.
- The JD9365 init table uses C compound literals, so it is a .c file; it does not compile as C++.
- Building one board after another rewrites managed_components and dependencies.lock in the project directory, so two board builds cannot run at the same time.
- The P4 image had not finished a compile when this was written, and nothing had been on a bench. The P4 draws portrait 800 x 1280; landscape is not written.

In the code: firmware/relay-display/main/config.h, main/boards/, main/board_panel.h, board_rgb_stc8.cpp, board_dsi_jd9365.cpp, sdkconfig.board.*, build.sh --board, main/Kconfig.projbuild (placeholders at the end), README.md "Boards".
