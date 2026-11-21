---
title: "All audio in and out of Relay should go through the bus, so the AES67 output was shipped as a stopgap"
author: Justin
date: 2026-11-21
branch: aes67-output-267
cost: { minutes: 1, tokens: 1320 }
id: l-dd006f92
scope: repo
status: active
src: agent
touches:
  - app/modules/aes67/output.py
  - app/audiobus.py
  - docs/modules.md
---

## Decided
Justin, 2026-11-21, while reviewing #267: the intended design is that every audio input and output goes through the 32-lane bus. The operator assigns sound devices to the bus once, and everything Relay plays (alert tones, spoken translation) is patched to bus outputs. This is issue #599. #267 shipped first with alert tones and speech sent straight to the AES67 stream, and moves onto the bus when #599 is built.

## Tried and ruled out
Waiting for #599 before shipping #267: not chosen; the AES67 sender is useful as a bus output either way.

## Watch out
Do not add a feature that opens a sound device or picks its own destination; build toward named destinations patched on the bus.

In the code: app/modules/aes67/output.py (`on_alert`, `speak`, `lane_sink`), app/audiobus.py, docs/modules.md "The 32-lane audio bus".
