---
title: "The Intercom tab is a tab of the crew module, livekit-client is vendored in the intercom module, and a module names the pages that may use the microphone"
author: Justin
date: 2026-11-15
branch: crew-tab-rebuild
cost: { minutes: 4, tokens: 47169 }
id: l-5d30a874
scope: repo
status: active
replaces: l-c2c2cb99
src: agent
touches:
  - app/main.py
  - app/modules/__init__.py
  - app/modules/crew/__init__.py
  - app/modules/crew/routes.py
  - app/modules/crew/static/crew.css
  - app/modules/crew/static/crew.js
  - app/modules/crew/static/crewadmin.js
  - app/modules/crew/static/sw.js
  - app/modules/crew/templates/admin.html
  - app/modules/crew/templates/crew.html
---

## Decided
Justin chose (2026-11-15, issue 240) to keep the crew page as the crew module (its own work for #226) and bring the intercom in as a tab, so core never names /crew. The crew module renders tabs that modules add with CrewTab (template, scripts, own permission). Justin chose to vendor livekit-client by downloading it once: 2.23.0, over two weeks old, in the intercom module's static folder with its licence, never from a CDN. The tab joins a room per channel through Relay's /intercom path (livekit-client appends /rtc), keeps the microphone track disabled except while a talk lease is held, and publishes a clone of one captured track per armed channel. A module lists the pages that may use the microphone in microphone_pages; the intercom lists /crew, so only while it is on is /crew sent microphone=(self).

## Tried and ruled out
Core-owned crew page with a hard-coded /crew microphone exception: it duplicated the crew module and put a crew path in core. Putting the library in core's vendor folder: it is the intercom's. Relying on the server's mute alone: the page also disables its own track.

## Watch out
Relay's global Permissions-Policy had microphone=() and silently blocked the intercom. Tried by Justin on real phones and it worked; the service worker could not be tried in the Browser pane (no worker support, self-signed certificate). The 150 ms mouth-to-ear target is not measured. The Remote chip waits for #277.

In the code: app/modules/intercom/static/crew.js, templates/crew.html, app/modules/crew (tabs in routes.py, crew.html, crew.js), app/modules/__init__.py (CrewTab, microphone_pages), app/main.py (permissions_policy_for)
