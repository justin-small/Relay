---
title: "Panel firmware is copied from the VXP display, built and tested in Espressif's container"
author: Justin
date: 2026-11-09
branch: relay-display-firmware
cost: { minutes: 1, tokens: 4570 }
id: l-9b9d77b1
scope: repo
status: active
replaces: l-17957567
src: agent
touches:
  - ".github/workflows/firmware.yml"
  - firmware/relay-display/README.md
  - firmware/relay-display/.gitignore
  - firmware/relay-display/CMakeLists.txt
  - firmware/relay-display/build.sh
  - firmware/relay-display/components/relay_auth/CMakeLists.txt
  - firmware/relay-display/components/relay_auth/include/auth.h
  - firmware/relay-display/components/relay_button/CMakeLists.txt
  - firmware/relay-display/components/relay_button/include/button_policy.h
  - firmware/relay-display/components/relay_captive/CMakeLists.txt
---

## Decided
The CrowPanel firmware in this repo is a copy of the VXP compact display firmware (0.2.2), not a shared component, so the two can diverge. The README's "Copied from VXP" table records what came from where. LVGL's own Montserrat fonts replace the generated Geist fonts. The emulator suite runs locally only; CI builds the firmware and does not run it.

## Tried and ruled out
Running the emulator suite with Homebrew's qemu-system-xtensa: it is upstream QEMU 11 and has no esp32s3 machine. Setting LVGL options in an lv_conf.h: this ESP-IDF LVGL component reads Kconfig, so lv_conf.h was dropped.

## Watch out
In the code: `firmware/relay-display/README.md` (the copy table, the local test command and the bench checklist), `firmware/relay-display/sdkconfig.defaults` (the CONFIG_LV_* lines), `firmware/relay-display/tests/conftest.py`, `.github/workflows/firmware.yml`.
No ESP-IDF install is needed: `docker run espressif/idf:v5.4.4` builds all three images and its own qemu-system-xtensa runs the 32 emulator tests. The bench items (backlight, RGB timing, Wi-Fi, PSRAM) are untested on a board.
