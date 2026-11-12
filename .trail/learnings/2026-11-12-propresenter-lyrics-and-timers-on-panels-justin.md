---
title: "ProPresenter lyrics and timers on panels: the panel counts timers itself, a repeat is not a change, and three nested feed_options overflowed the feeds task"
author: Justin
date: 2026-11-12
branch: propresenter-138-panels
cost: { minutes: 150, tokens: 34940 }
id: l-e851d44f
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/main.py
  - app/ppcontrol.py
  - app/propresenter.py
  - app/static/ppcontrol.js
  - app/templates/admin.html
  - docs/guide/src/54-propresenter.md
  - tests/test_propresenter.py
  - ".github/workflows/release.yml"
  - app/alerts.py
---

## Decided
- Lyrics and timers are two more panel feeds like the Alert buttons (kFeedLyrics 5, kFeedTimers 6, {"stream":"lyrics"|"timers"}, no setting of their own), offered from a "propresenter":{"lyrics","timers"} object in the targets message and /api/targets. Timers are a separate stream, /stream?stream=pptimers&for=panel&panel=<id>, not an extension of the lyrics stream: the lyrics stream is licensed text with its own audience switch, timers are not.
- A message that says what the glass already says is not a change. `rev` is the follower's, and it moves whenever anything in ProPresenter does (a running timer moves it every second), so the panel compares what it shows (lyrics_state::changes, timers_state::changes) and never `rev`. Relay sends a timers message when a timer starts, stops, resets, is renamed or is changed under it (a jump of more than 2 s from where it was heading), and at the 15 s heartbeat, never once a second. The panel counts from arrival, down for a countdown and up for an elapsed one, floor toward zero, and goes grey after 40 s of nothing.
- Which way a timer counts is not in timers/current; Relay reads it from GET /v1/timers (elapsed_time key = up) when it meets a new timer and every 20 resyncs.
- The panel names itself in the timers request (panel=<32 hex>), so thresholds and the timers shown can be set per panel (stored entry's "timers" block, nothing pushed on the event stream) over the global propresenter.timers. The id picks settings, it is not a credential.
- Digits are generated, 0-9 : - only (tools/gen_digits_font.sh, 140 px and 80 px, about 60 KB), because Montserrat 48 is LVGL's largest, and sit in fixed-width cells, one label per character: the font's digits are proportional, and a ticking 1 would move its neighbours.

## Tried and ruled out
- Comparing rev to decide a redraw (the brief's wording): rev moves with timers, so a lyrics pane would redraw each second a timer ran.
- A feed_options_with_alert(feed_options_with_propresenter(feed_options_with_service(...))) chain in feeds.cpp: each by-value feed_options is about 1.5 KB and the feeds task has 6 KB, so the third nesting overflowed it (a stack overflow panic the moment a Lyrics pick was applied). Found by the emulator suite, not by the native checks or the build. The menu is now built in place (feed_options_add_*).

## Watch out
- ProPresenter 22 answers DELETE /v1/timer/<id> 204 once and 404 after, while the timer stays listed: a timer created by a test stays until it is deleted in ProPresenter.
- The 10 inch Waveshare image still does not compile: board_dsi_jd9365.cpp fails on a designated-initialiser macro in the managed esp_lcd_jd9365 header (-std=gnu++2b). Nothing this change touches; ninja -k 0 shows every other source, the new views included, compiles for the ESP32-P4.
- Not flashed to a panel: how the lyrics and digits read from across a room, and the amber and red on the glass, are bench items (firmware README).
