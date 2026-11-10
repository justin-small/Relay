---
title: "Bench-flashing the CrowPanel from a Mac: use the dev image at 115200 baud, and run Relay locally with RELAY_DEMO=1 for test mode"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 11, tokens: 73673 }
id: l-4839a156
scope: repo
status: active
src: agent
touches:
  - run.py
  - app/engine.py
---

## Decided
Bench panels get the --dev image (throwaway-signed). Relay "test mode" is rehearsal mode, RELAY_DEMO=1: canned captions, no billed session.

## Tried and ruled out
Flashing the plain release image: it is unsigned, so the app aborts at start ("No signatures were found for the running app") and loops, with a fully black screen (no backlight). Flashing at 460800: the CH340 port (/dev/cu.wchusbserial*) dropped on the baud change ("Device not configured").

## Watch out
A black screen with no backlight can be this abort, not a board revision problem; read the boot log first. The panel setup page once showed "Could not start the connection" and then worked on retry; cause not found. Rehearsal mode run from the repo (.venv/bin/python run.py) binds loopback only and sends no mDNS announce, so a panel cannot find it; that needs the Docker setup.

In the code: esptool --chip esp32s3 -p /dev/cu.wchusbserial10 -b 115200 write-flash --flash-mode dio --flash-size keep --flash-freq 80m 0x0 firmware/relay-display/build-dev/relay-display.bin. Release signing: build.sh --sign key.pem (needs ESP-IDF, not installed on this Mac). RELAY_DEMO read in app/engine.py.
