---
title: "Panel firmware updates: Relay checks the image is a panel's, the panel alone verifies the signature"
author: Justin
date: 2026-11-10
branch: panels-firmware-updates
cost: { minutes: 1, tokens: 64082 }
id: l-90048868
scope: repo
status: active
src: agent
touches:
  - app/panelfw.py
  - firmware/relay-display/main/firmware_update.cpp
  - firmware/relay-display/README.md
---

## Decided
Images travel over plain HTTP on the LAN, so the signature check is done only on the panel (esp_ota_end against the key built into the running firmware). Relay checks just that an upload is a Relay panel's ESP32-S3 app, whole, with a readable version, and signed at all. A failed or rolled-back report ends Relay's ask; asks are held in memory only.

## Tried and ruled out
Verifying the signature in Relay: it would need the public key there and would suggest the panel could trust Relay. Keeping the signing key in the repo: release keys live in the PANEL_FIRMWARE_SIGNING_KEY secret.

## Watch out
Emulator update tests need their own seed (nvs-update, Relay port 8100); sharing a seed let other boards answer the fake Relay. The suite must run with one TCG thread, since esp_ota_end remaps flash and aborts QEMU otherwise.

In the code: app/panelfw.py, firmware/relay-display/main/firmware_update.cpp, tests/test_update.py.
