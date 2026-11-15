---
title: "The Intercom tab is a crew tab with livekit-client vendored in the module, and the crew page alone is allowed the microphone"
author: Justin
date: 2026-11-15
branch: intercom-tab-240
cost: { minutes: 8, tokens: 307484 }
id: l-c2c2cb99
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/templates/crew.html
  - app/modules/intercom/static/crew.js
  - app/main.py
---

## Decided
Justin chose (2026-11-15, issue 240) to vendor livekit-client by downloading it once: version 2.23.0 (chosen because it was over two weeks old), kept in the intercom module's static folder with its licence, never from a CDN so it works offline. The tab joins a room per channel through Relay's own /intercom path (livekit-client appends /rtc), keeps the microphone track disabled except while a talk lease is held, and publishes a clone of one captured microphone track per armed channel.

## Tried and ruled out
Putting the library in core's vendor folder: it is the intercom's, so the module owns it. Relying on the server's mute alone: the page also disables its own track, so a self-unmuted track sends silence.

## Watch out
Relay's global Permissions-Policy had microphone=() and silently blocked the intercom; the exact path /crew now gets microphone=(self), every other page stays blocked. Not tried against a real LiveKit server, on a real phone, with AirPods, or for the 150 ms mouth-to-ear target; the Browser pane has no service workers and no HTTPS trust for the self-signed certificate, so a plain-HTTP uvicorn run was used to see the page. The Remote chip waits for the remote module (#277).

In the code: app/modules/intercom/static/crew.js, templates/crew.html, livekit-client.umd.js, app/main.py (CREW_PERMISSIONS_POLICY), tests/js/intercom_crew.test.mjs
