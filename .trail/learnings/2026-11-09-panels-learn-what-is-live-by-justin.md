---
title: "Panels learn what is live by asking /api/targets, because the caption stream only announces changes"
author: Justin
date: 2026-11-09
branch: panels-feed-picker-split-view
cost: { minutes: 15, tokens: 309280 }
id: l-33d8bc20
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/feed_format.h
  - firmware/relay-display/tests/test_feed_picker.py
---

## Decided
The panel feed picker needs the list of live languages, but Relay's caption stream sends a `targets` message only when the live set changes, never when a client connects. So the panel asks `GET /api/targets` itself (every 2.5 s until it has an answer, then every 10 s) and also takes the stream's `targets` messages as they come.
A language that is not live falls back to the transcription while the saved choice is kept, so it switches back when the language returns. Only languages with `script` "latin" are offered.
The saved choice grew to schema 2 (second pane and split switch); a schema 1 blob is still read so a panel keeps its network when updated.

## Tried and ruled out
- Relying on the stream's `targets` alone: a panel that connects after the last change would never learn the list and its menu would stay empty.
- Treating a 4xx on the language as the only "not live" signal: Relay answers 200 for a known language that is not live, so the list is what says it.

## Watch out
- The emulator suite needs Espressif's QEMU fork. Homebrew's qemu has no esp32s3 machine; the espressif/idf Docker image carries one at /opt/esp/tools/qemu-xtensa (pip install pytest inside it).
- Saving the whole settings blob from the setup page would put an old feed choice back; it calls settings_adopt_feeds first.

In the code: main/feeds.cpp, main/feed_format.h, captions_fetch_targets in main/captions.cpp, tests/test_feed_picker.py.
