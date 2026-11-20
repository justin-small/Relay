---
title: Planning Center input slots stay the existing board channels; the shared pickers replace the soundboard checkbox list and slots are offered through the rundown…
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 10, tokens: 168777 }
id: l-6492868e
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/rundown.py
  - app/modules/planning_center/static/pcchannels.js
  - app/static/relay-ui.js
  - tests/test_ui_contract.py
  - app/static/network_map.js
  - app/modules/planning_center/__init__.py
---

## Decided
Justin, 2026-11-19, issue 546: the existing Planning Center board channels (a name and number, a core channel link, assigned to positions per service type, random draw) are the intermediate value; do not build a second slot model. They stay in Planning Center and are offered to others through the existing plan.rundown_now seam (each mic entry now carries channel_ref) so a soundboard module can later rename channels. The term is "Input channel"; "Input slot" was the chosen name for the idea. When one entry has several input channels, the draw should pick one at random (not built yet).

## Tried and ruled out
- Evolving board channels into a new slot type, or building slots beside them: not chosen.
- Moving slots into core now: not chosen; core imports no module.
- All of a slot's channels belonging to one person: not chosen, pick one at random.

## Watch out
NDI, Audio bus, Intercom and Blackmagic pick module ids, not core refs, so migrating them to the shared pickers needs a data migration first.

In the code: app/modules/planning_center/rundown.py (effective_mics, build), app/modules/planning_center/static/pcchannels.js, app/static/relay-ui.js (portPicker, channelPicker, routeEditor), tests/test_ui_contract.py.
