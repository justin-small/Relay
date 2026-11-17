---
title: "REAPER auto-records with the stream on air and/or schedule windows, marks each song, and leaves displays to a follow-up"
author: Justin
date: 2026-11-17
branch: reaper-413
id: l-1e744002
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/modules/propresenter/propresenter.py
  - app/modules/propresenter/slidetags.py
  - app/modules/propresenter/tagactions.py
  - docs/guide/src/54-propresenter.md
---

## Decided
Issue 413, 2026-11-17, Justin's choices: auto-record is two per-computer checkboxes, "while the stream is on air" and "during schedule windows" (either or both), with a stop delay. A marker is dropped for each new ProPresenter presentation, named after it. Showing recording on the status view and relay-display moved to issue 450.
Live OSC findings on Justin's Mac: /record, /stop are triggers; /action 40157 inserts a marker and /lastmarker/name names it; /action 41743 resends the whole state (used as heartbeat); /device/track/count raises the per-track bank (stock is 8); empty bank slots report as "Track N" at -150 dB with width "0W".

## Tried and ruled out
"Captioning running" as the auto-record trigger: offered, not chosen. A marker per slide: hundreds per service. Publishing REAPER into the status feed now: its on_air means a live stream, so a recording would make the stream look on air.

## Watch out
Silence level SILENT_VU = 0.1 is a guess (live read: 0.5 at -27 dB). REAPER on the same computer sends to 127.0.0.1, so the listen socket binds 127.0.0.1 then; otherwise the default network's address.

In the code:
app/modules/reaper/service.py (Machine.tick, problems), app/modules/reaper/client.py (Link, Track.real), core hooks control_recording and presentation_listeners in app/modules/__init__.py.
