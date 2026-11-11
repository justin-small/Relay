---
title: "esptool-js 0.7.1's hard reset does not restart a CrowPanel, so the flasher pulses RTS itself and asks for RESET as a fallback"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 20364 }
id: l-83ed439e
scope: repo
status: active
src: agent
touches:
  - app/static/flasher.js
  - app/static/flash_steps.js
  - app/templates/flash.html
  - tests/js/flash_steps.test.mjs
---

## Decided
After the first real flash from the browser (2026-11-11), the panel did not restart by itself and the page then said "The write did not finish". The flasher now resets the board itself (DTR low, RTS high 100 ms, RTS low, as Python esptool does) with the console already open, then shows "press RESET, this page is still listening" and watches for 45 s. Anything that fails after the write is reported as "did not hear the panel start", never as a failed write.

## Tried and ruled out
esptool-js's `after("hard_reset")`: in 0.7.1 its HardReset only sets RTS low and never raises it first, so EN is never pulsed and the chip stays in download mode.

## Watch out
Not yet retried on the panel. If the CrowPanel's RTS does not reach EN at all, the automatic reset still will not work and the RESET prompt is the path. Do not press RESET while the progress bar sits at 100%: esptool-js is still checking the write's MD5 then.

In the code: app/static/flasher.js (watchForBoot, RESET_WATCH_MS, BUTTON_WATCH_MS, data-flash-reset-hint), app/static/flash_steps.js (flashBoard's boot step), app/templates/flash.html, tests/js/flash_steps.test.mjs.
