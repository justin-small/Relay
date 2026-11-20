---
title: "The admin sidebar has one entry per module, with transcription and translation pages under one entry and settings under System"
author: Justin
date: 2026-11-18
branch: sidebar-by-module
cost: { minutes: 11, tokens: 248030 }
id: l-72669276
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - app/main.py
  - app/modules/__init__.py
  - app/modules/blackmagic/static/blackmagic.js
  - app/modules/crew/routes.py
  - app/modules/crew/static/chat.js
  - app/modules/crew/static/checklists.js
  - app/modules/crew/static/crew.css
  - app/modules/crew/static/crewadmin.js
  - app/modules/crew/static/resources.js
---

## Decided
Justin, 2026-11-17, from a mockup he approved: every module that is on is one sidebar entry. A module with one admin page is a plain link to it; one with several is an entry named by the module that opens to its pages. Session health, Audio input, Target languages, Blocked words, Viewer links, Broadcast overlay and Recordings sit under one "Transcription & translation" entry. Users, Credentials, Network, Network map, Appearance, Audit log and Modules sit under "System". Panels, Alerts, Boards and Schedules stay single links under "Output". Only one entry is open at a time: the one holding the current page.

Module entries are grouped under headings by what they do in a service (Show, Crew, Screens, Sound, Building), set by each module's manifest. Each module entry has a coloured dot for its health, and Transcription & translation has one for the stream.

## Tried and ruled out
Keeping AdminPage.group (Live/Output/Automation/Settings): it no longer means anything once a module is one entry, so it was removed rather than left as a dead field.

## Watch out
Health detail is not sent in the status push (it goes to every admin page); only the state is, cached for 5 s. The detail is in the dot's tooltip only when the page is rendered for someone who can see that module's pages.
A page under a closed entry is hidden, so a browser test must open the entry before clicking the link (nav_click in tests/test_browser.py).

In the code: Manifest.section and NAV_SECTIONS, admin_nav() and health_states() in app/modules/__init__.py; the nav block in app/templates/admin.html; groupOf and the accordion in app/static/shell.js; paintNavHealth in app/static/admin.js; .ndot in app/static/admin.css. ProPresenter's pages are titled Control / Slide view and lobby / Settings and Intercom's first page Channels, so they read well under their module.
