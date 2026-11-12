---
title: "The flasher's console must keep reading after a serial glitch, or it goes deaf when the panel's app starts"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 4, tokens: 19622 }
id: l-992ff37b
scope: repo
status: active
src: agent
touches:
  - app/static/flasher.js
---

## Decided
The boot watch restarts its read whenever the stream ends early, until the boot line arrives or the time runs out.

## Tried and ruled out
Reading once with esptool-js's rawRead, as VXP's flasher does: on the CrowPanel (CH340, 115200) the console showed the ROM and bootloader up to "esp_image: segment 3" and then nothing, so the page asked for RESET although the panel had already reset ("rst:0x1 (POWERON)", which is the page's own RTS pulse).

## Watch out
Cause inferred, not traced: the chip's clock change from bootloader to app glitches the line, Web Serial reports a framing or break error that ends the reader, and rawRead swallows it and returns. Not yet re-run on the panel after the fix.

In the code: app/static/flasher.js watch() (the read loop and stop()).
