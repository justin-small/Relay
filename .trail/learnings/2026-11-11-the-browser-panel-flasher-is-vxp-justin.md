---
title: "The browser panel flasher is VXP's, copied; its image is the merged file uploaded on the page, written at 115200"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 8, tokens: 199020 }
id: l-e4370f1b
scope: repo
status: active
src: agent
touches:
  - app/static/flash_steps.js
  - app/static/flasher.js
  - app/templates/flash.html
  - app/templates/admin.html
  - tests/test_panels.py
  - app/panelfw.py
  - tests/js/flash_steps.test.mjs
  - app/main.py
---

## Decided
Justin asked for VXP's Web Serial board flasher (#1546 there) in Relay. It is copied, not shared: /admin/flash, flash_steps.js and flasher.js, and VXP's vendored esptool-js 0.7.1 (byte-identical to npm's bundle.js). Relay has no installer that stages images, so the operator uploads the merged relay-display.bin on the flasher page; Relay checks the signed application inside it at 0x10000 with the update image's check and stores it as panel-firmware/<board>/flash.bin, apart from panel.bin, never offered to a panel. VXP's "other board" refusal by project name is dropped: every Relay board has the same project name (relay-display-app), and the boards differ by chip, which refuseChip already refuses.

## Tried and ruled out
VXP's 921600 write rate: the CrowPanel's USB is a CH340 bridge that dropped off a Mac at 460800 on the bench, so the write stays at the ROM's 115200 (about two minutes). Reusing the uploaded relay-display-app.bin: it cannot start a blank board, which needs the bootloader and partition table.

## Watch out
Not tried on a real panel yet: the write, the reset and the boot-line watch over the CH340. The release build in build/ is unsigned and is refused (and would not boot). Web Serial is allowed only on the exact path /admin/flash.

In the code: app/panelfw.py (inspect_flash, store_flash, FlashInfo.manifest), app/main.py (admin_flash_page, /api/admin/panels/flash*, permissions_policy_for), app/static/flash_steps.js, app/static/flasher.js (FLASH_BAUD), app/templates/flash.html, tests/js/flash_steps.test.mjs (node --test tests/js/).
