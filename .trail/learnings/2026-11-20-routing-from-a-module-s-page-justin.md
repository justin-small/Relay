---
title: "Routing from a module's page takes the module's own permission, the audio bus got its own page, and an input-channel entry can hold several channels with one d…"
author: Justin
date: 2026-11-20
branch: epic-539-leftovers
cost: { minutes: 18, tokens: 377589 }
id: l-ae8df36c
scope: repo
status: active
src: agent
touches:
  - app/routing.py
  - app/static/audiobus.js
  - app/main.py
  - app/static/relay-ui.js
  - app/modules/ndi/static/ndi-router.js
  - app/modules/blackmagic/static/blackmagic.js
  - app/modules/planning_center/rundown.py
  - app/static/network_map.js
---

## Decided
Justin, 2026-11-19, finishing epic 539's leftovers (#546 pages, #543 port names):
- A route may be set or cleared by network.manage OR the permission the destination's module names in its routes.<id> API (`permission`): NDI ndi.manage, Blackmagic blackmagic.control, audio bus audio.manage. Chosen over network.manage only, so NDI/Blackmagic operators keep routing from their own pages.
- The audio bus had no admin page at all; Justin chose a new Audio bus page (lanes, outputs, crosspoints) over putting the matrix on the Network page or skipping it.
- NDI's router grid and the Videohub "all routes" table are now relayUI.routeEditor (scoped by provider and gear). A Planning Center board entry may carry up to 7 more input channels (`also_refs`); whoever holds the mic gets one, drawn from a seed of draw key + mic + person so it stays put for the service time.
- Ports get a Relay-only name on the Network page's switch port panel; switch ports cannot be renamed on the switch.

## Tried and ruled out
- Migrating Intercom onto the channel picker: Intercom defines its own talk channels and never picks one, so there was nothing to replace (the mockup's "rooms" were invented).
- Replacing the Blackmagic front-panel "press output, pick input, TAKE" control: it mimics the hardware panel, so it stays.
- Moving tally.publish / register_source into gear.<id>: Justin decided in #545 they carry live readings, not channel lists.

## Watch out
- Saving the audio bus without a "matrix" key leaves core routes alone; sending "matrix" (even []) replaces them. The page never sends it.
- relayUI pickers share one list read for 2 s, so many pickers on one page (a row each) cost one request.
- Blackmagic, port rename and Planning Center extras were checked by tests and the browser suite only; no Videohub or switch was connected.

In the code: app/routing.py (permissions, may_route), app/main.py (_may_route, admin_audiobus_save), app/static/audiobus.js, app/static/relay-ui.js (routeEditor opts), app/modules/ndi/static/ndi-router.js, app/modules/blackmagic/static/blackmagic.js (routesFor), app/modules/planning_center/rundown.py (pick_ref, also_refs), app/static/network_map.js (portNameField).
