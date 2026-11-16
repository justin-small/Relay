---
title: "Panel firmware builds, emulator tests and USB flashing run on the Windows PC through WSL Docker, with the release key kept off it"
author: Justin
date: 2026-11-16
branch: obs-studio-module
cost: { minutes: 17, tokens: 51590 }
id: l-fdabafc7
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/tests/test_feed_picker.py
  - firmware/relay-display/tests/test_service_view.py
  - firmware/relay-display/tests/test_settings.py
  - firmware/relay-display/tests/layout.py
  - firmware/relay-display/tests/seeds.py
  - firmware/relay-display/main/settings.cpp
  - firmware/relay-display/tests/test_caption_view.py
  - firmware/relay-display/tools/build_windows.py
  - firmware/relay-display/main/caption_view.cpp
---

## Decided
The Windows build PC builds the panel firmware in WSL2 (Ubuntu-24.04) with Docker Engine running CI's digest-pinned espressif/idf container, so build.sh runs unchanged. Flashing runs on Windows with esptool on the panel's COM port (CrowPanel = CH340K), reading the image through /mnt/c. The release key is never sent there (--sign refused); unsigned release images do not boot, so bench flashing uses --dev.
The emulator suite runs 8 workers with pytest-xdist --dist loadfile: 102 tests in about 5 min, was over 20 serially. build_update.py is skipped while the firmware sources and build-qemu's test key are unchanged.

## Tried and ruled out
Docker Desktop: needs a signed-in desktop session, not startable over SSH. Native ESP-IDF on Windows: build.sh is bash, and the build would differ from CI's.

## Watch out
CI does not run the emulator suite, so it drifts from the firmware. On 2026-11-16 eight tests had gone stale: the menu's always-last Alert buttons row, the rotation row added under the menu title (tests/layout.py places taps), the 65% UI scale (fixed pixel offsets), and the reworded rotation fallback. Run `build_windows.py test` after any panel UI change.

In the code: firmware/relay-display/tools/build_windows.py, tests/layout.py.
