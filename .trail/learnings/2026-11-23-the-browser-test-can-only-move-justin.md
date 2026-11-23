---
title: "The browser test can only move between System tabs by clicking the tab, not the System link, and a new System page can expose that"
author: Justin
date: 2026-11-23
branch: issue-629-core-transports
cost: { minutes: 16, tokens: 80781 }
id: l-3918750b
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - tests/test_browser.py
  - run.py
  - app/static/shell.js
---

## Decided
Justin, 2026-11-22, issue 629: tests/test_browser.py's nav_click clicks a System (or ProPresenter) tab on the bar that is already showing, and only clicks the sidebar entry first when it is not.

## Tried and ruled out
Blaming the new Connections page or its script: the same failure happened with connections.js removed, and in a scratch Playwright script on origin/main. A fixed longer click timeout: the click is lost because the tab bar is drawn again as the page shows, so the element Playwright resolved is gone and every retry says "not visible".

## Watch out
The test walks pages in alphabetical order. Until Connections, no two System pages were next to each other, so the System-to-System click had never run. A new System page whose name sorts next to another one (Credentials, Helpers, Network...) can trigger this again if the helper is changed back.
In the code: nav_click and TAB_TITLE in tests/test_browser.py; the bar is built in tabBar in app/static/shell.js.
