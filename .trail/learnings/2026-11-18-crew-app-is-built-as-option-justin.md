---
title: "Crew app is built as option B: home screen and bottom tab bar on the phone, a live board by team on the operator page, full-screen page, join and offline"
author: Justin
date: 2026-11-18
branch: network-closets-redesign
cost: { minutes: 15, tokens: 213250 }
id: l-f16f725a
scope: repo
status: active
replaces: l-3c9cff50
src: agent
touches:
  - app/modules/crew/templates/crew.html
  - app/modules/crew/static/crew.css
  - app/modules/crew/static/crew.js
  - app/modules/crew/paging.py
  - app/modules/crew/routes.py
  - app/modules/crew/templates/page.html
  - app/modules/crew/templates/join.html
  - app/modules/crew/templates/offline.html
  - app/modules/crew/templates/admin.html
  - app/modules/crew/static/crewadmin.css
---

## Decided
Justin, 2026-11-18: build mockup B. Phone: Home (what is on now, your day, the plan), a bottom bar of the tabs modules add (Chat, Checklist, Files, Talk), and a sheet from your initials holding pages on/off, phones, other tools, Activity and sign out. Operator page: tabs Live board, Call times, Checklists, People, Setup; Live board is a column per team (or per call time) with paging and activity on the right. Page, join and offline each fill the phone.
Native iOS/Android apps with an Apple Watch app (pages, tally, push-to-talk) are to follow this design: issue 500.

## Tried and ruled out
An unread count on the Chat tab: the chat stream is closed while its tab is hidden (that is what lets Relay send a quiet notification instead), so there is nothing to count. crew.js listens for a crew:count event if a module ever sends one.

## Watch out
- The offline page is served by the service worker and cannot ask Relay anything: it shows the position and times crew.js last saved in localStorage ("relay-crew-last").
- join.html and offline.html cannot load tokens.css; their colours are a handful of variables at the top, held under the style-contract baseline (7 and 5).
- The crew page's flex rows override [hidden], so crew.css ends with a rule keeping [hidden] hidden.
- To view it in the UI demo while the installed Relay runs, set crew.join_port in the demo config.json to a free port (8095 is taken).

In the code: app/modules/crew/templates/crew.html, page.html, join.html, offline.html, admin.html; static/crew.css, crew.js, crewadmin.css, crewadmin.js; /crew-join/mark.svg serves the logo to the join port.
