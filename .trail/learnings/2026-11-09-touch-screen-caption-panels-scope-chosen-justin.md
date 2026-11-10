---
title: "Touch-screen caption panels: scope chosen for the Elecrow CrowPanel 7-inch"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 5, tokens: 64999 }
id: l-b4a2c356
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/hub.py
  - app/engine.py
---

## Decided
Discovery (no code yet) for wall/desk touch panels that show Relay captions, modelled on the VXP compact room display.
- Hardware: Elecrow CrowPanel Advance 7.0" (DIS02170A) V1.2+, ESP32-S3, 800x480, the same board VXP uses. One size only.
- Firmware lives in this repo (e.g. firmware/relay-display/) and copies what it needs from VXP's vxp-compact-display (panel driver, backlight, Wi-Fi + setup hotspot, NVS settings, BOOT recovery, OTA, QEMU tests). No shared code with VXP.
- Latin script only, so one small LVGL font; no CJK, no right-to-left.
- Feed choice: the operator sets a default per panel from Relay admin, and a person at the screen can change it from an on-screen menu (Transcription + live languages). The choice survives a reboot.
- One feed full screen, or optionally two side by side (original + one translation, like /both).
- Scrolling: follows live text; a touch scroll-back pauses it and shows "Jump to live". The panel keeps about 200 lines in PSRAM.
- Transport: plain HTTP on the LAN, no pairing (unlike VXP's pinned TLS). Panels find Relay automatically (mDNS).
- Admin gets: a list of connected panels, setting a panel's feed, and pushing panel firmware.

## Watch out
- The caption feed needs no new API: panels use the existing SSE /stream and /api/targets. The new API is only for the panel registry, remote feed setting and firmware.
- mDNS from inside Docker Desktop (macOS/Windows) doesn't reach the LAN, so auto-discovery needs the host to advertise it or a typed-address fallback.
- Firmware over plain HTTP means the panel must check a signed image (as VXP does); otherwise anyone on the LAN could flash it.
- Relay keeps ~40 lines per feed, so a rebooted panel can only refill 40 of its 200 lines unless the server keeps more.
- VXP's firmware never reads the GT911 touch chip; the touch driver is new work.

In the code: the feed is GET /stream?stream=transcription|translation&lang=XX with ?last_id= resume (app/main.py stream, app/hub.py), and the live set is GET /api/targets plus "targets" SSE messages (app/engine.py targets_payload). VXP firmware: ~/Downloads/Unifi-access/firmware/vxp-compact-display.
