---
title: "Touch is read straight from the GT911 and tries both I2C addresses, with the STC8 touch-activate step left out"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 7, tokens: 115651 }
id: l-468b377e
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/touch_format.h
  - firmware/relay-display/main/touch.h
  - firmware/relay-display/main/touch.cpp
  - firmware/relay-display/main/display.cpp
  - firmware/relay-display/main/display.h
  - firmware/relay-display/main/config.h
  - firmware/relay-display/main/idf_component.yml
  - firmware/relay-display/main/CMakeLists.txt
  - firmware/relay-display/main/screen.cpp
  - firmware/relay-display/main/screen.h
---

## Decided
The panel reads the GT911 itself over the shared I2C bus instead of using the esp_lcd_touch_gt911 component. It probes 0x5D then 0x14 and checks the product ID is "911", because the address depends on the INT level at reset, which the firmware does not drive. The reader sits behind a small interface so the emulator can feed fake touches from the serial console. Rotation 180 mirrors both axes.

## Tried and ruled out
Sending the STC8 touch-activate command: its value is not in the repo and I would not guess it.

## Watch out
If the GT911 does not answer at either address on the bench, the missing STC8 touch-activate command is the first suspect. The emulator tests cover rotation and LVGL's pointer, not the chip, its address or its I2C traffic.

In the code: firmware/relay-display/main/touch.cpp, touch_format.h, tests/test_touch.py. Emulator console: `touch <x> <y>`, `touch up` (provision.cpp console_task, QEMU builds only).
