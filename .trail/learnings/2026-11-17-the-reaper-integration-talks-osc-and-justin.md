---
title: "The REAPER integration talks OSC and covers record transport, following the service, slide tags, markers and record state on displays"
author: Justin
date: 2026-11-17
branch: oauth-cloud-redirect
cost: { minutes: 2, tokens: 49049 }
id: l-4531fbe7
scope: repo
status: active
src: agent
touches:
  - app/modules/lighting/osc.py
---

## Decided
Issue 413, 2026-11-17, Justin's choices: REAPER is a module that speaks OSC (REAPER's built-in OSC control surface), as the Companion module does. Scope: start/stop record with state and time, auto record when the service goes live and stop at the end, slide tags, a named marker at each slide/song change, and record state shown on the panel/health and also on the status view and relay-display.

## Tried and ruled out
REAPER's HTTP web remote: it needs no UDP ports, but it offers fewer actions and coarser state than OSC.

## Watch out
Get the connection working against Justin's REAPER on his Mac before building features. On 2026-11-17 reaper.ini had an OSC surface entry on local port 9000, but REAPER wasn't listening on it (only 8080 for the web remote). Feedback needs the surface's device IP and port set.

In the code:
Planned setup: REAPER listens on 8000, sends feedback to 127.0.0.1:9001. The OSC encoder app/modules/lighting/osc.py belongs to the lighting module; the REAPER module needs its own (modules can't import each other).
