---
title: "In Planning Center copy, 'follow' means only the service types Relay watches; use other words for choosing which service a page shows"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 1, tokens: 5351 }
id: l-39d7ab32
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/templates/admin.html
---

## Decided
Justin, 2026-11-17: the word "follow" in Planning Center screens already means one thing: the service types Relay reads plans from ("Service types Relay follows"). Do not reuse it for anything else, such as the service a page is showing. The service picker box is titled "Current service", the plan hint says "use it until midnight", and Service time Automatic "picks" the running service. Also: a box title must be true on every tab it appears on ("on this page" was wrong because the box is not on every tab).

## Watch out
Check new copy for words the module already uses with a fixed meaning (follow, plan, type, LIVE) before reusing them.
In the code: docs/mockups/planning-center/a-tabs.html; the real strings live in app/modules/planning_center/templates/admin.html.
