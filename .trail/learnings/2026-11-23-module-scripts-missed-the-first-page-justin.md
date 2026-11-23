---
title: "Module scripts missed the first page-config event when the state loaded fast, so the admin page waits for the parser before sending it"
author: Justin
date: 2026-11-23
branch: helper-releases-636
cost: { minutes: 23, tokens: 51830 }
id: l-90e5ac32
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - app/modules/intercom_remote/static/remote.js
  - app/static/admin.js
---

## Decided
Justin, 2026-11-23, issue 687 (flaky browser test on the Remote crew feed form): the admin page now waits for the HTML to finish parsing before it sends the `relay:config` event, so every module script has attached its listener first.

## Tried and ruled out
Blaming the Remote crew page, the channel list, permissions or a server error: the state response was fine, `can()` was true and nothing logged an error. The page just never made its first request, because the event had already fired.

## Watch out
Module scripts are plain scripts after admin.js. The boot code awaits `/api/admin/state` (about 40 ms), and on a fast response the event fires before they run. Every module that starts from `relay:config` was exposed, not only intercom_remote. It reproduced in 5 of 8 local runs of a loop that only opens the page, and 0 of 8 after the fix.

In the code: boot() in app/static/admin.js (the DOMContentLoaded wait before the dispatch); listeners in app/modules/*/static/*.js.
