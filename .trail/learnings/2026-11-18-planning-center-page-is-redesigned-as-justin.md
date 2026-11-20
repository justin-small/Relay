---
title: "Planning Center page is redesigned as tabs: Service, Team and mics, Rundown, Signage, Connection, with a shared service picker"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 12, tokens: 174143 }
id: l-2b636b10
scope: repo
status: active
src: agent
touches:
  - docs/mockups/planning-center/common.css
  - app/modules/planning_center/templates/admin.html
  - app/static/tokens.css
  - app/modules/planning_center/rundown.py
  - app/modules/planning_center/routes.py
  - app/modules/planning_center/static/pcrundown.js
---

## Decided
Justin, 2026-11-17: of three Planning Center mockups (tabs, live console with drawer, status board) he chose the tabbed one. Changes he asked for:
- A service type / plan / service time picker above the tabs, shared by Service, Team and mics, and Rundown.
- "Service types Relay follows" moves off Connection to the Service tab, with a line saying what it does. Connection holds only the token fields.
- Team and mics is designed from what the code does (positions mapped to console mic labels, Sings flag, label, who holds which mic this service, Redraw), not from the old page's layout.
- "Screens" is renamed Rundown, explains /screen, and offers more options: view, order, people cards, team, time, link.
- Form fields get a label, a sensible width, a visible border and a hint line; the old full-width inputs looked broken.
In the code: mockups in docs/mockups/planning-center/ (a-tabs.html is the chosen one); real page is app/modules/planning_center/templates/admin.html with static/planningcenter.js and pcrundown.js.

## Tried and ruled out
B (live console plus settings drawer) and C (status board of outputs): not chosen.

## Watch out
Mockups must load app/static/tokens.css and components.css and be served over http from the repo root (preview entry mockup-planning-center, port 8470); opened as file:// they render unstyled.
