---
title: "Dante and AES67 inputs are named in the input picker through a provided hook, and the feeding channel is read from this computer's own Dante device"
author: Justin
date: 2026-11-20
branch: dante-input-264
id: l-457682f2
scope: repo
status: active
src: agent
touches:
  - app/audio.py
  - app/main.py
  - app/modules/dante/__init__.py
  - app/modules/dante/service.py
  - app/static/admin.js
  - app/templates/admin.html
---

## Decided
Issue 264, Justin's answers: a new user-guide chapter; cover all four routes (Dante Virtual Soundcard, Via and AVIO; openaes67; the GPL macOS driver, linked only; Inferno), each marked not tested; show which Dante channel feeds Relay's input by reading the local Dante device's subscriptions (#258), blank when unreadable. Core asks the provided `audio.input_labels` and gets a kind and per-channel feeds; the saved device label is never changed because it is the stored selector.

## Watch out
Detection is by sound-card name only, so a renamed card shows no mark. The feed line depends on the unverified subscription layout in arc.py. No route was followed on real hardware, so the guide marks all as not tested; Inferno's licence was not checked.

In the code: `app/audio.py` (`label_inputs`), `app/modules/dante/inputs.py`, `docs/guide/src/55-dante.md`, `tests/test_dante_inputs.py`.
