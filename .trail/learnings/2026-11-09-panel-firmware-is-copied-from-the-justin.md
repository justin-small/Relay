---
title: "Panel firmware is copied from the VXP display, built and tested in Espressif's container"
author: Justin
date: 2026-11-09
branch: relay-display-firmware
cost: { minutes: 1, tokens: 1904 }
id: l-17957567
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/README.md
  - firmware/relay-display/tests/conftest.py
  - ".github/workflows/firmware.yml"
---

## Decided
The CrowPanel firmware in this repo is a copy of the VXP compact display firmware (0.2.2), not a shared component, so the two can diverge. The README's "Copied from VXP" table records what came from where. LVGL's own Montserrat fonts replace the generated Geist fonts. The setup page's Relay address is optional and plain http.

## Tried and ruled out
Running the emulator suite with Homebrew's qemu-system-xtensa: it is upstream QEMU 11 and has no esp32s3 machine, so the console socket never opens. Setting LVGL options in an lv_conf.h: this ESP-IDF LVGL component reads Kconfig, so lv_conf.h was dropped.

## Watch out
In the code: `firmware/relay-display/README.md` (the copy table and the bench checklist), `firmware/relay-display/sdkconfig.defaults` (the CONFIG_LV_* lines), `firmware/relay-display/tests/conftest.py`, `.github/workflows/firmware.yml`.
No ESP-IDF install is needed: `docker run espressif/idf:v5.4.4` builds all three images, and its own qemu-system-xtensa (2025-08-17 build, aarch64) runs the 32 emulator tests. CI pins a newer x86 QEMU release copied from VXP, and that path was not run here. The bench items (backlight, RGB timing, Wi-Fi, PSRAM) are untested on a board.
