---
title: The browser flasher ends at the checked write and asks for RESET; it does not reset the CrowPanel or wait for it to start
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 12506 }
id: l-314a1de1
scope: repo
status: active
replaces: l-83ed439e
src: agent
---

## Decided
Justin chose (2026-11-11, after flashing a real CrowPanel) to end the job once esptool-js has checked the write's MD5 and to tell the person to press RESET. Pressing RESET is fine with him.

## Tried and ruled out
esptool-js 0.7.1's `after("hard_reset")`: it only lowers RTS, never raises it first. Our own RTS pulse (DTR low, RTS high 100 ms, then low): the panel still did not restart from USB. Watching the console for the panel's `boot: relay-display` line, even re-reading after a stream error: the page saw the bootloader up to "esp_image: segment 3" and never the app's log, so it ended on "did not hear the panel start" every time.

## Watch out
Why the app's log never reaches the page over the CH340 is not known; the app does not use the console pins. Do not press RESET while the bar sits at 100% before the done message: esptool-js is still checking the MD5.

In the code: app/static/flash_steps.js flashBoard (ends after io.write), app/static/flasher.js, app/templates/flash.html (done message), tests/js/flash_steps.test.mjs.
