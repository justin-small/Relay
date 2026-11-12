---
title: Capture recovers from an unplugged input by reopening it itself; old PulseAudio device names fall back to the default input
author: Justin
date: 2026-11-12
branch: feature/state-folder-180
cost: { minutes: 7, tokens: 78950 }
id: l-e4dc7a35
scope: repo
status: active
src: agent
touches:
  - app/audio.py
  - app/config.py
  - app/db.py
  - app/main.py
  - app/paths.py
  - app/static/admin.js
  - app/templates/admin.html
  - requirements.txt
  - tests/smoke_test.py
  - tests/test_modules.py
---

## Decided
An input that stops delivering audio for 2 seconds is treated as unplugged. The capture thread then closes the dead stream, rescans devices and reopens the saved input every 2 seconds, with no restart. Capture stays "running" meanwhile so the caption sessions stay up, and the admin page says "Waiting for input" with a fixed sentence. A device saved under a PulseAudio-era name ("PulseAudio: ...", ALSA pulse/default) uses the default input and shows a fixed notice on the admin page. A failed open appends a fixed microphone hint per OS.

## Tried and ruled out
Detecting a blocked microphone precisely: PortAudio reports a denied mic and a busy device the same way, so the OS hint is added to every open failure instead of guessed. Showing PortAudio's own error text: CodeQL flags it, so the page only gets fixed sentences.

## Watch out
A stream that keeps delivering silence after unplug is not noticed; the OS behaviour was not checked on real hardware or on Windows/Linux. Removing the Pulse path breaks Docker-on-Mac/Windows audio, whose compose files and Pulse start scripts are still in the repo.

In the code: app/audio.py (_reconnect, _input_lost, selection_notice, mic_blocked_hint, LOST_AFTER_S, RETRY_EVERY_S), tests/smoke_test.py ("unplug and replug").
