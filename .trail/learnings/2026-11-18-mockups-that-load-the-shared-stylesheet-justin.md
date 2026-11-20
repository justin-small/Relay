---
title: "Mockups that load the shared stylesheet must not name their own classes row, card or grow"
author: Justin
date: 2026-11-18
branch: network-closets-redesign
cost: { minutes: 8, tokens: 257603 }
id: l-c4c41247
scope: repo
status: active
src: agent
touches:
  - docs/mockups/crew/common.css
  - docs/mockups/crew/common.js
  - docs/mockups/crew/a-tabs.html
  - docs/mockups/crew/b-home.html
  - docs/mockups/crew/c-teams.html
  - docs/mockups/crew/index.html
  - app/static/components.css
---

## Decided
Crew app mockups (2026-11-18) were built on the real tokens and components stylesheets, like the Planning Center ones, with their own class names for list lines.

## Watch out
The shared components stylesheet already defines .row (flex-wrap, every child flex 1 1 200px), so a mockup's own ".row" list line breaks into stacked boxes. .card and .grow are shared too and are meant to be reused, not redefined. The mockup server caches CSS, so after editing a mockup stylesheet force a reload before judging a screenshot.

In the code: docs/mockups/crew/common.css uses .ln for list lines; app/static/components.css line 43 is the shared .row. Serve with the mockup-planning-center preview entry (repo root, port 8470) and open /docs/mockups/crew/index.html.
