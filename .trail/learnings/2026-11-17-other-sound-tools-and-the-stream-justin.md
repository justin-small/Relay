---
title: "Other sound tools and the stream-mix report: modules publish through a core seam, and what was guessed"
author: Justin
date: 2026-11-17
branch: audio-lanes-92
cost: { minutes: 23, tokens: 163989 }
id: l-117df199
scope: repo
status: active
src: agent
touches:
  - app/boards.py
  - app/config.py
  - app/main.py
  - app/metering.py
  - app/mixreport.py
  - app/modules/open_sound_meter/__init__.py
  - app/modules/open_sound_meter/listener.py
  - app/modules/open_sound_meter/routes.py
  - app/modules/open_sound_meter/static/open_sound_meter.js
  - app/modules/open_sound_meter/templates/open_sound_meter.html
---

## Decided
Issue 149, Justin's go-ahead (2026-11-17): Open Sound Meter and ProdMesh RTA are modules that register a source with core's metering seam and publish readings; boards pick a source per widget. The stream-mix report is core: a stereo pair of bus lanes, one row a second, saved per engine session (one file per day when none runs), rehearsal rows tagged and left out of the verdict by default.

## Tried and ruled out
- Hosting ProdMesh inside Relay: no source or licence file is in the repo, so only the remote-connection half is built.
- A module opening its own UDP socket on the event loop: test_modules forbids it; the Open Sound Meter listener reads a routed netsock socket in a thread instead.
- Exact BS.1770 gating blocks: rows are one momentary value a second, so integrated loudness can differ from a hardware meter by a few tenths of a LU.

## Watch out
Open Sound Meter's packet shape and default port, and ProdMesh's field names, were guessed from the issue; unrecognised packets are kept on each module page so the real shape can be added. Left and right lanes must come from the same device or correlation is meaningless. Not run on real hardware.
In the code: app/metering.py (register_source/publish), app/mixreport.py, app/modules/open_sound_meter/, app/modules/prodmesh_rta/, tests/test_sound_modules.py, tests/test_mixreport.py.
