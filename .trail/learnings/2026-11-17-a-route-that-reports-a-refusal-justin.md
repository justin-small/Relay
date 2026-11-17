---
title: "A route that reports a refusal answers from a fixed table of sentences, even when the exception's text is already fixed"
author: Justin
date: 2026-11-17
branch: audio-metering-149
cost: { minutes: 6, tokens: 15189 }
id: l-1402b02d
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/metering.py
  - app/boards.py
  - app/config.py
  - app/mixreport.py
  - app/modules/open_sound_meter/__init__.py
  - app/modules/open_sound_meter/listener.py
  - app/modules/open_sound_meter/routes.py
  - app/modules/open_sound_meter/static/open_sound_meter.js
  - app/modules/open_sound_meter/templates/open_sound_meter.html
---

## Decided
Justin's PR 446 (2026-11-17): the calibrate route raises CalibrateError(reason) and answers from a table of sentences keyed by that reason.

## Tried and ruled out
Returning str(exc) from a ValueError whose message was a literal: CodeQL (required check) still reported "information exposure through an exception", because it follows the exception's text into the response.

## Watch out
Any new route that turns an exception into a response should look the sentence up in a constant table, not format the exception.
In the code: app/metering.py (CALIBRATE_SENTENCES, CalibrateError), app/main.py (admin_metering_calibrate).
